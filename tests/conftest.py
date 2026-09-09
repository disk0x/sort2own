"""
Fixtures for the sort2own tests.

Most of the script never reads a file's contents — classify(), destination()
and execute() only care about duration/size/tag — so those tests use sparse
placeholder files, which are instant and need no ffmpeg. Only tests that go
through scan()/ffprobe() need make_rip, which shells out to ffmpeg and skips
when it is not installed.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sort2own  # noqa: E402

# Stand-in bitrate, so a placeholder's size tracks its duration the way a real
# rip's does — duplicate detection compares both.
BYTES_PER_SECOND = 1000


@pytest.fixture
def src_dir(tmp_path):
    d = tmp_path / "rip"
    d.mkdir()
    return d


@pytest.fixture
def library(tmp_path):
    d = tmp_path / "library"
    d.mkdir()
    return d


@pytest.fixture
def make_titles(src_dir):
    """
    Build Title objects backed by sparse placeholder files.

    Each spec is a dict: duration (required), plus optional name, size, tag,
    chapters. Files are named Film_t00.mkv, Film_t01.mkv … like MakeMKV's.
    """
    def _make(specs):
        titles = []
        for i, spec in enumerate(specs):
            duration = float(spec["duration"])
            path = src_dir / spec.get("name", f"Film_t{i:02d}.mkv")
            path.touch()
            os.truncate(path, int(spec.get("size", duration * BYTES_PER_SECOND)))
            titles.append(sort2own.Title(
                path=path,
                duration=duration,
                size=path.stat().st_size,
                chapters=int(spec.get("chapters", 0)),
                tag=spec.get("tag", ""),
            ))
        return titles
    return _make


@pytest.fixture
def make_plan(make_titles, library):
    def _make(specs, name="A Film (2024)", **kw):
        return sort2own.Plan(name=name, library=library,
                             titles=make_titles(specs), **kw)
    return _make


@pytest.fixture
def make_rip(src_dir):
    """Real MKVs via ffmpeg — only for tests that exercise scan()/ffprobe()."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")

    def _make(specs, into=None):
        target = into or src_dir
        target.mkdir(parents=True, exist_ok=True)
        for i, spec in enumerate(specs):
            path = target / spec.get("name", f"Film_t{i:02d}.mkv")
            cmd = ["ffmpeg", "-v", "error", "-y",
                   "-f", "lavfi", "-i", "color=c=black:s=64x36:r=1",
                   "-t", str(spec["duration"]),
                   "-c:v", "libx264", "-preset", "ultrafast"]
            if spec.get("tag"):
                cmd += ["-metadata", f"title={spec['tag']}"]
            subprocess.run(cmd + [str(path)], check=True, capture_output=True)
        return target
    return _make
