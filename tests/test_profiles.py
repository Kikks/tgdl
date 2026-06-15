import tgdl.profiles as profiles
from tgdl.config import DownloadConfig
from tgdl.profiles import delete_profile, list_profiles, load_profile, save_profile


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles")
    cfg = DownloadConfig(channel="@x", last_n_days=5)

    save_profile("p", cfg, quiet=True)
    assert "p" in list_profiles()

    loaded = load_profile("p")
    assert loaded.channel == "@x"
    assert loaded.last_n_days == 5

    delete_profile("p")
    assert "p" not in list_profiles()
