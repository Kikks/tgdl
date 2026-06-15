from tgdl.filters import _caption_matches, parse_size


def test_parse_size_units():
    assert parse_size("100KB") == 100 * 1024
    assert parse_size("2.5MB") == int(2.5 * 1024**2)
    assert parse_size("1GB") == 1024**3
    assert parse_size("512") == 512


def test_caption_plain_substring_case_insensitive():
    assert _caption_matches("Hello World", "world")
    assert not _caption_matches("Hello", "world")


def test_caption_regex():
    assert _caption_matches("invoice #4021", r"/#\d+/")
    assert not _caption_matches("no numbers here", r"/#\d+/")
