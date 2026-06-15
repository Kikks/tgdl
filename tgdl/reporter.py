from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

if TYPE_CHECKING:
    from tgdl.downloader import DownloadStats


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@dataclass
class FileHandle:
    """Opaque per-file handle returned by a reporter's ``add_file``."""

    key: int
    label: str
    total: int | None
    done: int = 0
    extra: object = None  # reporter-specific payload (e.g. a Rich TaskID)


@runtime_checkable
class ProgressReporter(Protocol):
    """
    Display-agnostic sink for download progress.

    Two implementations exist: :class:`RichReporter` (interactive TTY, the
    historical behaviour) and :class:`JsonReporter` (writes machine-readable
    ``status.json`` + ``progress.ndjson`` for the background-job layer).

    The download engine only ever talks to this interface, so the same core
    powers both ``tgdl download`` and ``tgdl _job-run``.
    """

    def begin(
        self,
        *,
        total_files: int | None,
        total_bytes: int,
        channel: str,
        channel_name: str,
    ) -> None: ...

    def mark(self, phase: str) -> None: ...

    def add_file(self, label: str, total: int | None, start: int = 0) -> FileHandle: ...

    def advance_file(self, handle: FileHandle, n: int) -> None: ...

    def finish_file(self, handle: FileHandle, status: str = "complete") -> None: ...

    def advance_overall(self, n: int = 1) -> None: ...

    def note(self, text: str) -> None: ...

    def tick(self, stats: DownloadStats) -> None: ...

    def end(self, stats: DownloadStats, *, phase: str = "done") -> None: ...


# ── Rich (interactive TTY) ────────────────────────────────────────────────────


class RichReporter:
    """Renders progress to the terminal — preserves tgdl's original UX exactly."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
        )
        self._overall = None
        self._key = 0
        self._finished: set[int] = set()

    def mark(self, phase: str) -> None:  # no-op for TTY
        pass

    def begin(self, *, total_files, total_bytes, channel, channel_name) -> None:
        self._progress.start()
        self._overall = self._progress.add_task("[cyan]Overall", total=None)

    def add_file(self, label: str, total: int | None, start: int = 0) -> FileHandle:
        self._key += 1
        task_id = self._progress.add_task(f"[green]{label[:40]}", total=total, completed=start)
        return FileHandle(key=self._key, label=label, total=total, done=start, extra=task_id)

    def advance_file(self, handle: FileHandle, n: int) -> None:
        handle.done += n
        self._progress.update(handle.extra, advance=n)

    def finish_file(self, handle: FileHandle, status: str = "complete") -> None:
        if handle.key in self._finished:
            return
        self._finished.add(handle.key)
        self._progress.remove_task(handle.extra)

    def advance_overall(self, n: int = 1) -> None:
        if self._overall is not None:
            self._progress.advance(self._overall, n)

    def note(self, text: str) -> None:
        self._progress.print(text)

    def tick(self, stats: DownloadStats) -> None:  # noqa: D401 - nothing to flush for TTY
        pass

    def end(self, stats: DownloadStats, *, phase: str = "done") -> None:
        self._progress.stop()


# ── JSON (background job) ──────────────────────────────────────────────────────


class JsonReporter:
    """
    Writes a job's live state to ``status.json`` (atomically) and appends
    per-file events to ``progress.ndjson``.  Read by the Raycast menu bar and
    ``tgdl job status``.

    ``status.json`` is the public contract — see ``docs/json-api.md``.
    """

    FLUSH_INTERVAL = 1.0  # seconds between status.json rewrites

    def __init__(self, job_dir: Path, *, dry_run: bool = False, output_path: str = ""):
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.status_path = self.job_dir / "status.json"
        self.ndjson_path = self.job_dir / "progress.ndjson"
        self.dry_run = dry_run
        self.output_path = output_path

        self.channel = ""
        self.channel_name = ""
        self.total_files: int | None = None
        self.total_bytes = 0
        self.bytes_done = 0
        self.started_at = time.time()
        self._last_flush = 0.0
        self._last_bytes = 0
        self._last_time = self.started_at
        self._speed_bps = 0.0
        self._key = 0
        self._active: dict[int, FileHandle] = {}
        self._finished: set[int] = set()
        self._stats: DownloadStats | None = None

    # -- lifecycle -------------------------------------------------------------

    def mark(self, phase: str) -> None:
        """Write a status snapshot in ``phase`` (e.g. 'estimating') with our PID."""
        self._write(phase=phase, force=True)

    def begin(self, *, total_files, total_bytes, channel, channel_name) -> None:
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.channel = channel
        self.channel_name = channel_name
        self.started_at = time.time()
        self._last_time = self.started_at
        self._write(phase="downloading", force=True)

    def add_file(self, label: str, total: int | None, start: int = 0) -> FileHandle:
        self._key += 1
        h = FileHandle(key=self._key, label=label, total=total, done=start)
        self._active[h.key] = h
        self._event("file_start", name=label, total=total)
        return h

    def advance_file(self, handle: FileHandle, n: int) -> None:
        handle.done += n
        self.bytes_done += n
        self._maybe_flush()

    def finish_file(self, handle: FileHandle, status: str = "complete") -> None:
        if handle.key in self._finished:
            return
        self._finished.add(handle.key)
        self._active.pop(handle.key, None)
        self._event("file_done", name=handle.label, status=status)
        self._write()

    def advance_overall(self, n: int = 1) -> None:
        self._maybe_flush()

    def note(self, text: str) -> None:
        self._event("note", text=text)
        with open(self.job_dir / "log.txt", "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")

    def tick(self, stats: DownloadStats) -> None:
        self._stats = stats
        self._maybe_flush()

    def end(self, stats: DownloadStats, *, phase: str = "done") -> None:
        self._stats = stats
        self._write(phase=phase, force=True)

    # -- internals -------------------------------------------------------------

    def _recompute_speed(self, now: float) -> None:
        dt = now - self._last_time
        if dt >= 0.5:
            self._speed_bps = (self.bytes_done - self._last_bytes) / dt
            self._last_bytes = self.bytes_done
            self._last_time = now

    def _maybe_flush(self) -> None:
        now = time.time()
        if now - self._last_flush >= self.FLUSH_INTERVAL:
            self._write()

    def _current_file(self) -> dict | None:
        if not self._active:
            return None
        # Show the most recently started active file.
        h = self._active[max(self._active)]
        pct = round(100.0 * h.done / h.total, 1) if h.total else None
        return {"name": h.label, "pct": pct, "active_count": len(self._active)}

    def _write(self, *, phase: str = "downloading", force: bool = False) -> None:
        now = time.time()
        self._recompute_speed(now)
        self._last_flush = now

        s = self._stats
        completed = s.completed if s else 0
        skipped = s.skipped if s else 0
        failed = s.failed if s else 0

        remaining = max(self.total_bytes - self.bytes_done, 0)
        eta = int(remaining / self._speed_bps) if self._speed_bps > 1 else None

        status = {
            "job_id": self.job_dir.name,
            "pid": os.getpid(),
            "phase": phase,
            "dry_run": self.dry_run,
            "channel": self.channel,
            "channel_name": self.channel_name,
            "output_path": self.output_path,
            "started_at": _iso(self.started_at),
            "updated_at": _iso(now),
            "totals": {"files": self.total_files, "bytes": self.total_bytes},
            "progress": {
                "completed": completed,
                "skipped": skipped,
                "failed": failed,
                "bytes_done": self.bytes_done,
            },
            "current_file": self._current_file(),
            "speed_bps": round(self._speed_bps, 1),
            "eta_seconds": eta,
            "error": (s.errors[-1] if s and s.errors and phase == "failed" else None),
        }
        _atomic_write_json(self.status_path, status)

    def _event(self, kind: str, **fields) -> None:
        rec = {"t": _iso(time.time()), "event": kind, **fields}
        with open(self.ndjson_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _iso(epoch: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)
