from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import xxhash
from rich.console import Console
from rich.table import Table
from telethon import TelegramClient
from telethon.errors import FloodWaitError

from tgdl.config import DownloadConfig, ResumeMode
from tgdl.filters import message_media_type, passes_filters
from tgdl.organizer import resolve_path, unique_dest
from tgdl.reporter import ProgressReporter, RichReporter
from tgdl.state import DownloadStatus, StateDB

console = Console()


@dataclass
class DownloadStats:
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_downloaded: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class EstimateResult:
    total_files: int
    total_bytes: int
    already_have_bytes: int
    already_have_files: int
    free_disk_bytes: int


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


async def estimate(
    client: TelegramClient,
    entity,
    config: DownloadConfig,
    state: StateDB,
    channel_id: str,
) -> EstimateResult:
    """Scan matching messages and return size/disk estimates without downloading."""
    total_files = 0
    total_bytes = 0
    already_files = 0
    already_bytes = 0

    offset_date = _offset_date(config)

    async for msg in client.iter_messages(
        entity, offset_date=offset_date, reverse=bool(offset_date)
    ):
        if not _in_date_range(msg, config):
            continue
        ok, _ = passes_filters(msg, config)
        if not ok:
            continue

        file = getattr(msg, "file", None)
        size = file.size if file else 0

        if _already_downloaded(msg, config, state, channel_id):
            already_files += 1
            already_bytes += size
        else:
            total_files += 1
            total_bytes += size

    output = config.output_path.expanduser()
    output.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(str(output)).free

    return EstimateResult(
        total_files=total_files,
        total_bytes=total_bytes,
        already_have_bytes=already_bytes,
        already_have_files=already_files,
        free_disk_bytes=free,
    )


def print_estimate(est: EstimateResult, config: DownloadConfig):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="bold")

    table.add_row("Files matched:", str(est.total_files + est.already_have_files))
    table.add_row(
        "Already downloaded:",
        f"{est.already_have_files} files ({_fmt_bytes(est.already_have_bytes)})",
    )
    table.add_row("To download:", f"{est.total_files} files ({_fmt_bytes(est.total_bytes)})")

    free_style = "red" if est.free_disk_bytes < est.total_bytes else "green"
    table.add_row(
        "Free disk space:",
        f"[{free_style}]{_fmt_bytes(est.free_disk_bytes)}[/{free_style}]",
    )
    table.add_row("Output path:", str(config.output_path.expanduser()))

    console.print(table)

    if est.free_disk_bytes < est.total_bytes:
        console.print(
            f"\n[red bold]✗ Not enough disk space.[/red bold] "
            f"Need {_fmt_bytes(est.total_bytes)}, have {_fmt_bytes(est.free_disk_bytes)}.\n"
            "Adjust your filters, change the output path, or free up space."
        )
        return False
    return True


async def run_download(
    client: TelegramClient,
    entity,
    config: DownloadConfig,
    state: StateDB,
    channel_id: str,
    channel_name: str,
    dry_run: bool = False,
    reporter: ProgressReporter | None = None,
    totals: tuple[int, int] | None = None,
) -> DownloadStats:
    stats = DownloadStats()
    sem = asyncio.Semaphore(config.concurrency)
    offset_date = _offset_date(config)

    if reporter is None:
        reporter = RichReporter(console)

    total_files, total_bytes = totals if totals else (None, 0)
    reporter.begin(
        total_files=total_files,
        total_bytes=total_bytes,
        channel=config.channel,
        channel_name=channel_name,
    )

    try:
        tasks: list[asyncio.Task] = []

        async for msg in client.iter_messages(
            entity, offset_date=offset_date, reverse=bool(offset_date)
        ):
            if not _in_date_range(msg, config):
                continue
            ok, reason = passes_filters(msg, config)
            if not ok:
                continue

            if _already_downloaded(msg, config, state, channel_id):
                stats.skipped += 1
                reporter.advance_overall()
                reporter.tick(stats)
                continue

            task = asyncio.create_task(
                _download_one(
                    client,
                    msg,
                    config,
                    state,
                    channel_id,
                    channel_name,
                    reporter,
                    sem,
                    stats,
                    dry_run,
                )
            )
            tasks.append(task)

            # Keep queue bounded so we don't hold all messages in memory
            if len(tasks) >= config.concurrency * 4:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                tasks = list(pending)
                reporter.advance_overall(len(done))
                reporter.tick(stats)

        if tasks:
            await asyncio.gather(*tasks)
            reporter.advance_overall(len(tasks))

        reporter.end(stats, phase="done")
    except Exception:
        reporter.end(stats, phase="failed")
        raise

    return stats


async def _download_one(
    client: TelegramClient,
    msg,
    config: DownloadConfig,
    state: StateDB,
    channel_id: str,
    channel_name: str,
    reporter: ProgressReporter,
    sem: asyncio.Semaphore,
    stats: DownloadStats,
    dry_run: bool,
):
    async with sem:
        file = getattr(msg, "file", None)
        expected_size = file.size if file else None
        unique_id = _media_unique_id(msg)

        if dry_run:
            name = file.name if file and file.name else f"msg_{msg.id}"
            reporter.note(
                f"  [dim][DRY RUN][/dim] would download: [cyan]{name}[/cyan]"
                + (f" ({_fmt_bytes(expected_size)})" if expected_size else "")
            )
            stats.skipped += 1
            return

        # Disk space check before each file
        output = config.output_path.expanduser()
        output.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(output)).free
        threshold = config.disk_space_threshold_mb * 1024 * 1024
        if free < threshold:
            reporter.note(
                f"\n[red bold]⚠ Low disk space ({_fmt_bytes(free)} remaining).[/red bold] "
                "Download paused. Free up space and re-run to resume."
            )
            state.upsert(channel_id, msg.id, DownloadStatus.PARTIAL)
            stats.failed += 1
            return

        dest = resolve_path(msg, config, channel_name)
        if config.resume_mode == ResumeMode.OVERWRITE:
            dest = dest  # always overwrite
        elif config.resume_mode == ResumeMode.SKIP and dest.exists():
            stats.skipped += 1
            return
        else:
            dest = unique_dest(dest) if dest.exists() else dest

        part = Path(str(dest) + ".part")

        # Resume from partial if applicable
        start_offset = 0
        if config.resume_mode == ResumeMode.SMART and part.exists():
            start_offset = part.stat().st_size
            state.upsert(channel_id, msg.id, DownloadStatus.PARTIAL, download_path=str(dest))

        media_type = message_media_type(msg)
        label = (file.name if file and file.name else f"msg_{msg.id}") or f"msg_{msg.id}"
        handle = reporter.add_file(label, total=expected_size, start=start_offset)

        bytes_written = start_offset
        h = xxhash.xxh64()

        try:
            mode = "ab" if start_offset > 0 else "wb"
            part.parent.mkdir(parents=True, exist_ok=True)

            async with client.iter_download(msg.media, offset=start_offset) as downloader:
                with open(part, mode) as f:
                    async for chunk in downloader:
                        f.write(chunk)
                        bytes_written += len(chunk)
                        h.update(chunk)
                        reporter.advance_file(handle, len(chunk))

            # Atomic rename
            part.rename(dest)
            file_hash = h.hexdigest()

            if config.deduplicate:
                existing_path = state.has_hash(file_hash)
                if existing_path and Path(existing_path) != dest:
                    dest.unlink()
                    stats.skipped += 1
                    state.upsert(
                        channel_id,
                        msg.id,
                        DownloadStatus.SKIPPED,
                        file_hash=file_hash,
                        file_unique_id=unique_id,
                    )
                    reporter.finish_file(handle, status="skipped")
                    return

            state.upsert(
                channel_id,
                msg.id,
                DownloadStatus.COMPLETE,
                file_unique_id=unique_id,
                file_hash=file_hash,
                download_path=str(dest),
                file_size=bytes_written,
            )
            state.log_bandwidth(channel_id, bytes_written - start_offset)
            stats.completed += 1
            stats.bytes_downloaded += bytes_written - start_offset

            if config.json_sidecars:
                _write_sidecar(dest, msg, media_type, channel_name)

        except FloodWaitError as exc:
            wait = exc.seconds
            reporter.note(f"[yellow]Telegram flood wait: {wait}s…[/yellow]")
            await asyncio.sleep(wait)
            # Re-queue not implemented; mark partial so next run resumes
            state.upsert(channel_id, msg.id, DownloadStatus.PARTIAL, download_path=str(dest))
            stats.failed += 1
        except Exception as exc:
            state.upsert(channel_id, msg.id, DownloadStatus.FAILED, download_path=str(dest))
            stats.failed += 1
            stats.errors.append(f"msg {msg.id}: {exc}")
        finally:
            reporter.finish_file(handle)
            reporter.tick(stats)


def _write_sidecar(dest: Path, msg, media_type, channel_name: str):
    sidecar = dest.with_suffix(".json")
    sender = getattr(msg, "sender", None)
    data = {
        "message_id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "channel": channel_name,
        "media_type": media_type.value if media_type else None,
        "caption": msg.text or msg.message or None,
        "sender": {
            "id": getattr(sender, "id", None),
            "username": getattr(sender, "username", None),
            "first_name": getattr(sender, "first_name", None),
            "last_name": getattr(sender, "last_name", None),
        }
        if sender
        else None,
    }
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _offset_date(config: DownloadConfig):
    """Return the earliest date for Telethon's iter_messages offset_date, or None."""
    from datetime import timedelta

    if config.date_range_type == "last_n_days" and config.last_n_days:
        return datetime.now(UTC) - timedelta(days=config.last_n_days)
    if config.date_range_type == "custom" and config.date_from:
        return config.date_from
    return None


def _in_date_range(msg, config: DownloadConfig) -> bool:
    if config.date_range_type == "all":
        return True
    msg_date = msg.date
    if msg_date and msg_date.tzinfo is None:
        msg_date = msg_date.replace(tzinfo=UTC)

    if config.date_range_type == "last_n_days" and config.last_n_days:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=config.last_n_days)
        return msg_date >= cutoff

    if config.date_range_type == "custom":
        if config.date_from:
            df = config.date_from
            if df.tzinfo is None:
                df = df.replace(tzinfo=UTC)
            if msg_date < df:
                return False
        if config.date_to:
            dt = config.date_to
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            if msg_date > dt:
                return False

    return True


def _media_unique_id(msg) -> str | None:
    """Extract a stable Telegram file ID from the underlying media object."""
    media = getattr(msg, "media", None)
    if not media:
        return None
    doc = getattr(media, "document", None)
    if doc:
        return str(doc.id)
    photo = getattr(media, "photo", None)
    if photo:
        return str(photo.id)
    return None


def _already_downloaded(msg, config: DownloadConfig, state: StateDB, channel_id: str) -> bool:
    if config.resume_mode == ResumeMode.OVERWRITE:
        return False

    if state.is_complete(channel_id, msg.id):
        return True

    # Deduplicate by Telegram's file ID (cheaper than hash — no download needed)
    if config.deduplicate:
        uid = _media_unique_id(msg)
        if uid and state.has_unique_id(uid):
            return True

    return False
