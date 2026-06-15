from __future__ import annotations

import re
from typing import TYPE_CHECKING

from tgdl.config import DownloadConfig, MediaType

if TYPE_CHECKING:
    from telethon.tl.types import Message


def message_media_type(msg: Message) -> MediaType | None:
    """Return the MediaType for a message, or None if it has no downloadable media."""
    if msg.photo:
        return MediaType.PHOTO
    if msg.gif:
        return MediaType.GIF
    if msg.sticker:
        return MediaType.STICKER
    if msg.voice:
        return MediaType.VOICE
    if msg.audio:
        return MediaType.AUDIO
    if msg.video:
        return MediaType.VIDEO
    if msg.document:
        return MediaType.DOCUMENT
    return None


def passes_filters(msg: Message, config: DownloadConfig) -> tuple[bool, str]:
    """
    Returns (True, "") if the message passes all configured filters,
    or (False, reason) explaining which filter rejected it.
    """
    media_type = message_media_type(msg)

    if media_type is None:
        return False, "no media"

    if media_type not in config.media_types:
        return False, f"media type {media_type.value} not selected"

    file = getattr(msg, "file", None)
    file_size = file.size if file else None

    if file_size is not None:
        if config.file_size.min_bytes is not None and file_size < config.file_size.min_bytes:
            return False, f"file too small ({file_size} < {config.file_size.min_bytes})"
        if config.file_size.max_bytes is not None and file_size > config.file_size.max_bytes:
            return False, f"file too large ({file_size} > {config.file_size.max_bytes})"

    if config.caption_keyword:
        caption = msg.text or msg.message or ""
        if not _caption_matches(caption, config.caption_keyword):
            return False, "caption keyword not matched"

    if config.sender_filter:
        sender_name = _sender_name(msg)
        if sender_name not in config.sender_filter:
            return False, f"sender {sender_name!r} not in filter list"

    return True, ""


def _caption_matches(caption: str, keyword: str) -> bool:
    if keyword.startswith("/") and keyword.endswith("/") and len(keyword) > 2:
        pattern = keyword[1:-1]
        return bool(re.search(pattern, caption, re.IGNORECASE))
    return keyword.lower() in caption.lower()


def _sender_name(msg: Message) -> str:
    sender = getattr(msg, "sender", None)
    if sender is None:
        return ""
    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"
    first = getattr(sender, "first_name", "") or ""
    last = getattr(sender, "last_name", "") or ""
    return (first + " " + last).strip() or str(getattr(sender, "id", ""))


def parse_size(value: str) -> int:
    """Parse '100KB', '2.5MB', '1GB' → bytes."""
    value = value.strip().upper()
    units = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "B": 1}
    for suffix, mult in units.items():
        if value.endswith(suffix):
            num = value[: -len(suffix)].strip()
            return int(float(num) * mult)
    return int(value)
