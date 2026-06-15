from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError
from rich.console import Console

from tgdl.config import PROFILES_DIR, DownloadConfig

console = Console()


def _profile_path(name: str) -> Path:
    return PROFILES_DIR / f"{name}.yaml"


def list_profiles() -> list[str]:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def save_profile(name: str, config: DownloadConfig):
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = _profile_path(name)
    # Pydantic v2: model_dump with mode='json' gives serialisable types
    data = config.model_dump(mode="json")
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    console.print(f"[green]✓ Profile [bold]{name}[/bold] saved → {path}[/green]")


def load_profile(name: str) -> DownloadConfig:
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Profile '{name}' not found. Run [bold]tgdl profile list[/bold].")
    with open(path) as f:
        data = yaml.safe_load(f)
    try:
        return DownloadConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Profile '{name}' has invalid data:\n{exc}") from exc


def delete_profile(name: str):
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Profile '{name}' not found.")
    path.unlink()
    console.print(f"[yellow]Profile [bold]{name}[/bold] deleted.[/yellow]")


def profile_exists(name: str) -> bool:
    return _profile_path(name).exists()
