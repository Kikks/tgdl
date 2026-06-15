"""
Non-interactive download core.

``run_headless`` is the wizard-free, confirm-free counterpart of
``cli._download_flow``.  It accepts a fully-formed :class:`DownloadConfig` and a
:class:`ProgressReporter`, so the same engine powers both the background-job
worker (``tgdl _job-run``) and any future scripted entry point.
"""

from __future__ import annotations

from dataclasses import dataclass

from telethon import TelegramClient

from tgdl.config import STATE_DB, DownloadConfig
from tgdl.downloader import DownloadStats, estimate, run_download
from tgdl.reporter import ProgressReporter
from tgdl.state import StateDB


class HeadlessError(RuntimeError):
    """Raised when a headless run cannot proceed (auth, channel, disk)."""


@dataclass
class HeadlessResult:
    stats: DownloadStats
    channel_name: str
    channel_id: str


async def run_headless(
    config: DownloadConfig,
    reporter: ProgressReporter,
    *,
    dry_run: bool = False,
    client: TelegramClient | None = None,
) -> HeadlessResult:
    """
    Resolve the channel, estimate, and download — emitting all progress through
    ``reporter``.  Raises :class:`HeadlessError` on unrecoverable setup failures.
    """
    own_client = client is None
    if own_client:
        from tgdl.auth import load_credentials, make_client

        creds = load_credentials()
        if not creds:
            raise HeadlessError("Not authenticated. Run `tgdl init` first.")
        client = make_client(*creds)

    async with client:
        if not await client.is_user_authorized():
            raise HeadlessError("Telegram session is not authorized. Run `tgdl init`.")

        try:
            entity = await client.get_entity(config.channel)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller/job status
            raise HeadlessError(f"Could not resolve channel '{config.channel}': {exc}") from exc

        channel_name = (
            getattr(entity, "title", None) or getattr(entity, "username", None) or config.channel
        )
        channel_id = str(entity.id)

        with StateDB(STATE_DB) as state:
            reporter.note(f"Scanning '{channel_name}' for matching media…")
            est = await estimate(client, entity, config, state, channel_id)

            if not dry_run and est.free_disk_bytes < est.total_bytes:
                raise HeadlessError(
                    f"Not enough disk space: need {est.total_bytes} bytes, "
                    f"have {est.free_disk_bytes}."
                )

            stats = await run_download(
                client,
                entity,
                config,
                state,
                channel_id,
                channel_name,
                dry_run=dry_run,
                reporter=reporter,
                totals=(est.total_files, est.total_bytes),
            )

    return HeadlessResult(stats=stats, channel_name=channel_name, channel_id=channel_id)
