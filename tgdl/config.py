from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    GIF = "gif"
    STICKER = "sticker"


MEDIA_TYPE_LABELS: dict[MediaType, str] = {
    MediaType.PHOTO: "Photos",
    MediaType.VIDEO: "Videos",
    MediaType.DOCUMENT: "Documents",
    MediaType.AUDIO: "Audio files",
    MediaType.VOICE: "Voice messages",
    MediaType.GIF: "GIFs / Animations",
    MediaType.STICKER: "Stickers",
}


class DateRangeType(str, Enum):
    ALL = "all"
    LAST_N_DAYS = "last_n_days"
    CUSTOM = "custom"


class ResumeMode(str, Enum):
    SMART = "smart"  # skip completed, resume partial downloads
    SKIP = "skip"  # skip if dest file already exists on disk
    OVERWRITE = "overwrite"


class FileSizeFilter(BaseModel):
    min_bytes: int | None = None
    max_bytes: int | None = None


class DownloadConfig(BaseModel):
    channel: str = ""
    media_types: list[MediaType] = Field(default_factory=lambda: list(MediaType))
    date_range_type: DateRangeType = DateRangeType.ALL
    last_n_days: int | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

    file_size: FileSizeFilter = Field(default_factory=FileSizeFilter)
    caption_keyword: str | None = None
    sender_filter: list[str] = Field(default_factory=list)
    deduplicate: bool = True

    output_path: Path = Field(default_factory=lambda: Path.home() / "tgdl_downloads")
    filename_template: str = "{year}-{month}-{day}_{message_id}_{filename}"
    subfolders_by_type: bool = False
    subfolders_by_date: bool = False
    subfolders_by_sender: bool = False
    json_sidecars: bool = False

    resume_mode: ResumeMode = ResumeMode.SMART
    concurrency: int = 3
    disk_space_threshold_mb: int = 500


TGDL_DIR = Path.home() / ".tgdl"
CREDENTIALS_FILE = TGDL_DIR / "credentials.yaml"
SESSION_FILE = TGDL_DIR / "session"
STATE_DB = TGDL_DIR / "state.db"
PROFILES_DIR = TGDL_DIR / "profiles"
JOBS_DIR = TGDL_DIR / "jobs"
JOBS_LOCK = JOBS_DIR / ".lock"


FILENAME_TEMPLATE_TOKENS = (
    "{year}    - 4-digit year of the message\n"
    "{month}   - 2-digit month\n"
    "{day}     - 2-digit day\n"
    "{time}    - HHMMSS of the message\n"
    "{date}    - YYYY-MM-DD shorthand\n"
    "{sender}  - sender's display name\n"
    "{sender_id} - sender's numeric ID\n"
    "{channel} - channel/chat name\n"
    "{message_id} - Telegram message ID\n"
    "{filename} - original filename (or 'file' if unknown)\n"
    "{ext}     - file extension without dot\n"
    "{type}    - media type (photo/video/document/…)"
)

TEMPLATE_PRESETS: dict[str, str] = {
    "Default (date + ID + name)": "{year}-{month}-{day}_{message_id}_{filename}",
    "Flat (ID only)": "{message_id}_{filename}",
    "By type / date / name": "{type}/{year}-{month}/{message_id}_{filename}",
    "By sender / date": "{sender}/{year}-{month}/{message_id}_{filename}",
    "By year-month / name": "{year}/{month}/{message_id}_{filename}",
}
