# Contributing to tgdl

Thanks for your interest! This is a small, focused project — issues and PRs are
welcome.

## Development setup

```bash
git clone https://github.com/Kikks/tgdl.git
cd tgdl
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running checks

```bash
pytest            # tests
ruff check .      # lint
ruff format .     # format
```

CI runs all three on Python 3.11–3.13; please make sure they pass locally first.

## Architecture in one minute

`DownloadConfig` (`tgdl/config.py`) is the central data model — it flows
**wizard → config → downloader → state**. New options usually start there.

| Module | Role |
|---|---|
| `config.py` | `DownloadConfig` model + paths/constants |
| `auth.py` | Credentials + Telethon session |
| `filters.py` | Media-type / size / caption / sender filtering |
| `organizer.py` | Filename templates + subfolders |
| `downloader.py` | Async download engine (resume, dedup, bandwidth) |
| `reporter.py` | `ProgressReporter` — `RichReporter` (TTY) / `JsonReporter` (jobs) |
| `headless.py` | Wizard-free download core |
| `jobs.py` / `worker.py` | Background-job layer + detached worker |
| `state.py` | SQLite resume/dedup/bandwidth store |
| `tui/wizard.py` | Interactive configuration wizard |

The download engine only talks to a `ProgressReporter`, so the same core powers
both the interactive CLI and background jobs. If you change download behaviour,
keep both reporters working.

## Guidelines

- Match the surrounding style; keep comments at the same density.
- Add or update tests for logic changes (`tests/`).
- The `~/.tgdl/` contract (`status.json` and the `--json` outputs) is public —
  document changes in [`docs/json-api.md`](docs/json-api.md) and the changelog.
- Never commit secrets (`*.session`, `credentials.yaml`).

## Commit messages

Short imperative subject lines (e.g. "Add sender-subfolder option"). Reference
issues where relevant.
