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
