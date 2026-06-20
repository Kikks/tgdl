from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from tgdl import __version__

app = typer.Typer(
    name="tgdl",
    help="Telegram media downloader — download photos, videos, and more from any channel or chat.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
profile_app = typer.Typer(help="Manage named download profiles.")
app.add_typer(profile_app, name="profile")
job_app = typer.Typer(help="Run and monitor background download jobs.")
app.add_typer(job_app, name="job")
auth_app = typer.Typer(help="Inspect Telegram authentication.")
app.add_typer(auth_app, name="auth")

console = Console()


def _print_json(data) -> None:
    """Emit compact JSON to stdout for machine consumers (the Raycast extension)."""
    import json

    print(json.dumps(data, ensure_ascii=False, default=str))


# ── helpers ──────────────────────────────────────────────────────────────────


def _run(coro):
    """Run an async function from a sync Typer command."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit()


def _require_auth():
    from tgdl.auth import is_authenticated

    if not is_authenticated():
        console.print("[red bold]Not authenticated.[/red bold] Run [bold]tgdl init[/bold] first.")
        raise typer.Exit(1)


def _require_auth_quiet() -> bool:
    """Like _require_auth but returns a bool instead of printing/exiting."""
    from tgdl.auth import is_authenticated

    return is_authenticated()


def _get_client():
    from tgdl.auth import load_credentials, make_client

    creds = load_credentials()
    if not creds:
        console.print("[red]No credentials found. Run [bold]tgdl init[/bold].[/red]")
        raise typer.Exit(1)
    return make_client(*creds)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ── tgdl init ────────────────────────────────────────────────────────────────


@app.command()
def init(
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-authenticate even if already set up."
    ),
):
    """First-time setup: save API credentials and authenticate with Telegram."""
    from tgdl.auth import run_init

    _run(run_init(force=force))


@app.command()
def version(json_out: bool = typer.Option(False, "--json", help="Emit JSON.")):
    """Print the installed tgdl version."""
    if json_out:
        _print_json({"version": __version__})
    else:
        console.print(__version__)


# ── tgdl download ────────────────────────────────────────────────────────────


@app.command()
def download(
    channel: str | None = typer.Argument(None, help="@username, invite link, or numeric ID."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview what would be downloaded without saving anything."
    ),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Start wizard pre-filled from a saved profile."
    ),
):
    """
    Launch the interactive download wizard, then start downloading.

    Use [bold]--dry-run[/bold] to preview the download without saving any files.
    Use [bold]--profile NAME[/bold] to start with settings from a saved profile.
    """
    _require_auth()
    _run(_download_flow(channel, dry_run=dry_run, profile_name=profile))


async def _download_flow(
    channel_arg: str | None,
    dry_run: bool,
    profile_name: str | None,
):
    from tgdl.auth import load_credentials, make_client
    from tgdl.config import STATE_DB
    from tgdl.downloader import estimate, print_estimate, run_download
    from tgdl.profiles import load_profile
    from tgdl.state import StateDB
    from tgdl.tui.wizard import run_wizard

    prefill = None
    if profile_name:
        try:
            prefill = load_profile(profile_name)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)

    config = await run_wizard(prefill=prefill, channel_override=channel_arg)
    if config is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # Optionally save as profile
    import questionary

    save_name = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: questionary.text(
            "Save these settings as a profile? (name, or blank to skip):"
        ).ask(),
    )
    if save_name and save_name.strip():
        from tgdl.profiles import save_profile

        save_profile(save_name.strip(), config)

    creds = load_credentials()
    client = make_client(*creds)

    async with client:
        try:
            entity = await client.get_entity(config.channel)
        except Exception as exc:
            console.print(f"[red]Could not resolve channel: {exc}[/red]")
            return

        channel_name = (
            getattr(entity, "title", None) or getattr(entity, "username", None) or config.channel
        )
        channel_id = str(entity.id)

        console.print(Rule(f"[bold]{channel_name}[/bold]", style="cyan"))

        with StateDB(STATE_DB) as state:
            # Resource estimate
            console.print("\n[dim]Scanning messages for estimate…[/dim]")
            est = await estimate(client, entity, config, state, channel_id)
            ok = print_estimate(est, config)

            if not ok and not dry_run:
                console.print("[red]Aborting due to insufficient disk space.[/red]")
                return

            if est.total_files == 0:
                console.print("[green]Nothing new to download.[/green]")
                return

            # Confirm
            confirmed = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: questionary.confirm(
                    f"\n{'[DRY RUN] ' if dry_run else ''}Start download?",
                    default=True,
                ).ask(),
            )
            if not confirmed:
                console.print("[yellow]Cancelled.[/yellow]")
                return

            console.print()
            stats = await run_download(
                client, entity, config, state, channel_id, channel_name, dry_run=dry_run
            )

        _print_summary(stats, dry_run)


def _print_summary(stats, dry_run: bool):
    from tgdl.downloader import _fmt_bytes as fmt

    console.print(Rule("[bold cyan]Download Complete[/bold cyan]", style="cyan"))
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="bold")

    label = "Would download" if dry_run else "Downloaded"
    table.add_row(f"{label}:", str(stats.completed))
    table.add_row("Skipped:", str(stats.skipped))
    table.add_row("Failed:", str(stats.failed))
    if not dry_run:
        table.add_row("Data transferred:", fmt(stats.bytes_downloaded))
    console.print(table)

    if stats.errors:
        console.print("\n[red]Errors:[/red]")
        for err in stats.errors[:10]:
            console.print(f"  [red dim]{err}[/red dim]")
        if len(stats.errors) > 10:
            console.print(f"  [dim]… and {len(stats.errors) - 10} more[/dim]")


# ── tgdl status ──────────────────────────────────────────────────────────────


@app.command()
def status(json_out: bool = typer.Option(False, "--json", help="Emit JSON for machine consumers.")):
    """Show download history, per-channel statistics, and bandwidth usage."""
    from tgdl.config import STATE_DB
    from tgdl.downloader import _fmt_bytes as fmt
    from tgdl.state import StateDB

    with StateDB(STATE_DB) as db:
        channels = db.all_channels()

        if json_out:
            _print_json(
                {
                    "channels": [{"channel_id": ch, **db.channel_stats(ch)} for ch in channels],
                    "recent_sessions": [dict(r) for r in db.recent_sessions(20)],
                }
            )
            return

        if not channels:
            console.print(
                "[dim]No download history yet. Run [bold]tgdl download[/bold] to get started.[/dim]"
            )
            return

        for ch in channels:
            s = db.channel_stats(ch)
            table = Table(title=ch, show_header=False, box=None, padding=(0, 2))
            table.add_column(style="dim")
            table.add_column(style="bold")
            table.add_row("Files complete:", str(s["complete"]))
            table.add_row("Partial:", str(s["partial"]))
            table.add_row("Failed:", str(s["failed"]))
            table.add_row("Skipped:", str(s["skipped"]))
            table.add_row("Total size:", fmt(s["total_bytes"]))
            table.add_row("Total bandwidth:", fmt(s["total_bandwidth"]))
            console.print(table)
            console.print()

        console.print(Rule("[dim]Recent sessions[/dim]"))
        sessions = db.recent_sessions(20)
        sess_table = Table(show_header=True, header_style="bold cyan")
        sess_table.add_column("Date")
        sess_table.add_column("Channel")
        sess_table.add_column("Files", justify="right")
        sess_table.add_column("Data", justify="right")
        for row in sessions:
            sess_table.add_row(
                row["session_date"],
                row["channel_id"],
                str(row["files_downloaded"]),
                fmt(row["bytes_downloaded"]),
            )
        console.print(sess_table)


# ── tgdl reset ───────────────────────────────────────────────────────────────


@app.command()
def reset(
    channel: str = typer.Argument(..., help="Channel ID or @username whose state to clear."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
):
    """
    Clear download state for a channel so it will be fully re-downloaded next run.

    [yellow]Warning:[/yellow] this does not delete already-downloaded files from disk.
    """
    from tgdl.config import STATE_DB
    from tgdl.state import StateDB

    if not yes:
        confirmed = typer.confirm(
            f"Clear all download state for '{channel}'? Files on disk are not deleted.",
            default=False,
        )
        if not confirmed:
            raise typer.Abort()

    with StateDB(STATE_DB) as db:
        db.clear_channel(channel)
    console.print(f"[green]✓ State cleared for [bold]{channel}[/bold].[/green]")


# ── tgdl profile * ───────────────────────────────────────────────────────────


@profile_app.command("list")
def profile_list(json_out: bool = typer.Option(False, "--json", help="Emit JSON.")):
    """List all saved profiles."""
    from tgdl.profiles import list_profiles, load_profile

    profiles = list_profiles()

    if json_out:
        items = []
        for name in profiles:
            try:
                cfg = load_profile(name)
                items.append(
                    {
                        "name": name,
                        "channel": cfg.channel,
                        "media_types": [m.value for m in cfg.media_types],
                        "output_path": str(cfg.output_path),
                    }
                )
            except (FileNotFoundError, ValueError):
                items.append({"name": name, "channel": "", "media_types": [], "output_path": ""})
        _print_json(items)
        return

    if not profiles:
        console.print(
            "[dim]No profiles saved yet. Use [bold]tgdl download --profile[/bold] or save during a wizard run.[/dim]"
        )
        return
    for name in profiles:
        console.print(f"  [cyan]•[/cyan] {name}")


@profile_app.command("save")
def profile_save(
    name: str = typer.Option(..., "--name", help="Profile name."),
    config: str = typer.Option(..., "--config", "-c", help="Path to a JSON DownloadConfig."),
):
    """Save a JSON config as a named profile (headless). Emits JSON."""
    import json as _json
    from pathlib import Path

    from tgdl.config import DownloadConfig
    from tgdl.profiles import save_profile

    try:
        data = _json.loads(Path(config).read_text(encoding="utf-8"))
        cfg = DownloadConfig.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        _print_json({"error": "bad_config", "detail": str(exc)})
        raise typer.Exit(1) from exc

    save_profile(name.strip(), cfg, quiet=True)
    _print_json({"ok": True, "name": name.strip()})


@profile_app.command("run")
def profile_run(
    name: str = typer.Argument(..., help="Profile name to run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without downloading."),
):
    """Run a saved profile directly, skipping the wizard."""
    _require_auth()
    _run(_download_flow(channel_arg=None, dry_run=dry_run, profile_name=name))


@profile_app.command("delete")
def profile_delete(
    name: str = typer.Argument(..., help="Profile name to delete."),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Delete a saved profile."""
    from tgdl.profiles import delete_profile

    if not yes:
        confirmed = typer.confirm(f"Delete profile '{name}'?", default=False)
        if not confirmed:
            raise typer.Abort()
    try:
        delete_profile(name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


@profile_app.command("show")
def profile_show(
    name: str = typer.Argument(..., help="Profile name to inspect."),
    json_out: bool = typer.Option(False, "--json", help="Emit the config as JSON."),
):
    """Print the configuration stored in a profile."""
    from tgdl.profiles import load_profile

    try:
        cfg = load_profile(name)
    except (FileNotFoundError, ValueError) as exc:
        if json_out:
            _print_json({"error": "not_found", "detail": str(exc)})
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if json_out:
        _print_json(cfg.model_dump(mode="json"))
        return

    import yaml

    console.print(
        yaml.dump(cfg.model_dump(mode="json"), default_flow_style=False, allow_unicode=True)
    )


# ── tgdl job * ─────────────────────────────────────────────────────────────────


@job_app.command("start")
def job_start(
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to a JSON DownloadConfig."
    ),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Start from a saved profile instead."
    ),
    channel: str | None = typer.Option(
        None, "--channel", help="Override the channel from config/profile."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without saving files."),
):
    """
    Start a detached background download and print its job id as JSON.

    Provide either [bold]--config FILE[/bold] (a JSON DownloadConfig) or
    [bold]--profile NAME[/bold]. The download keeps running after this command
    returns; monitor it with [bold]tgdl job list[/bold].
    """
    import json as _json

    from tgdl.config import DownloadConfig
    from tgdl.jobs import create_job, start_detached

    if not _require_auth_quiet():
        _print_json({"error": "not_authenticated", "hint": "Run `tgdl init` first."})
        raise typer.Exit(1)

    if config:
        try:
            from pathlib import Path

            data = _json.loads(Path(config).read_text(encoding="utf-8"))
            cfg = DownloadConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            _print_json({"error": "bad_config", "detail": str(exc)})
            raise typer.Exit(1) from exc
    elif profile:
        from tgdl.profiles import load_profile

        try:
            cfg = load_profile(profile)
        except (FileNotFoundError, ValueError) as exc:
            _print_json({"error": "bad_profile", "detail": str(exc)})
            raise typer.Exit(1)
    else:
        _print_json({"error": "missing_input", "hint": "Pass --config FILE or --profile NAME."})
        raise typer.Exit(1)

    if channel:
        cfg.channel = channel
    if not cfg.channel:
        _print_json({"error": "missing_channel"})
        raise typer.Exit(1)

    job_id = create_job(cfg, dry_run=dry_run)
    pid = start_detached(job_id, dry_run=dry_run)
    _print_json({"job_id": job_id, "pid": pid})


@job_app.command("retry")
def job_retry(job_id: str = typer.Argument(..., help="Job id to re-run.")):
    """Re-run a job from its saved config as a new detached job. Emits JSON."""
    from tgdl.jobs import retry_job

    if not _require_auth_quiet():
        _print_json({"error": "not_authenticated", "hint": "Run `tgdl init` first."})
        raise typer.Exit(1)

    result = retry_job(job_id)
    if result is None:
        _print_json({"error": "not_found", "job_id": job_id})
        raise typer.Exit(1)
    new_id, pid = result
    _print_json({"job_id": new_id, "pid": pid})


@job_app.command("list")
def job_list(json_out: bool = typer.Option(False, "--json", help="Emit JSON.")):
    """List all background jobs and their current state."""
    from tgdl.jobs import list_jobs

    jobs = list_jobs()
    if json_out:
        _print_json(jobs)
        return

    if not jobs:
        console.print("[dim]No jobs yet. Start one with [bold]tgdl job start[/bold].[/dim]")
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Job")
    table.add_column("Channel")
    table.add_column("Phase")
    table.add_column("Progress", justify="right")
    for j in jobs:
        p = j.get("progress", {})
        t = j.get("totals", {})
        table.add_row(
            j["job_id"],
            j.get("channel_name") or j.get("channel", ""),
            j.get("phase", "?"),
            f"{p.get('completed', 0)}/{t.get('files') if t.get('files') is not None else '?'}",
        )
    console.print(table)


@job_app.command("status")
def job_status(
    job_id: str = typer.Argument(..., help="Job id."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
):
    """Show one job's current status."""
    from tgdl.jobs import read_status

    status = read_status(job_id)
    if status is None:
        if json_out:
            _print_json({"error": "not_found", "job_id": job_id})
        else:
            console.print(f"[red]No such job: {job_id}[/red]")
        raise typer.Exit(1)
    if json_out:
        _print_json(status)
    else:
        import yaml

        console.print(yaml.dump(status, default_flow_style=False, allow_unicode=True))


@job_app.command("cancel")
def job_cancel(
    job_id: str = typer.Argument(..., help="Job id."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
):
    """Cancel a running job."""
    from tgdl.jobs import cancel_job

    ok = cancel_job(job_id)
    if json_out:
        _print_json({"ok": ok, "job_id": job_id})
    elif ok:
        console.print(f"[yellow]Cancelled {job_id}.[/yellow]")
    else:
        console.print(f"[red]Could not cancel {job_id}.[/red]")


@job_app.command("clean")
def job_clean(json_out: bool = typer.Option(False, "--json", help="Emit JSON.")):
    """Remove finished job directories."""
    from tgdl.jobs import clean_jobs

    n = clean_jobs(only_done=True)
    if json_out:
        _print_json({"removed": n})
    else:
        console.print(f"[green]Removed {n} finished job(s).[/green]")


@app.command("_job-run", hidden=True)
def _job_run(
    job_id: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Internal: the detached worker process for a job. Not for direct use."""
    from tgdl.worker import run_job

    raise typer.Exit(run_job(job_id, dry_run=dry_run))


# ── tgdl auth status ───────────────────────────────────────────────────────────


@auth_app.command("status")
def auth_status(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
    verify: bool = typer.Option(
        False,
        "--verify",
        help=(
            "Verify the session against Telegram (network). Off by default so this "
            "command never opens the session database — which would clash with a "
            "running download and raise 'database is locked'."
        ),
    ),
):
    """Report whether tgdl is authenticated (with --verify, also as whom)."""
    from tgdl.auth import is_authenticated, load_credentials, make_client

    # Default: a cheap, local-only check (credentials + session file exist). Safe
    # to poll frequently and never contends for the Telegram session DB.
    if not verify:
        result = {"authenticated": is_authenticated(), "version": __version__}
        if json_out:
            _print_json(result)
        elif result["authenticated"]:
            console.print("[green]✓ Authenticated.[/green]")
        else:
            console.print("[yellow]Not authenticated.[/yellow] Run [bold]tgdl init[/bold].")
        return

    if not is_authenticated():
        result = {"authenticated": False, "version": __version__}
        if json_out:
            _print_json(result)
        else:
            console.print("[yellow]Not authenticated.[/yellow] Run [bold]tgdl init[/bold].")
        return

    async def _check():
        client = make_client(*load_credentials())
        async with client:
            if not await client.is_user_authorized():
                return {"authenticated": False}
            me = await client.get_me()
            return {
                "authenticated": True,
                "user": {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username,
                },
            }

    result = _run(_check())
    result["version"] = __version__
    if json_out:
        _print_json(result)
    elif result.get("authenticated"):
        u = result["user"]
        console.print(
            f"[green]✓ Authenticated as[/green] [bold]{u['first_name']}[/bold]"
            + (f" @{u['username']}" if u["username"] else "")
        )
    else:
        console.print(
            "[yellow]Session not authorized.[/yellow] Run [bold]tgdl init --force[/bold]."
        )


def _auth_error_code(exc: Exception) -> str:
    name = type(exc).__name__
    return {
        "PhoneCodeInvalidError": "invalid_code",
        "PhoneCodeExpiredError": "code_expired",
        "PhoneNumberInvalidError": "invalid_phone",
        "PhoneNumberUnoccupiedError": "invalid_phone",
        "PasswordHashInvalidError": "invalid_password",
        "ApiIdInvalidError": "invalid_api",
        "FloodWaitError": "flood_wait",
    }.get(name, "auth_error")


@auth_app.command("login-start")
def auth_login_start(
    api_id: str = typer.Option(..., "--api-id"),
    api_hash: str = typer.Option(..., "--api-hash"),
    phone: str = typer.Option(..., "--phone"),
):
    """Headless login step 1: save credentials and request a verification code. Emits JSON."""
    from tgdl.auth import send_login_code

    if not api_id.strip().isdigit():
        _print_json({"error": "bad_api_id"})
        raise typer.Exit(1)
    try:
        result = _run(send_login_code(int(api_id), api_hash.strip(), phone.strip()))
    except Exception as exc:  # noqa: BLE001 - mapped to a JSON error code for the UI
        result = {"error": _auth_error_code(exc), "detail": str(exc)}
    _print_json(result)


@auth_app.command("login-finish")
def auth_login_finish(
    phone: str = typer.Option(..., "--phone"),
    code: str = typer.Option(..., "--code"),
    phone_code_hash: str = typer.Option(..., "--phone-code-hash"),
    password_stdin: bool = typer.Option(
        False,
        "--password-stdin",
        help="Read the 2FA password from stdin (avoids it showing in ps).",
    ),
):
    """Headless login step 2: sign in with the code (and 2FA password). Emits JSON."""
    import sys

    from tgdl.auth import complete_login

    password = sys.stdin.read().strip() or None if password_stdin else None
    try:
        result = _run(complete_login(phone.strip(), code.strip(), phone_code_hash, password))
    except Exception as exc:  # noqa: BLE001 - mapped to a JSON error code for the UI
        result = {"error": _auth_error_code(exc), "detail": str(exc)}
    _print_json(result)


@auth_app.command("logout")
def auth_logout():
    """Log out of Telegram and remove the local session. Emits JSON."""
    from tgdl.auth import do_logout

    _print_json(_run(do_logout()))


# ── tgdl dialogs ───────────────────────────────────────────────────────────────


@app.command()
def dialogs(
    limit: int = typer.Option(50, "--limit", "-n", help="Max chats to list."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
):
    """List your recent chats/channels (handy for picking a download target)."""
    _require_auth()

    async def _list():
        client = _get_client()
        out = []
        async with client:
            async for d in client.iter_dialogs(limit=limit):
                ent = d.entity
                out.append(
                    {
                        "id": str(d.id),
                        "name": d.name,
                        "username": getattr(ent, "username", None),
                        "is_channel": bool(getattr(d, "is_channel", False)),
                        "is_group": bool(getattr(d, "is_group", False)),
                        "is_user": bool(getattr(d, "is_user", False)),
                    }
                )
        return out

    items = _run(_list())
    if json_out:
        _print_json(items)
        return
    for it in items:
        handle = f" [dim]@{it['username']}[/dim]" if it["username"] else ""
        console.print(f"  [cyan]•[/cyan] {it['name']}{handle}")
