"""Handing a title to the desktop's media player, so the user can look at it."""

import subprocess

import sort2own


def test_the_file_goes_to_the_desktop_opener(tmp_path, monkeypatch):
    seen = {}

    def record(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
    monkeypatch.setattr(sort2own.shutil, "which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(sort2own.subprocess, "Popen", record)

    film = tmp_path / "Film_t03.mkv"
    assert sort2own.open_in_player(film) is None
    assert seen["argv"] == ["/usr/bin/xdg-open", str(film)]


def test_the_player_is_detached_and_silenced(tmp_path, monkeypatch):
    """
    It must not draw on the terminal the TUI owns, and it has to outlive the
    keypress that started it.
    """
    seen = {}
    monkeypatch.setattr(sort2own.shutil, "which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(sort2own.subprocess, "Popen",
                        lambda argv, **kw: seen.update(kw))

    sort2own.open_in_player(tmp_path / "x.mkv")
    assert seen["stdout"] is subprocess.DEVNULL
    assert seen["stderr"] is subprocess.DEVNULL
    assert seen["start_new_session"] is True


def test_no_opener_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(sort2own.shutil, "which", lambda name: None)
    assert "xdg-open" in sort2own.open_in_player(tmp_path / "x.mkv")


def test_a_failing_launch_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(sort2own.shutil, "which", lambda name: "/usr/bin/xdg-open")

    def explode(argv, **kwargs):
        raise OSError("no display")
    monkeypatch.setattr(sort2own.subprocess, "Popen", explode)

    assert sort2own.open_in_player(tmp_path / "x.mkv") == "no display"
