"""
Background-job layer.

A *job* is one detached download running headlessly, writing its live state to
``~/.tgdl/jobs/<job_id>/status.json`` so any other process (the Raycast menu
bar, a second window, you in a terminal) can observe it without holding the
download in memory.

On-disk layout per job::

    ~/.tgdl/jobs/<job_id>/
    ├── config.json      # the DownloadConfig this job ran with
    ├── status.json      # live state — the public contract (docs/json-api.md)
    ├── progress.ndjson  # append-only per-file event stream
    └── log.txt          # human-readable log

Only one download touches the Telegram session at a time; additional jobs wait
in the ``queued`` phase behind a cross-process lock.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from tgdl.config import JOBS_DIR, DownloadConfig

# A job whose status.json hasn't been touched in this long, with a dead PID, is
# considered crashed rather than running.
STALE_SECONDS = 30
ACTIVE_PHASES = {"queued", "estimating", "downloading", "paused"}


# ── job creation ──────────────────────────────────────────────────────────────


def new_job_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def create_job(config: DownloadConfig, *, dry_run: bool = False) -> str:
    """Persist a job's config + an initial queued status. Returns the job_id."""
    job_id = new_job_id()
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)

    (d / "config.json").write_text(
        json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_status(
        job_id,
        {
            "job_id": job_id,
            "pid": None,
            "phase": "queued",
            "dry_run": dry_run,
            "channel": config.channel,
            "channel_name": config.channel,
            "started_at": None,
            "updated_at": _now_iso(),
            "totals": {"files": None, "bytes": 0},
            "progress": {"completed": 0, "skipped": 0, "failed": 0, "bytes_done": 0},
            "current_file": None,
            "speed_bps": 0,
            "eta_seconds": None,
            "error": None,
        },
    )
    return job_id


def start_detached(job_id: str, *, dry_run: bool = False) -> int:
    """
    Spawn ``tgdl _job-run <job_id>`` in a new session so it survives the parent
    (e.g. the Raycast popup) closing. Returns the child PID.
    """
    d = job_dir(job_id)
    log = open(d / "log.txt", "a", encoding="utf-8")  # noqa: SIM115 - handed to child
    cmd = [sys.executable, "-m", "tgdl", "_job-run", job_id]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(Path.home()),
    )
    return proc.pid


# ── reading / listing ─────────────────────────────────────────────────────────


def read_status(job_id: str) -> dict | None:
    path = job_dir(job_id) / "status.json"
    if not path.exists():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return _reconcile(status)


def list_jobs() -> list[dict]:
    """All jobs, newest first, with stale 'downloading' states reconciled."""
    if not JOBS_DIR.exists():
        return []
    out = []
    for d in sorted(JOBS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        status = read_status(d.name)
        if status:
            out.append(status)
    return out


def _reconcile(status: dict) -> dict:
    """Mark a job failed if it claims to be active but its process is gone/stale."""
    phase = status.get("phase")
    if phase not in ACTIVE_PHASES:
        return status

    pid = status.get("pid")
    updated = _parse_iso(status.get("updated_at"))
    age = time.time() - updated if updated else None

    alive = _pid_alive(pid) if pid else False
    if not alive and (age is None or age > STALE_SECONDS):
        status = dict(status)
        status["phase"] = "failed"
        status["error"] = status.get("error") or "Process exited unexpectedly."
    return status


# ── control ───────────────────────────────────────────────────────────────────


def cancel_job(job_id: str) -> bool:
    """Terminate a running job. Returns True if a signal was delivered."""
    status = read_status(job_id)
    if not status:
        return False
    pid = status.get("pid")
    if pid and _pid_alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    cur = json.loads((job_dir(job_id) / "status.json").read_text(encoding="utf-8"))
    cur["phase"] = "cancelled"
    cur["updated_at"] = _now_iso()
    _write_status(job_id, cur)
    return True


def clean_jobs(*, only_done: bool = True) -> int:
    """Remove finished job directories. Returns the count removed."""
    import shutil

    removed = 0
    for status in list_jobs():
        if only_done and status.get("phase") in ACTIVE_PHASES:
            continue
        shutil.rmtree(job_dir(status["job_id"]), ignore_errors=True)
        removed += 1
    return removed


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_status(job_id: str, data: dict) -> None:
    path = job_dir(job_id) / "status.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (ValueError, OverflowError):
        return None
