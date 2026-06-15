from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from tgdl.config import (
    MEDIA_TYPE_LABELS,
    TEMPLATE_PRESETS,
    DateRangeType,
    DownloadConfig,
    FileSizeFilter,
    MediaType,
    ResumeMode,
)
from tgdl.filters import parse_size

console = Console()

_STYLE = Style(
    [
        ("qmark", "fg:#00bcd4 bold"),
        ("question", "bold"),
        ("answer", "fg:#00e676 bold"),
        ("pointer", "fg:#00bcd4 bold"),
        ("highlighted", "fg:#00bcd4 bold"),
        ("selected", "fg:#00e676"),
        ("separator", "fg:#555555"),
        ("instruction", "fg:#888888"),
    ]
)


async def _ask(fn, *args, **kwargs):
    """Run a synchronous questionary prompt in a thread so it doesn't clash with the running event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, style=_STYLE, **kwargs).ask())


def _section(title: str):
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="cyan"))


async def run_wizard(
    prefill: DownloadConfig | None = None,
    channel_override: str | None = None,
) -> DownloadConfig | None:
    """
    Interactive download configuration wizard.
    Returns a filled DownloadConfig, or None if the user cancelled.
    """
    cfg = prefill.model_copy(deep=True) if prefill else DownloadConfig()

    console.print(
        Panel.fit(
            "[bold cyan]tgdl — Download Wizard[/bold cyan]\n"
            "[dim]Press Ctrl+C at any time to cancel.[/dim]",
            border_style="cyan",
        )
    )

    # ── 1. Channel ───────────────────────────────────────────────────────
    _section("Channel / Chat")
    default_channel = channel_override or cfg.channel or ""
    channel = await _ask(
        questionary.text,
        "Channel, group, or chat  (@username, invite link, or numeric ID):",
        default=default_channel,
        validate=lambda v: bool(v.strip()) or "Required.",
    )
    if channel is None:
        return None
    cfg.channel = channel.strip()

    # ── 2. Media types ───────────────────────────────────────────────────
    _section("Media Types")
    current_types = {t.value for t in cfg.media_types}
    selected_types = await _ask(
        questionary.checkbox,
        "Which media types to download?",
        choices=[
            questionary.Choice(label, value=mt.value, checked=(mt.value in current_types))
            for mt, label in MEDIA_TYPE_LABELS.items()
        ],
    )
    if selected_types is None:
        return None
    if not selected_types:
        console.print("[red]You must select at least one media type.[/red]")
        return None
    cfg.media_types = [MediaType(t) for t in selected_types]

    # ── 3. Date range ────────────────────────────────────────────────────
    _section("Date Range")
    range_choice = await _ask(
        questionary.select,
        "Date range:",
        choices=[
            questionary.Choice("Entire history", value="all"),
            questionary.Choice("Last N days", value="last_n_days"),
            questionary.Choice("Custom range", value="custom"),
        ],
        default=cfg.date_range_type.value,
    )
    if range_choice is None:
        return None
    cfg.date_range_type = DateRangeType(range_choice)

    if cfg.date_range_type == DateRangeType.LAST_N_DAYS:
        days_str = await _ask(
            questionary.text,
            "How many days back?",
            default=str(cfg.last_n_days or 30),
            validate=lambda v: v.isdigit() and int(v) > 0 or "Enter a positive number.",
        )
        if days_str is None:
            return None
        cfg.last_n_days = int(days_str)

    elif cfg.date_range_type == DateRangeType.CUSTOM:
        from_str = await _ask(
            questionary.text,
            "Start date (YYYY-MM-DD, leave blank for no start):",
            default=cfg.date_from.strftime("%Y-%m-%d") if cfg.date_from else "",
        )
        if from_str is None:
            return None
        cfg.date_from = _parse_date(from_str)

        to_str = await _ask(
            questionary.text,
            "End date (YYYY-MM-DD, leave blank for no end):",
            default=cfg.date_to.strftime("%Y-%m-%d") if cfg.date_to else "",
        )
        if to_str is None:
            return None
        cfg.date_to = _parse_date(to_str)

    # ── 4. Filters ───────────────────────────────────────────────────────
    _section("Filters  (optional)")

    # File size
    enable_size = await _ask(
        questionary.confirm,
        "Add a file size filter?",
        default=cfg.file_size.min_bytes is not None or cfg.file_size.max_bytes is not None,
    )
    if enable_size is None:
        return None
    if enable_size:
        min_str = await _ask(
            questionary.text,
            "Minimum file size (e.g. 100KB, 2MB — blank for none):",
            default=_fmt_bytes_short(cfg.file_size.min_bytes) if cfg.file_size.min_bytes else "",
        )
        max_str = await _ask(
            questionary.text,
            "Maximum file size (e.g. 500MB, 2GB — blank for none):",
            default=_fmt_bytes_short(cfg.file_size.max_bytes) if cfg.file_size.max_bytes else "",
        )
        if min_str is None or max_str is None:
            return None
        cfg.file_size = FileSizeFilter(
            min_bytes=parse_size(min_str) if min_str.strip() else None,
            max_bytes=parse_size(max_str) if max_str.strip() else None,
        )

    # Caption keyword
    caption_kw = await _ask(
        questionary.text,
        "Caption keyword filter (plain text or /regex/, blank to skip):",
        default=cfg.caption_keyword or "",
    )
    if caption_kw is None:
        return None
    cfg.caption_keyword = caption_kw.strip() or None

    # Sender filter
    sender_raw = await _ask(
        questionary.text,
        "Restrict to specific senders? (comma-separated @usernames or names, blank for all):",
        default=", ".join(cfg.sender_filter) if cfg.sender_filter else "",
    )
    if sender_raw is None:
        return None
    cfg.sender_filter = [s.strip() for s in sender_raw.split(",") if s.strip()]

    # Deduplication
    dedup = await _ask(
        questionary.confirm,
        "Skip hash-duplicate files (same content, different name)?",
        default=cfg.deduplicate,
    )
    if dedup is None:
        return None
    cfg.deduplicate = dedup

    # ── 5. Output path ───────────────────────────────────────────────────
    _section("Output")

    out_str = await _ask(
        questionary.path,
        "Download folder:",
        default=str(cfg.output_path),
        only_directories=True,
    )
    if out_str is None:
        return None
    cfg.output_path = Path(out_str).expanduser()

    # ── 6. Filename template ─────────────────────────────────────────────
    _section("Filename Template")
    console.print(
        "[dim]Tokens: {year} {month} {day} {time} {date} {sender} {sender_id} "
        "{channel} {message_id} {filename} {ext} {type}[/dim]"
    )

    preset_names = list(TEMPLATE_PRESETS.keys()) + ["Custom…"]
    preset_choice = await _ask(
        questionary.select,
        "Choose a filename template preset:",
        choices=preset_names,
        default=preset_names[0],
    )
    if preset_choice is None:
        return None

    if preset_choice == "Custom…":
        custom = await _ask(
            questionary.text,
            "Enter template:",
            default=cfg.filename_template,
            validate=lambda v: bool(v.strip()) or "Required.",
        )
        if custom is None:
            return None
        cfg.filename_template = custom.strip()
    else:
        cfg.filename_template = TEMPLATE_PRESETS[preset_choice]

    # Subfolder options
    subfolder_choices = await _ask(
        questionary.checkbox,
        "Also organise into subfolders by:",
        choices=[
            questionary.Choice(
                "Media type  (photo/video/…)", value="type", checked=cfg.subfolders_by_type
            ),
            questionary.Choice("Year-Month", value="date", checked=cfg.subfolders_by_date),
            questionary.Choice("Sender", value="sender", checked=cfg.subfolders_by_sender),
        ],
    )
    if subfolder_choices is None:
        return None
    cfg.subfolders_by_type = "type" in subfolder_choices
    cfg.subfolders_by_date = "date" in subfolder_choices
    cfg.subfolders_by_sender = "sender" in subfolder_choices

    # JSON sidecars
    sidecars = await _ask(
        questionary.confirm,
        "Save message metadata as .json sidecar files?",
        default=cfg.json_sidecars,
    )
    if sidecars is None:
        return None
    cfg.json_sidecars = sidecars

    # ── 7. Resume mode ───────────────────────────────────────────────────
    _section("Resume / Overwrite")
    resume_choice = await _ask(
        questionary.select,
        "How to handle files that already exist?",
        choices=[
            questionary.Choice(
                "Smart continue  — skip completed, resume partially downloaded files",
                value="smart",
            ),
            questionary.Choice(
                "Skip existing   — skip any file that already exists on disk",
                value="skip",
            ),
            questionary.Choice(
                "Overwrite all   — re-download everything",
                value="overwrite",
            ),
        ],
        default=cfg.resume_mode.value,
    )
    if resume_choice is None:
        return None
    cfg.resume_mode = ResumeMode(resume_choice)

    # ── 8. Concurrency ───────────────────────────────────────────────────
    _section("Performance")
    concurrency_str = await _ask(
        questionary.select,
        "Concurrent downloads:",
        choices=["1", "2", "3", "5", "8", "10"],
        default=str(cfg.concurrency),
    )
    if concurrency_str is None:
        return None
    cfg.concurrency = int(concurrency_str)

    disk_threshold_str = await _ask(
        questionary.text,
        "Pause when free disk space falls below (MB):",
        default=str(cfg.disk_space_threshold_mb),
        validate=lambda v: v.isdigit() and int(v) >= 0 or "Enter a non-negative number.",
    )
    if disk_threshold_str is None:
        return None
    cfg.disk_space_threshold_mb = int(disk_threshold_str)

    return cfg


def _parse_date(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _fmt_bytes_short(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{int(n)}{unit}"
        n //= 1024
    return f"{int(n)}GB"
