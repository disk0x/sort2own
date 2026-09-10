"""
Copying big files: say what is really happening, and show it happening.

Both of these came from a real run over SMB — the header claimed "hardlink"
for a run that copied every file, and a 32 GB transfer gave no output for
several minutes.
"""

import os

import sort2own


# --- the method reported must be the method used ---------------------------

def test_same_filesystem_keeps_the_hardlink(tmp_path, monkeypatch):
    monkeypatch.setattr(sort2own, "same_filesystem", lambda a, b: True)
    assert sort2own.resolve_method(tmp_path, tmp_path, "hardlink", True) == "hardlink"


def test_accepting_the_fallback_reports_copy_not_hardlink(tmp_path, monkeypatch,
                                                          capsys):
    """
    The bug: after consenting, every file was copied but the run still
    announced "method: hardlink".
    """
    monkeypatch.setattr(sort2own, "same_filesystem", lambda a, b: False)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    assert sort2own.resolve_method(tmp_path, tmp_path, "hardlink", True) == "copy"


def test_declining_the_fallback_aborts(tmp_path, monkeypatch):
    monkeypatch.setattr(sort2own, "same_filesystem", lambda a, b: False)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert sort2own.resolve_method(tmp_path, tmp_path, "hardlink", True) is None


def test_unattended_never_falls_back_on_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(sort2own, "same_filesystem", lambda a, b: False)
    assert sort2own.resolve_method(tmp_path, tmp_path, "hardlink", False) is None


def test_an_explicit_copy_is_not_second_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(sort2own, "same_filesystem", lambda a, b: False)
    assert sort2own.resolve_method(tmp_path, tmp_path, "copy", False) == "copy"


def test_the_run_header_says_copy_after_consent(make_rip, src_dir, library,
                                                monkeypatch, capsys):
    """End to end: consent to the fallback, and the header must not lie."""
    make_rip([{"duration": 4}])
    monkeypatch.setattr(sort2own, "same_filesystem", lambda a, b: False)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    # An interactive run without a usable terminal: take the consent path but
    # skip the curses editor.
    monkeypatch.setattr(sort2own.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sort2own.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sort2own, "run_tui", lambda plan: True)

    sort2own.main([str(src_dir), "--library", str(library),
                   "--name", "A Film (2024)", "--min-extra", "1"])
    out = capsys.readouterr().out
    assert "method: copy" in out
    assert "method: hardlink" not in out


# --- copying ---------------------------------------------------------------

def test_a_copy_is_faithful(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(bytes(range(256)) * 5000)
    sort2own.copy_file(src, dst, progress=False)
    assert dst.read_bytes() == src.read_bytes()
    assert dst.stat().st_mode == src.stat().st_mode


def test_an_empty_file_copies(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.touch()
    sort2own.copy_file(src, dst, progress=False)
    assert dst.is_file() and dst.stat().st_size == 0


def test_a_quick_copy_draws_no_progress(tmp_path, capsys):
    """Nothing should flicker for a file that copies instantly."""
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"small")
    sort2own.copy_file(src, dst, progress=True)
    assert capsys.readouterr().err == ""


def test_progress_reports_size_rate_and_time_left():
    line = sort2own.progress_line("Film.mkv", 8 * 2**30, 32 * 2**30, 100.0)
    assert "8.00/32.00 GiB" in line
    assert "25%" in line
    assert "MiB/s" in line
    assert "left" in line


def test_progress_does_not_divide_by_zero():
    """An empty file never reaches the drawing threshold, but must not crash."""
    assert "0.00/0.00 GiB" in sort2own.progress_line("x.mkv", 0, 0, 0.0)


# --- move ------------------------------------------------------------------

def test_a_move_within_one_filesystem_is_a_rename(tmp_path):
    src, dst = tmp_path / "a.mkv", tmp_path / "out" / "b.mkv"
    src.write_bytes(b"payload")
    assert sort2own.place(src, dst, "move") == "move"
    assert dst.read_bytes() == b"payload"
    assert not src.exists()


def test_a_move_across_filesystems_copies_then_removes(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.mkv", tmp_path / "out" / "b.mkv"
    src.write_bytes(b"payload")

    def no_rename(*args, **kwargs):
        raise OSError("cross-device link")
    monkeypatch.setattr(sort2own.os, "rename", no_rename)

    assert sort2own.place(src, dst, "move") == "move"
    assert dst.read_bytes() == b"payload"
    assert not src.exists()


# --- destinations that cannot take metadata --------------------------------

def test_a_share_that_refuses_chmod_does_not_lose_the_copy(tmp_path,
                                                           monkeypatch):
    """
    Regression: gvfs/SMB rejects chmod with errno 95, shutil.copystat raised,
    and the run died *after* 31 GB had been transferred.
    """
    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"payload" * 1000)

    def refuse(*args, **kwargs):
        raise OSError(95, "Operation not supported")
    monkeypatch.setattr(sort2own.shutil, "copystat", refuse)

    sort2own.copy_file(src, dst, progress=False)
    assert dst.read_bytes() == src.read_bytes()


def test_timestamps_are_still_carried_over_when_chmod_fails(tmp_path,
                                                            monkeypatch):
    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"payload")
    os.utime(src, (1_000_000, 1_000_000))

    def refuse(*args, **kwargs):
        raise OSError(95, "Operation not supported")
    monkeypatch.setattr(sort2own.shutil, "copystat", refuse)

    sort2own.copy_file(src, dst, progress=False)
    assert int(dst.stat().st_mtime) == 1_000_000


def test_a_destination_refusing_everything_still_keeps_the_bytes(tmp_path,
                                                                 monkeypatch):
    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"payload")

    def refuse(*args, **kwargs):
        raise OSError(95, "Operation not supported")
    monkeypatch.setattr(sort2own.shutil, "copystat", refuse)
    monkeypatch.setattr(sort2own.os, "utime", refuse)

    sort2own.copy_file(src, dst, progress=False)
    assert dst.read_bytes() == b"payload"


def test_a_whole_run_survives_a_share_that_refuses_chmod(make_rip, src_dir,
                                                         library, monkeypatch):
    make_rip([{"duration": 4}, {"duration": 2, "tag": "Trailer"}])

    def refuse(*args, **kwargs):
        raise OSError(95, "Operation not supported")
    monkeypatch.setattr(sort2own.shutil, "copystat", refuse)

    assert sort2own.main([str(src_dir), "--library", str(library), "--yes",
                          "--copy", "--min-extra", "1",
                          "--name", "A Film (2024)"]) == 0
    assert (library / "A Film (2024)" / "A Film (2024).mkv").exists()
