from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from tgdl.config import DownloadConfig, MediaType
from tgdl.filters import message_media_type

if TYPE_CHECKING:
    from telethon.tl.types import Message


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(value: str) -> str:
    """Sanitize a string for use in a filesystem path component."""
    value = _INVALID_CHARS.sub("_", value)
    value = value.strip(". ")
    return value or "unnamed"


def _tokens(msg: Message, channel_name: str, media_type: MediaType) -> dict[str, str]:
    date = msg.date
    file = getattr(msg, "file", None)
    original_name = (file.name if file and file.name else "") or ""
    ext = (file.ext.lstrip(".") if file and file.ext else "") or _ext_for_type(media_type)
    stem = Path(original_name).stem if original_name else f"file_{msg.id}"

    sender = getattr(msg, "sender", None)
    sender_name = ""
    sender_id = ""
    if sender:
        sender_name = (
            getattr(sender, "username", None)
            or (getattr(sender, "first_name", "") or "").strip()
            or str(getattr(sender, "id", ""))
        )
        sender_id = str(getattr(sender, "id", ""))

    filename_no_ext = _safe(stem)

    return {
        "year": date.strftime("%Y"),
        "month": date.strftime("%m"),
        "day": date.strftime("%d"),
        "time": date.strftime("%H%M%S"),
        "date": date.strftime("%Y-%m-%d"),
        "sender": _safe(sender_name),
        "sender_id": _safe(sender_id),
        "channel": _safe(channel_name),
        "message_id": str(msg.id),
        "filename": filename_no_ext,
        "ext": ext,
        "type": media_type.value,
    }


def _ext_for_type(t: MediaType) -> str:
    return {
        MediaType.PHOTO: "jpg",
        MediaType.VIDEO: "mp4",
        MediaType.AUDIO: "mp3",
        MediaType.VOICE: "ogg",
        MediaType.GIF: "gif",
        MediaType.STICKER: "webp",
        MediaType.DOCUMENT: "bin",
    }.get(t, "bin")


def resolve_path(
    msg: Message,
    config: DownloadConfig,
    channel_name: str,
    base: Path | None = None,
) -> Path:
    """
    Build the destination path for a message's media file.
    Handles template substitution, subfolder organisation, and collision avoidance.
    """
    media_type = message_media_type(msg)
    if media_type is None:
        raise ValueError("Message has no downloadable media")

    t = _tokens(msg, channel_name, media_type)
    template = config.filename_template

    # Replace all {token} placeholders
    try:
        rendered = template.format(**t)
    except KeyError:
        rendered = re.sub(r"\{[^}]+\}", "_", template)
        rendered = rendered.replace("{", "").replace("}", "")

    # Inject subfolders around the rendered name
    parts = []
    if config.subfolders_by_type:
        parts.append(t["type"])
    if config.subfolders_by_date:
        parts.append(f"{t['year']}-{t['month']}")
    if config.subfolders_by_sender:
        parts.append(t["sender"])
    parts.append(rendered)

    rel_path = Path(*parts) if parts else Path(rendered)

    # Ensure extension is present
    if not rel_path.suffix:
        rel_path = rel_path.with_suffix(f".{t['ext']}")

    root = (base or config.output_path).expanduser().resolve()
    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    return dest


def unique_dest(dest: Path) -> Path:
    """Append _1, _2, … to avoid overwriting an existing file."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1
