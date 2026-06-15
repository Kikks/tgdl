from tgdl.organizer import _safe, unique_dest


def test_safe_strips_invalid_chars():
    assert _safe("a/b:c*d") == "a_b_c_d"
    assert _safe("  ...  ") == "unnamed"
    assert _safe("normal name") == "normal name"


def test_unique_dest_avoids_collision(tmp_path):
    p = tmp_path / "file.jpg"
    p.write_text("x")
    assert unique_dest(p) == tmp_path / "file_1.jpg"
    (tmp_path / "file_1.jpg").write_text("y")
    assert unique_dest(p) == tmp_path / "file_2.jpg"


def test_unique_dest_passthrough_when_free(tmp_path):
    p = tmp_path / "free.jpg"
    assert unique_dest(p) == p
