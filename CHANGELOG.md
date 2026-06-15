# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-06-15

### Added
- **Background-job layer.** `tgdl job start/list/status/cancel/clean` run
  detached downloads that survive the launching terminal, writing live state to
  `~/.tgdl/jobs/<id>/status.json`. Downloads are serialized behind a lock so only
  one touches the Telegram session at a time.
- **JSON API** for integrations (`--json` on `status`, `auth status`, `dialogs`,
  and all `job` commands). Documented in `docs/json-api.md`. Powers the Raycast
  extension.
- `tgdl auth status` and `tgdl dialogs` commands.
- `ProgressReporter` abstraction with `RichReporter` (interactive TTY) and
  `JsonReporter` (machine-readable) backends.
- Headless download core (`tgdl/headless.py`) — wizard-free execution.
- Test suite (`pytest`) and CI.

### Changed
- The download engine now reports progress through a `ProgressReporter` instead
  of writing to Rich directly; interactive behaviour is unchanged.
- Replaced deprecated `datetime.utcnow()` usage.

## [0.1.0]

### Added
- Initial release: interactive download wizard, filters (media type, date, size,
  caption, sender), smart resume, deduplication, filename templates, subfolders,
  JSON sidecars, named profiles, disk/bandwidth tracking, and `status`/`reset`.
