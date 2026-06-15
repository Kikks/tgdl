# tgdl JSON API

Every integration surface (the Raycast extension, scripts, your own tooling)
talks to tgdl through these stable, machine-readable commands. All of them take
`--json` (or, for `job start`, always emit JSON) and print a single JSON value
to stdout.

> **Stability:** the shapes below are a contract. Fields may be **added** in a
> minor release; existing fields will not change meaning without a major bump.

---

## Authentication

### `tgdl auth status --json`

```json
{ "authenticated": true, "user": { "id": 895556463, "first_name": "Ada", "username": "ada" } }
```

When not set up: `{ "authenticated": false }`. Authentication itself
(`tgdl init`) is interactive (phone code / 2FA) and must run in a terminal.

---

## Picking a target

### `tgdl dialogs --json [-n LIMIT]`

```json
[
  { "id": "-1001390335620", "name": "EDM Producers", "username": "forEDMproducer",
    "is_channel": true, "is_group": false, "is_user": false }
]
```

> **Note:** prefer `@username` as the download `channel` when one exists. Raw
> numeric IDs only resolve if the entity is already cached in the Telegram
> session, so `@username` is the robust choice.

---

## Jobs (background downloads)

A **job** is one detached download. It writes its live state to
`~/.tgdl/jobs/<job_id>/status.json`; readers poll that file (directly or via
`job status`). Downloads are serialized — only one runs at a time; the rest sit
in `phase: "queued"`.

### `tgdl job start --config FILE.json [--dry-run]`
### `tgdl job start --profile NAME [--channel OVERRIDE] [--dry-run]`

Starts a detached download and returns immediately:

```json
{ "job_id": "20260615-081327-d98eae", "pid": 97844 }
```

Error shapes (exit code 1): `{ "error": "not_authenticated" | "bad_config" | "bad_profile" | "missing_input" | "missing_channel", ... }`.

The `--config` file is a JSON [`DownloadConfig`](#downloadconfig).

### `tgdl job list --json`

Array of [status objects](#status-object), newest first.

### `tgdl job status <job_id> --json`

One [status object](#status-object), or `{ "error": "not_found" }`.

### `tgdl job cancel <job_id> --json`  →  `{ "ok": true, "job_id": "…" }`
### `tgdl job clean --json`  →  `{ "removed": 3 }`  (removes finished jobs)
### `tgdl job retry <job_id>`  →  `{ "job_id": "<new>", "pid": … }`  (re-runs from the saved config; always JSON)

### Status object

```json
{
  "job_id": "20260615-081327-d98eae",
  "pid": 97844,
  "phase": "downloading",
  "dry_run": false,
  "channel": "@forEDMproducer",
  "channel_name": "EDM Producers",
  "output_path": "/Users/me/tgdl_downloads",
  "started_at": "2026-06-15T08:13:27Z",
  "updated_at": "2026-06-15T08:13:41Z",
  "totals":   { "files": 24, "bytes": 8123456789 },
  "progress": { "completed": 16, "skipped": 2, "failed": 0, "bytes_done": 1760541 },
  "current_file": { "name": "clip_402.mp4", "pct": 64.2, "active_count": 3 },
  "speed_bps": 5400000,
  "eta_seconds": 980,
  "error": null
}
```

`phase` is one of: `queued`, `estimating`, `downloading`, `paused`, `done`,
`failed`, `cancelled`. `totals.files` is `null` until the estimate completes.
A job claiming an active phase whose process has died is reported as `failed`
(staleness reconciliation), so readers never see a zombie `downloading`.

---

## History

### `tgdl status --json`

```json
{
  "channels": [
    { "channel_id": "1390335620", "complete": 17, "partial": 0, "failed": 0,
      "skipped": 2, "total_bytes": 188000000, "total_bandwidth": 188000000 }
  ],
  "recent_sessions": [
    { "channel_id": "1390335620", "session_date": "2026-06-15",
      "bytes_downloaded": 1879487, "files_downloaded": 17 }
  ]
}
```

---

## Profiles

### `tgdl profile list --json`

```json
[ { "name": "edm", "channel": "@forEDMproducer", "media_types": ["photo"], "output_path": "~/tgdl_downloads" } ]
```

### `tgdl profile show <name> --json`  →  a [`DownloadConfig`](#downloadconfig), or `{ "error": "not_found" }`.

Run a profile as a background job with `tgdl job start --profile <name>`.

---

## DownloadConfig

The config object accepted by `job start --config`. It is the JSON form of the
internal Pydantic model — any field may be omitted to take its default.

```json
{
  "channel": "@forEDMproducer",
  "media_types": ["photo", "video", "document", "audio", "voice", "gif", "sticker"],
  "date_range_type": "all",
  "last_n_days": 30,
  "date_from": null,
  "date_to": null,
  "file_size": { "min_bytes": null, "max_bytes": null },
  "caption_keyword": null,
  "sender_filter": [],
  "deduplicate": true,
  "output_path": "~/tgdl_downloads",
  "filename_template": "{year}-{month}-{day}_{message_id}_{filename}",
  "subfolders_by_type": false,
  "subfolders_by_date": false,
  "subfolders_by_sender": false,
  "json_sidecars": false,
  "resume_mode": "smart",
  "concurrency": 3,
  "disk_space_threshold_mb": 500
}
```

- `date_range_type`: `all` | `last_n_days` | `custom`
- `resume_mode`: `smart` (skip complete, resume partial) | `skip` | `overwrite`
- Filename template tokens: `{year} {month} {day} {time} {date} {sender}
  {sender_id} {channel} {message_id} {filename} {ext} {type}`
