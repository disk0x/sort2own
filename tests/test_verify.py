"""Item 3 — a copy that silently went wrong must not be left in the library."""

import json

import pytest

import sort2own
from sort2own import MAIN


# --- sample_hash -----------------------------------------------------------

def test_identical_files_hash_alike(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for p in (a, b):
        p.write_bytes(b"abc" * 1000)
    assert sort2own.sample_hash(a) == sort2own.sample_hash(b)


def test_truncation_changes_the_hash(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 5000)
    b.write_bytes(b"x" * 4999)
    assert sort2own.sample_hash(a) != sort2own.sample_hash(b)


def test_a_change_inside_a_sampled_block_is_caught(tmp_path):
    """The blocks are spread through the file, not bunched at the start."""
    size = sort2own.SAMPLE_BLOCK * sort2own.SAMPLE_POINTS * 3
    inside = size - sort2own.SAMPLE_BLOCK // 2          # within the last block
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(bytes(size))
    data = bytearray(size)
    data[inside:inside + 16] = b"corruption here!"
    b.write_bytes(bytes(data))
    assert a.stat().st_size == b.stat().st_size
    assert sort2own.sample_hash(a) != sort2own.sample_hash(b)


def test_sampling_can_miss_a_change_between_the_blocks(tmp_path):
    """
    The documented tradeoff: sampling reads a few MiB rather than the whole
    file, so a change in an unsampled gap slips through. --verify full is
    there for when that matters.
    """
    size = sort2own.SAMPLE_BLOCK * sort2own.SAMPLE_POINTS * 3
    gap = sort2own.SAMPLE_BLOCK * 2                     # between blocks 1 and 2
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(bytes(size))
    data = bytearray(size)
    data[gap:gap + 16] = b"corruption here!"
    b.write_bytes(bytes(data))
    assert sort2own.sample_hash(a) == sort2own.sample_hash(b)
    assert sort2own.sample_hash(a, full=True) != sort2own.sample_hash(b, full=True)


# --- verify_copy -----------------------------------------------------------

def test_a_good_copy_passes(tmp_path):
    src, dst = tmp_path / "s", tmp_path / "d"
    src.write_bytes(b"payload" * 100)
    dst.write_bytes(src.read_bytes())
    problem, digest = sort2own.verify_copy(src, dst, src.stat().st_size, False)
    assert problem is None
    assert digest == sort2own.sample_hash(src)


def test_a_truncated_copy_is_reported(tmp_path):
    src, dst = tmp_path / "s", tmp_path / "d"
    src.write_bytes(b"payload" * 100)
    dst.write_bytes(b"payl")
    problem, _ = sort2own.verify_copy(src, dst, src.stat().st_size, False)
    assert "expected" in problem


def test_a_same_size_but_different_copy_is_reported(tmp_path):
    src, dst = tmp_path / "s", tmp_path / "d"
    src.write_bytes(b"a" * 500)
    dst.write_bytes(b"b" * 500)
    problem, _ = sort2own.verify_copy(src, dst, src.stat().st_size, False)
    assert problem == "copy does not match the source"


# --- execute ---------------------------------------------------------------

def a_plan(make_plan):
    plan = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"}])
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    return plan


def break_copies(monkeypatch, when=lambda dst: True):
    """
    Make place() succeed but leave a short file behind — the realistic SMB
    failure, which raises nothing. Returns the real place() so a later run
    can be restored.
    """
    real = sort2own.place

    def broken(src, dst, mode):
        used = real(src, dst, mode)
        if when(dst):
            dst.write_bytes(b"half a file")
        return used

    monkeypatch.setattr(sort2own, "place", broken)
    return real


def test_a_sound_copy_is_recorded_with_its_fingerprint(make_plan):
    plan = a_plan(make_plan)
    assert sort2own.execute(plan, "copy", False) == sort2own.Outcome(placed=2)
    runs = json.loads((sort2own.library_root(plan)
                       / sort2own.MANIFEST_NAME).read_text())
    for action in runs[0]["actions"]:
        assert len(action["sample_hash"]) == 32
        assert action["full_hash"] is False


def test_hardlinks_are_fingerprinted_too(make_plan):
    """Undo needs a hash for every method, not just copies."""
    plan = a_plan(make_plan)
    sort2own.execute(plan, "hardlink", False)
    runs = json.loads((sort2own.library_root(plan)
                       / sort2own.MANIFEST_NAME).read_text())
    assert all(a["sample_hash"] for a in runs[0]["actions"])


def test_a_bad_copy_is_removed_and_counted(make_plan, monkeypatch):
    plan = a_plan(make_plan)
    break_copies(monkeypatch, when=lambda dst: dst.name.startswith("A Film"))
    outcome = sort2own.execute(plan, "copy", False)
    assert (outcome.failures, outcome.placed) == (1, 1)

    root = sort2own.library_root(plan)
    assert not (root / "A Film (2024).mkv").exists()
    assert (root / "extras" / "Trailer.mkv").exists()      # the good one stayed
    assert plan.titles[0].path.exists()                    # source untouched

    runs = json.loads((root / sort2own.MANIFEST_NAME).read_text())
    assert [a["kind"] for a in runs[0]["actions"]] == ["extra"]


def test_a_failed_copy_makes_main_exit_non_zero(make_rip, src_dir, library,
                                                monkeypatch):
    make_rip([{"duration": 20}])
    break_copies(monkeypatch)
    assert sort2own.main([str(src_dir), "--library", str(library), "--yes",
                          "--name", "Test Film (2024)", "--copy"]) == 1


def test_a_failed_copy_is_retried_on_the_next_run(make_rip, src_dir, library,
                                                  monkeypatch):
    """It never reached the manifest, so idempotence must not skip it."""
    make_rip([{"duration": 20}])
    args = [str(src_dir), "--library", str(library), "--yes",
            "--name", "Test Film (2024)", "--copy"]
    real_place = break_copies(monkeypatch)
    assert sort2own.main(args) == 1

    monkeypatch.setattr(sort2own, "place", real_place)
    assert sort2own.main(args) == 0
    assert (library / "Test Film (2024)" / "Test Film (2024).mkv").exists()


@pytest.mark.parametrize("flag,expected", [("sample", False), ("full", True)])
def test_verify_full_is_recorded(make_rip, src_dir, library, flag, expected):
    make_rip([{"duration": 20}])
    sort2own.main([str(src_dir), "--library", str(library), "--yes", "--copy",
                   "--name", "Test Film (2024)", "--verify", flag])
    runs = json.loads((library / "Test Film (2024)"
                       / sort2own.MANIFEST_NAME).read_text())
    assert runs[0]["actions"][0]["full_hash"] is expected
