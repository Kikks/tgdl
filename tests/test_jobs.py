"""Job lifecycle bookkeeping — create, read, reconcile-when-dead, clean.

These exercise the on-disk job layer without spawning a real download by
pointing JOBS_DIR at a tmp path.
"""

import os

import tgdl.jobs as jobs
from tgdl.config import DownloadConfig


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path / "jobs")


def test_create_job_writes_config_and_queued_status(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    jid = jobs.create_job(DownloadConfig(channel="@x", concurrency=2))
    status = jobs.read_status(jid)
    assert status["phase"] == "queued"
    assert status["channel"] == "@x"
    cfg_file = jobs.job_dir(jid) / "config.json"
    assert cfg_file.exists()


def test_dead_active_job_is_reconciled_to_failed(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    jid = jobs.create_job(DownloadConfig(channel="@x"))
    # Forge an active status owned by a PID that cannot exist.
    jobs._write_status(
        jid,
        {
            "job_id": jid,
            "pid": 2**30,
            "phase": "downloading",
            "updated_at": "2000-01-01T00:00:00Z",
            "progress": {},
            "totals": {},
        },
    )
    assert jobs.read_status(jid)["phase"] == "failed"


def test_clean_removes_finished_only(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    done = jobs.create_job(DownloadConfig(channel="@a"))
    jobs._write_status(done, {"job_id": done, "phase": "done", "updated_at": "x"})
    active = jobs.create_job(DownloadConfig(channel="@b"))
    # Owned by this live process with a fresh timestamp → looks genuinely active.
    jobs._write_status(
        active,
        {
            "job_id": active,
            "pid": os.getpid(),
            "phase": "downloading",
            "updated_at": jobs._now_iso(),
        },
    )
    removed = jobs.clean_jobs(only_done=True)
    assert removed == 1
    assert jobs.read_status(done) is None
    assert jobs.read_status(active) is not None


def test_create_job_status_includes_output_path(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    jid = jobs.create_job(DownloadConfig(channel="@x", output_path=tmp_path / "out"))
    status = jobs.read_status(jid)
    assert status["output_path"].endswith("out")


def test_retry_job_clones_config_to_new_job(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "start_detached", lambda jid, dry_run=False: 4242)
    jid = jobs.create_job(DownloadConfig(channel="@x"))
    result = jobs.retry_job(jid)
    assert result is not None
    new_id, pid = result
    assert new_id != jid
    assert pid == 4242
    assert jobs.read_status(new_id)["channel"] == "@x"


def test_retry_unknown_job_returns_none(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert jobs.retry_job("does-not-exist") is None
