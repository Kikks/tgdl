from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DownloadStatus:
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    channel_id   TEXT    NOT NULL,
    message_id   INTEGER NOT NULL,
    file_unique_id TEXT,
    file_hash    TEXT,
    download_path TEXT,
    file_size    INTEGER,
    status       TEXT    NOT NULL,
    downloaded_at TEXT,
    PRIMARY KEY (channel_id, message_id)
);

CREATE TABLE IF NOT EXISTS bandwidth_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id       TEXT    NOT NULL,
    session_date     TEXT    NOT NULL,
    bytes_downloaded INTEGER DEFAULT 0,
    files_downloaded INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_file_hash      ON downloads(file_hash);
CREATE INDEX IF NOT EXISTS idx_file_unique_id ON downloads(file_unique_id);
CREATE INDEX IF NOT EXISTS idx_bw_channel     ON bandwidth_log(channel_id, session_date);
"""


class StateDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> StateDB:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        return self

    def __exit__(self, *_):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── download record helpers ──────────────────────────────────────────

    def get(self, channel_id: str, message_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM downloads WHERE channel_id=? AND message_id=?",
            (channel_id, message_id),
        ).fetchone()

    def is_complete(self, channel_id: str, message_id: int) -> bool:
        row = self.get(channel_id, message_id)
        return row is not None and row["status"] == DownloadStatus.COMPLETE

    def partial_bytes(self, channel_id: str, message_id: int) -> int:
        """Return how many bytes were already saved for a partial download."""
        row = self.get(channel_id, message_id)
        if row and row["status"] == DownloadStatus.PARTIAL and row["download_path"]:
            part = Path(row["download_path"] + ".part")
            if part.exists():
                return part.stat().st_size
        return 0

    def has_unique_id(self, unique_id: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM downloads WHERE file_unique_id=? AND status=?",
                (unique_id, DownloadStatus.COMPLETE),
            ).fetchone()
            is not None
        )

    def has_hash(self, file_hash: str) -> str | None:
        """Return download_path if a file with this hash already exists."""
        row = self._conn.execute(
            "SELECT download_path FROM downloads WHERE file_hash=? AND status=?",
            (file_hash, DownloadStatus.COMPLETE),
        ).fetchone()
        return row["download_path"] if row else None

    def upsert(
        self,
        channel_id: str,
        message_id: int,
        status: str,
        *,
        file_unique_id: str | None = None,
        file_hash: str | None = None,
        download_path: str | None = None,
        file_size: int | None = None,
    ):
        self._conn.execute(
            """
            INSERT INTO downloads
                (channel_id, message_id, file_unique_id, file_hash,
                 download_path, file_size, status, downloaded_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(channel_id, message_id) DO UPDATE SET
                file_unique_id = excluded.file_unique_id,
                file_hash      = excluded.file_hash,
                download_path  = excluded.download_path,
                file_size      = excluded.file_size,
                status         = excluded.status,
                downloaded_at  = excluded.downloaded_at
            """,
            (
                channel_id,
                message_id,
                file_unique_id,
                file_hash,
                download_path,
                file_size,
                status,
                _utcnow().isoformat(),
            ),
        )
        self._conn.commit()

    def clear_channel(self, channel_id: str):
        self._conn.execute("DELETE FROM downloads WHERE channel_id=?", (channel_id,))
        self._conn.commit()

    def all_channels(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT channel_id FROM downloads").fetchall()
        return [r["channel_id"] for r in rows]

    # ── bandwidth helpers ────────────────────────────────────────────────

    def log_bandwidth(self, channel_id: str, bytes_downloaded: int, files: int = 1):
        today = _utcnow().date().isoformat()
        existing = self._conn.execute(
            "SELECT id FROM bandwidth_log WHERE channel_id=? AND session_date=?",
            (channel_id, today),
        ).fetchone()
        if existing:
            self._conn.execute(
                """UPDATE bandwidth_log
                   SET bytes_downloaded = bytes_downloaded + ?,
                       files_downloaded = files_downloaded + ?
                   WHERE id=?""",
                (bytes_downloaded, files, existing["id"]),
            )
        else:
            self._conn.execute(
                "INSERT INTO bandwidth_log (channel_id,session_date,bytes_downloaded,files_downloaded) VALUES(?,?,?,?)",
                (channel_id, today, bytes_downloaded, files),
            )
        self._conn.commit()

    # ── stats ────────────────────────────────────────────────────────────

    def channel_stats(self, channel_id: str) -> dict:
        row = self._conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(file_size) as total_bytes,
                SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) as complete,
                SUM(CASE WHEN status='partial'  THEN 1 ELSE 0 END) as partial,
                SUM(CASE WHEN status='failed'   THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status='skipped'  THEN 1 ELSE 0 END) as skipped
               FROM downloads WHERE channel_id=?""",
            (channel_id,),
        ).fetchone()
        bw = self._conn.execute(
            "SELECT SUM(bytes_downloaded) as bw FROM bandwidth_log WHERE channel_id=?",
            (channel_id,),
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "total_bytes": row["total_bytes"] or 0,
            "complete": row["complete"] or 0,
            "partial": row["partial"] or 0,
            "failed": row["failed"] or 0,
            "skipped": row["skipped"] or 0,
            "total_bandwidth": bw["bw"] or 0,
        }

    def recent_sessions(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._conn.execute(
            """SELECT channel_id, session_date,
                      bytes_downloaded, files_downloaded
               FROM bandwidth_log
               ORDER BY session_date DESC, id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
