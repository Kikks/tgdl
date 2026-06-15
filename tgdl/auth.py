from __future__ import annotations

import stat
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from tgdl.config import CREDENTIALS_FILE, SESSION_FILE, TGDL_DIR

console = Console()


def _ensure_tgdl_dir():
    TGDL_DIR.mkdir(parents=True, exist_ok=True)


def load_credentials() -> tuple[int, str] | None:
    if not CREDENTIALS_FILE.exists():
        return None
    with open(CREDENTIALS_FILE) as f:
        data = yaml.safe_load(f)
    if data and "api_id" in data and "api_hash" in data:
        return int(data["api_id"]), str(data["api_hash"])
    return None


def save_credentials(api_id: int, api_hash: str):
    _ensure_tgdl_dir()
    with open(CREDENTIALS_FILE, "w") as f:
        yaml.dump({"api_id": api_id, "api_hash": api_hash}, f)
    CREDENTIALS_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


def make_client(api_id: int, api_hash: str) -> TelegramClient:
    _ensure_tgdl_dir()
    return TelegramClient(str(SESSION_FILE), api_id, api_hash)


def is_authenticated() -> bool:
    creds = load_credentials()
    if not creds:
        return False
    session = Path(str(SESSION_FILE) + ".session")
    return session.exists()


async def run_init(force: bool = False):
    """Interactive first-time setup: prompt for API credentials and authenticate."""
    _ensure_tgdl_dir()

    console.print(
        Panel.fit(
            "[bold cyan]tgdl — Telegram Media Downloader[/bold cyan]\n[dim]First-time setup[/dim]",
            border_style="cyan",
        )
    )

    existing = load_credentials()
    if existing and not force:
        console.print(
            "[yellow]Credentials already exist.[/yellow] "
            "Use [bold]tgdl init --force[/bold] to re-authenticate.\n"
        )
        client = make_client(*existing)
        async with client:
            me = await client.get_me()
            if me:
                console.print(
                    f"[green]✓ Authenticated as[/green] [bold]{me.first_name}[/bold]"
                    + (f" @{me.username}" if me.username else "")
                )
                return
        console.print("[yellow]Session invalid — re-running setup.[/yellow]")

    console.print(
        "\nYou need a Telegram API ID and hash. Get them from "
        "[link=https://my.telegram.org]https://my.telegram.org[/link] → "
        "[bold]API Development Tools[/bold].\n"
    )

    import questionary

    api_id_str = await _ask("API ID (numeric)", questionary)
    while not api_id_str.strip().isdigit():
        console.print("[red]API ID must be a number.[/red]")
        api_id_str = await _ask("API ID (numeric)", questionary)

    api_hash = (await _ask("API Hash", questionary)).strip()
    while len(api_hash) != 32:
        console.print("[red]API Hash must be 32 characters.[/red]")
        api_hash = (await _ask("API Hash", questionary)).strip()

    api_id = int(api_id_str)
    save_credentials(api_id, api_hash)
    console.print("[green]✓ Credentials saved.[/green]")

    client = make_client(api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        phone = (
            await _ask("Phone number (with country code, e.g. +15551234567)", questionary)
        ).strip()
        await client.send_code_request(phone)
        code = (await _ask("Verification code from Telegram", questionary)).strip()
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = (await _ask("2FA password", questionary, password=True)).strip()
            await client.sign_in(password=password)

    me = await client.get_me()
    await client.disconnect()

    console.print(
        f"\n[green bold]✓ Authenticated as[/green bold] [bold]{me.first_name}[/bold]"
        + (f" @{me.username}" if me.username else "")
        + f"  (id: {me.id})"
    )
    console.print("[dim]Session saved. You won't need to log in again.[/dim]")


async def _ask(prompt: str, questionary_mod, password: bool = False) -> str:
    import asyncio

    loop = asyncio.get_event_loop()
    if password:
        return await loop.run_in_executor(None, lambda: questionary_mod.password(prompt).ask())
    return await loop.run_in_executor(None, lambda: questionary_mod.text(prompt).ask())


# ── headless / step-wise login (drives the Raycast onboarding) ─────────────────


def _user_dict(me) -> dict:
    return {"id": me.id, "first_name": me.first_name, "username": me.username}


async def send_login_code(api_id: int, api_hash: str, phone: str) -> dict:
    """
    Step 1 of headless login: persist credentials and request an SMS/app code.

    Returns ``{"phone_code_hash": ...}`` to pass to :func:`complete_login`, or
    ``{"already_authorized": True, "user": ...}`` if the session is already valid.
    """
    save_credentials(api_id, api_hash)
    client = make_client(api_id, api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            return {
                "ok": True,
                "already_authorized": True,
                "user": _user_dict(await client.get_me()),
            }
        sent = await client.send_code_request(phone)
        return {"ok": True, "phone_code_hash": sent.phone_code_hash}
    finally:
        await client.disconnect()


async def complete_login(phone: str, code: str, phone_code_hash: str, password: str | None) -> dict:
    """
    Step 2 of headless login: sign in with the received code (and 2FA password
    if the account has one). Reuses the session persisted by step 1.
    """
    creds = load_credentials()
    if not creds:
        return {"error": "no_credentials"}
    client = make_client(*creds)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                return {"ok": False, "needs_password": True}
            await client.sign_in(password=password)
        return {"ok": True, "user": _user_dict(await client.get_me())}
    finally:
        await client.disconnect()


async def do_logout() -> dict:
    """Log out of Telegram and remove the local session (for re-onboarding)."""
    creds = load_credentials()
    if creds:
        client = make_client(*creds)
        await client.connect()
        try:
            await client.log_out()
        except Exception:  # noqa: BLE001 - logging out is best-effort
            pass
        finally:
            if client.is_connected():
                await client.disconnect()
    session = Path(str(SESSION_FILE) + ".session")
    session.unlink(missing_ok=True)
    return {"ok": True}
