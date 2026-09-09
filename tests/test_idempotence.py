"""Item 4 — running the script twice on the same rip must be a no-op."""

import json

import sort2own
from sort2own import MAIN, EXTRA, SKIP


def a_plan(make_plan, **kw):
    plan = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"},
                      {"duration": 30}], **kw)
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    return plan


def write_manifest(folder, actions, **run):
    folder.mkdir(parents=True, exist_ok=True)
    run = {"when": "2026-09-09T12:00:00", "name": "A Film (2024)",
           "tv": False, "actions": actions, **run}
    (folder / sort2own.MANIFEST_NAME).write_text(json.dumps([run]))


# --- load_manifest ---------------------------------------------------------

def test_missing_manifest_is_empty_not_an_error(tmp_path):
    assert sort2own.load_manifest(tmp_path) == []


def test_corrupt_manifest_warns_and_degrades_to_empty(tmp_path, capsys):
    (tmp_path / sort2own.MANIFEST_NAME).write_text("{ this is not json")
    assert sort2own.load_manifest(tmp_path) == []
    assert "cannot read" in capsys.readouterr().err


def test_manifest_that_is_not_a_list_degrades_to_empty(tmp_path, capsys):
    (tmp_path / sort2own.MANIFEST_NAME).write_text('{"runs": []}')
    assert sort2own.load_manifest(tmp_path) == []
    assert "not a list" in capsys.readouterr().err


# --- matching --------------------------------------------------------------

def test_same_file_is_recognised_by_inode(make_plan):
    plan = a_plan(make_plan)
    main = plan.titles[0]
    (plan.library / "x.mkv").touch()          # the placed file is still there
    write_manifest(sort2own.library_root(plan), [{
        "source": str(main.path), "destination": str(plan.library / "x.mkv"),
        "kind": MAIN, "method": "hardlink", "size": 1, "duration": 1,
    }])
    assert sort2own.mark_already_placed(plan) == 1
    assert main.kind == SKIP
    assert main.note == "already placed as x.mkv"


def test_a_re_rip_elsewhere_is_recognised_by_size_and_duration(make_plan,
                                                              tmp_path):
    """New path, new inode, identical bytes — the fallback key."""
    plan = a_plan(make_plan)
    main = plan.titles[0]
    (plan.library / "A Film (2024).mkv").touch()
    write_manifest(sort2own.library_root(plan), [{
        "source": str(tmp_path / "gone" / "Film_t00.mkv"),
        "destination": str(plan.library / "A Film (2024).mkv"),
        "kind": MAIN, "method": "copy",
        "size": main.size, "duration": main.duration,
    }])
    assert sort2own.mark_already_placed(plan) == 1
    assert main.kind == SKIP


def test_a_different_title_is_left_alone(make_plan, tmp_path):
    plan = a_plan(make_plan)
    write_manifest(sort2own.library_root(plan), [{
        "source": str(tmp_path / "gone.mkv"), "destination": "/x.mkv",
        "kind": MAIN, "method": "copy", "size": 999_999, "duration": 999_999,
    }])
    assert sort2own.mark_already_placed(plan) == 0
    assert plan.titles[0].kind == MAIN


def test_old_manifests_without_size_still_work(make_plan, tmp_path):
    """Records written before this feature lack size/duration."""
    plan = a_plan(make_plan)
    write_manifest(sort2own.library_root(plan), [{
        "source": str(tmp_path / "gone.mkv"), "destination": "/x.mkv",
        "kind": MAIN, "method": "copy",
    }])
    assert sort2own.mark_already_placed(plan) == 0
    assert plan.titles[0].kind == MAIN


def test_classify_s_own_skip_reason_is_preserved(make_plan):
    plan = a_plan(make_plan)
    junk = plan.titles[2]
    write_manifest(sort2own.library_root(plan), [{
        "source": str(junk.path), "destination": "/x.mkv", "kind": EXTRA,
        "method": "copy", "size": junk.size, "duration": junk.duration,
    }])
    sort2own.mark_already_placed(plan)
    assert junk.note == "shorter than 90s"


def test_no_manifest_means_nothing_is_marked(make_plan):
    plan = a_plan(make_plan)
    assert sort2own.mark_already_placed(plan) == 0


def test_a_recorded_placement_whose_file_is_gone_does_not_count(make_plan):
    """
    "Already placed" has to mean the file is still there. Delete an extra and
    the manifest still records it; without checking, the title would be
    skipped forever and never restored.
    """
    plan = a_plan(make_plan)
    main = plan.titles[0]
    write_manifest(sort2own.library_root(plan), [{
        "source": str(main.path),
        "destination": str(plan.library / "deleted-since.mkv"),
        "kind": MAIN, "method": "copy",
        "size": main.size, "duration": main.duration,
    }])
    assert sort2own.mark_already_placed(plan) == 0
    assert main.kind == MAIN


# --- end to end ------------------------------------------------------------

def run(src, library, *extra):
    return sort2own.main([str(src), "--library", str(library), "--yes",
                          "--name", "Test Film (2024)", "--min-extra", "3",
                          "--copy", *extra])


def test_running_twice_places_nothing_the_second_time(make_rip, src_dir,
                                                      library):
    make_rip([{"duration": 20}, {"duration": 4, "tag": "Trailer"}])
    assert run(src_dir, library) == 0
    before = sorted(p.name for p in library.rglob("*.mkv"))

    assert run(src_dir, library) == 0
    assert sorted(p.name for p in library.rglob("*.mkv")) == before
    assert not list(library.rglob("* (2).mkv"))

    runs = json.loads((library / "Test Film (2024)"
                       / sort2own.MANIFEST_NAME).read_text())
    assert len(runs) == 2
    assert runs[1]["actions"] == []


def test_a_deleted_extra_is_restored_while_the_rest_is_left_alone(make_rip,
                                                                  src_dir,
                                                                  library):
    """The realistic repair case: the feature is fine, an extra went missing."""
    make_rip([{"duration": 20}, {"duration": 4, "tag": "Trailer"}])
    run(src_dir, library)
    folder = library / "Test Film (2024)"
    main = folder / "Test Film (2024).mkv"
    placed_before = main.stat().st_mtime_ns
    (folder / "trailers" / "Trailer.mkv").unlink()

    assert run(src_dir, library) == 0
    assert (folder / "trailers" / "Trailer.mkv").exists()   # back
    assert main.stat().st_mtime_ns == placed_before         # untouched
    assert not list(folder.rglob("* (2).mkv"))              # not duplicated
    make_rip([{"duration": 20}, {"duration": 4, "tag": "Trailer"}])
    run(src_dir, library)
    run(src_dir, library, "--force")
    assert list(library.rglob("* (2).mkv"))


def test_the_skip_is_reported(make_rip, src_dir, library, capsys):
    make_rip([{"duration": 20}])
    run(src_dir, library)
    capsys.readouterr()
    run(src_dir, library)
    out = capsys.readouterr().out
    assert "already placed by an earlier run" in out
    assert "already placed as Test Film (2024)/Test Film (2024).mkv" in out


def test_a_corrupt_manifest_does_not_stop_a_run(make_rip, src_dir, library):
    make_rip([{"duration": 20}])
    run(src_dir, library)
    (library / "Test Film (2024)"
     / sort2own.MANIFEST_NAME).write_text("truncated…")
    assert run(src_dir, library) == 0
    assert list(library.rglob("* (2).mkv"))    # history lost, so it re-places


def test_a_corrupt_manifest_is_kept_not_overwritten(make_rip, src_dir,
                                                    library):
    make_rip([{"duration": 20}])
    run(src_dir, library)
    folder = library / "Test Film (2024)"
    (folder / sort2own.MANIFEST_NAME).write_text("truncated…")
    run(src_dir, library)
    kept = folder / (sort2own.MANIFEST_NAME + ".corrupt")
    assert kept.read_text() == "truncated…"
    assert json.loads((folder / sort2own.MANIFEST_NAME).read_text())


def test_a_second_corrupt_manifest_does_not_clobber_the_first(make_rip,
                                                              src_dir, library):
    make_rip([{"duration": 20}])
    run(src_dir, library)
    folder = library / "Test Film (2024)"
    for text in ("first breakage", "second breakage"):
        (folder / sort2own.MANIFEST_NAME).write_text(text)
        run(src_dir, library)
    assert (folder / (sort2own.MANIFEST_NAME
                      + ".corrupt")).read_text() == "first breakage"
    assert (folder / (sort2own.MANIFEST_NAME
                      + " (2).corrupt")).read_text() == "second breakage"
