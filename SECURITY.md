# Security Policy

## Your credentials stay local

tgdl stores everything under `~/.tgdl/` on your own machine and talks directly
to Telegram's API. Nothing is sent to any third-party server.

- `~/.tgdl/credentials.yaml` — your Telegram API ID/hash, written `chmod 600`.
- `~/.tgdl/session.session` — your Telegram session file. **Treat this like a
  password**: anyone with it can act as your Telegram account. It is never
  committed (see `.gitignore`) and should never be shared.

If you think your session may be compromised, revoke it from Telegram:
*Settings → Devices → Active sessions*, then re-run `tgdl init --force`.

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue:

- Use GitHub's **Report a vulnerability** (Security → Advisories) on this repo, or
- email the maintainer listed on the GitHub profile.

You'll get an acknowledgement within a few days. Thanks for helping keep users
safe.
