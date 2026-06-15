"""The DownloadConfig JSON round-trip is the contract the Raycast extension and
`job start --config` depend on."""

import json

from tgdl.config import DateRangeType, DownloadConfig, MediaType, ResumeMode


def test_defaults_serialize_to_json():
    cfg = DownloadConfig()
    blob = json.dumps(cfg.model_dump(mode="json"))
    again = DownloadConfig.model_validate(json.loads(blob))
    assert again.media_types == list(MediaType)
    assert again.resume_mode == ResumeMode.SMART
    assert again.deduplicate is True


def test_partial_config_takes_defaults():
    cfg = DownloadConfig.model_validate(
        {
            "channel": "@x",
            "media_types": ["photo", "video"],
            "date_range_type": "last_n_days",
            "last_n_days": 7,
        }
    )
    assert cfg.channel == "@x"
    assert cfg.media_types == [MediaType.PHOTO, MediaType.VIDEO]
    assert cfg.date_range_type == DateRangeType.LAST_N_DAYS
    assert cfg.concurrency == 3  # default preserved


def test_extension_shaped_payload():
    # Mirrors what the Raycast form will POST as config.json.
    payload = {
        "channel": "@forEDMproducer",
        "media_types": ["photo"],
        "date_range_type": "last_n_days",
        "last_n_days": 5,
        "output_path": "/tmp/out",
        "concurrency": 3,
    }
    cfg = DownloadConfig.model_validate(payload)
    assert str(cfg.output_path) == "/tmp/out"
