"""
The detached job worker — what ``tgdl _job-run <id>`` actually executes.

Acquires a cross-process lock so only one download touches the Telegram session
at a time (others sit in the ``queued`` phase), then runs the headless download
with a :class:`JsonReporter` writing live state to the job directory.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import time

from tgdl.config import JOBS_DIR, JOBS_LOCK, DownloadConfig
from tgdl.headless import HeadlessError, run_headless
from tgdl.jobs import job_dir
from tgdl.reporter import JsonReporter

# How long to wait between lock-acquire attempts while queued.
QUEUE_POLL = 2.0


def run_job(job_id: str, *, dry_run: bool = False) -> int:
    """Entry point for ``_job-run``. Returns a process exit code."""
    d = job_dir(job_id)
    config = DownloadConfig.model_validate(
        json.loads((d / "config.json").read_text(encoding="utf-8"))
    )

    reporter = JsonReporter(d, dry_run=dry_run, output_path=str(config.output_path.expanduser()))
    reporter.channel = config.channel
    reporter.channel_name = config.channel

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = open(JOBS_LOCK, "w")  # noqa: SIM115 - held for the process lifetime

    # Queue behind any active download.
    reporter.mark("queued")
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            reporter.mark("queued")  # refresh updated_at so we look alive
            time.sleep(QUEUE_POLL)

    try:
        reporter.mark("estimating")
        result = asyncio.run(run_headless(config, reporter, dry_run=dry_run))
        reporter.end(result.stats, phase="done")
        return 0
    except HeadlessError as exc:
        _fail(reporter, str(exc))
        return 1
    except KeyboardInterrupt:
        _fail(reporter, "Interrupted.", phase="cancelled")
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort: never leave a zombie 'downloading'
        _fail(reporter, f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _fail(reporter: JsonReporter, message: str, *, phase: str = "failed") -> None:
    reporter.note(message)
    if reporter._stats is None:  # no stats yet — fabricate an empty one
        from tgdl.downloader import DownloadStats

        reporter._stats = DownloadStats(errors=[message])
    elif message not in reporter._stats.errors:
        reporter._stats.errors.append(message)
    reporter.end(reporter._stats, phase=phase)
