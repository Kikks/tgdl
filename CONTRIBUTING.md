# Contributing to tgdl

Thanks for your interest! This is a small, focused project — issues and PRs are
welcome.

## Development setup

```bash
git clone https://github.com/Kikks/tgdl.git
cd tgdl
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install --install-hooks          # lint/format on commit
pre-commit install --hook-type commit-msg   # conventional-commit message check
```

## Running checks

```bash
pytest                    # tests
ruff check .              # lint
ruff format .             # format
pre-commit run --all-files  # everything the commit hook runs
```

CI runs lint, format-check, and tests on Python 3.11–3.13; please make sure they
pass locally first. With the hooks installed, ruff lint/format run automatically
on staged files at commit time, and your commit message is checked against the
[Conventional Commits](https://www.conventionalcommits.org/) spec (e.g.
`feat: add sender subfolder option`, `fix: handle flood-wait retry`).

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

This repo uses [Conventional Commits](https://www.conventionalcommits.org/),
enforced by the `commit-msg` hook (commitizen). Format:

```
<type>[optional scope]: <description>
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`. For
example: `feat(jobs): add retry on flood-wait`. Reference issues in the body
where relevant.
