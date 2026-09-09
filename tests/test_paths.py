"""A network share has to be mounted; a URL is not a path."""

from pathlib import Path

import pytest

import sort2own


@pytest.mark.parametrize("url", [
    "smb://nas.example/movies",
    "nfs://nas/export/films",
    "ftp://host/share",
    "file:///mnt/movies",
    "sftp://nas/movies",
])
def test_a_url_is_refused_rather_than_silently_resolved(url):
    """
    pathlib treats smb://nas/movies as a *relative* path, so without this it
    resolves against the working directory and the rip lands in a folder
    literally named "smb:".
    """
    with pytest.raises(SystemExit) as e:
        sort2own.local_path(Path(url), "--library")
    assert "looks like a URL" in str(e.value)
    assert "mounted" in str(e.value)


@pytest.mark.parametrize("good", [
    "/mnt/movies",
    "/media/My: Films",          # a colon inside a name is not a scheme
    "relative/dir",
    "~",
    # gvfs mounts a share at a real path whose name contains "smb-share:" —
    # a genuine filesystem path that must not be mistaken for a URL.
    "/run/user/1000/gvfs/smb-share:server=nas.example,share=media/media/movies",
])
def test_ordinary_paths_pass_through(good):
    assert isinstance(sort2own.local_path(Path(good), "--library"), Path)


def test_the_path_comes_back_expanded_and_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sort2own.local_path(Path("~/films"), "--library") == tmp_path / "films"


def test_a_url_library_stops_the_run(make_rip, src_dir):
    make_rip([{"duration": 4}])
    with pytest.raises(SystemExit):
        sort2own.main([str(src_dir), "--library", "smb://nas.example/movies",
                       "--yes", "--name", "A Film (2024)", "--copy"])


def test_a_url_undo_target_stops_the_run():
    with pytest.raises(SystemExit):
        sort2own.main(["--undo", "smb://nas.example/movies/A Film (2024)"])
