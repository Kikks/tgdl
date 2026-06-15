from tgdl.state import DownloadStatus, StateDB


def test_upsert_and_completeness(tmp_path):
    with StateDB(tmp_path / "s.db") as db:
        db.upsert(
            "chan",
            1,
            DownloadStatus.COMPLETE,
            file_unique_id="u1",
            file_hash="h1",
            download_path="/x/a.jpg",
            file_size=10,
        )
        assert db.is_complete("chan", 1)
        assert not db.is_complete("chan", 2)
        assert db.has_unique_id("u1")
        assert db.has_hash("h1") == "/x/a.jpg"


def test_clear_channel(tmp_path):
    with StateDB(tmp_path / "s.db") as db:
        db.upsert("chan", 1, DownloadStatus.COMPLETE)
        db.clear_channel("chan")
        assert not db.is_complete("chan", 1)
        assert db.all_channels() == []


def test_bandwidth_accumulates(tmp_path):
    with StateDB(tmp_path / "s.db") as db:
        db.log_bandwidth("chan", 100)
        db.log_bandwidth("chan", 50)
        stats = db.channel_stats("chan")
        assert stats["total_bandwidth"] == 150
