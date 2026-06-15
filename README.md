# tgdl — Telegram Media Downloader

[![CI](https://github.com/Kikks/tgdl/actions/workflows/ci.yml/badge.svg)](https://github.com/Kikks/tgdl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tgdl.svg)](https://pypi.org/project/tgdl/)
[![Python](https://img.shields.io/pypi/pyversions/tgdl.svg)](https://pypi.org/project/tgdl/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A fast, scriptable command-line tool for downloading photos, videos, documents,
and other media from any Telegram channel, group, or chat — with smart resume,
deduplication, filtering, named profiles, and a friendly setup wizard.

It also exposes a **background-job layer with a JSON API**, which powers the
[Raycast extension](https://github.com/Kikks/tgdl-raycast).

---

## Features

- **Everything, filtered** — pick media types, date ranges, size bounds, caption
  keywords (plain or `/regex/`), and specific senders.
- **Smart resume** — partial downloads continue where they left off; completed
  files are skipped on re-run.
- **Deduplication** — by Telegram file ID (no re-download) and by content hash.
- **Flexible organisation** — filename templates + optional subfolders by type,
  date, or sender, plus optional `.json` metadata sidecars.
- **Resource-aware** — disk-space estimate before downloading and a pause
  threshold while running.
- **Profiles** — save a configuration once, re-run it forever.
- **Background jobs** — detached downloads you can monitor from anywhere
  (see [`docs/json-api.md`](docs/json-api.md)).

## Install

```bash
pipx install tgdl      # recommended — isolated, on your PATH
# or
pip install tgdl
```

Requires Python 3.11+.

## Quickstart

```bash
tgdl init                       # one-time: API credentials + login
tgdl download                   # interactive wizard, then download
tgdl download @somechannel      # pre-fill the channel
tgdl download --dry-run         # preview without saving anything
```

You'll need a Telegram **API ID** and **API hash** from
<https://my.telegram.org> → *API Development Tools*. `tgdl init` walks you
through it.

## Command reference

| Command | What it does |
|---|---|
| `tgdl init` | First-time setup and authentication |
| `tgdl download [CHANNEL]` | Interactive wizard → download |
| `tgdl status` | Per-channel stats and bandwidth (`--json` available) |
| `tgdl reset CHANNEL` | Clear resume state for a channel |
| `tgdl dialogs` | List your recent chats (`--json`) |
| `tgdl profile list/run/show/delete` | Manage saved profiles |
| `tgdl job start/list/status/cancel/clean` | Background download jobs |
| `tgdl auth status` | Check authentication (`--json`) |

### Background jobs & the JSON API

Start a detached download and monitor it from any process:

```bash
echo '{"channel":"@somechannel","media_types":["photo","video"]}' > cfg.json
tgdl job start --config cfg.json        # → {"job_id": "...", "pid": ...}
tgdl job list --json
tgdl job status <job_id> --json
```

The full, stable contract is documented in [`docs/json-api.md`](docs/json-api.md).

## Where things live

| Path | Contents |
|---|---|
| `~/.tgdl/credentials.yaml` | Your API ID/hash (chmod 600) |
| `~/.tgdl/session.session` | Telegram session (treat as a password) |
| `~/.tgdl/state.db` | Resume / dedup / bandwidth state |
| `~/.tgdl/profiles/` | Saved profiles |
| `~/.tgdl/jobs/` | Background job state |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and PRs welcome.

## License

[MIT](LICENSE) © Olufemi Okikioluwa
