"""
--reuse-from: sorting one rip into a second library without answering the
same questions again.
"""

import json

import sort2own
from sort2own import EPISODE, EXTRA, MAIN, SKIP, VERSION


def sorted_once(make_plan, library=None, **kw):
    """Place a rip, so its manifest holds a set of decisions."""
    plan = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"},
                      {"duration": 137}], **kw)
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    if library is not None:
        plan.library = library
    # A choice the heuristics would not have made on their own.
    plan.titles[2].extra_type, plan.titles[2].label = "interviews", "Am Set"
    sort2own.execute(plan, "copy", False)
    return plan, sort2own.library_root(plan)


# --- what gets recorded ----------------------------------------------------

def test_the_manifest_records_the_type_and_name_outright(make_plan):
    plan, folder = sorted_once(make_plan)
    run = json.loads((folder / sort2own.MANIFEST_NAME).read_text())[0]
    chosen = next(a for a in run["actions"] if a["label"] == "Am Set")
    assert chosen["extra_type"] == "interviews"


def test_skipped_titles_are_recorded_too(make_plan):
    plan = make_plan([{"duration": 7200}, {"duration": 30}])
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    sort2own.execute(plan, "copy", False)
    run = json.loads((sort2own.library_root(plan)
                      / sort2own.MANIFEST_NAME).read_text())[0]
    assert [s["kind"] for s in run["skipped"]] == [SKIP]


def test_undo_ignores_the_skipped_list(make_plan):
    """It records what was *not* done; undo must not try to remove any of it."""
    plan = make_plan([{"duration": 7200}, {"duration": 30}])
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    sort2own.execute(plan, "copy", False)
    assert sort2own.undo(sort2own.library_root(plan), None, False, False) == 0
    assert plan.titles[1].path.exists()          # the skipped file is untouched


# --- reading them back -----------------------------------------------------

def test_decisions_carry_into_a_second_library(make_plan, tmp_path):
    plan, folder = sorted_once(make_plan)

    second = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"},
                        {"duration": 137}])
    second.library = tmp_path / "other-library"
    sort2own.classify(second, None, 90, 0.85, 0.01)
    assert sort2own.reuse_decisions(second, folder) == 3

    assert second.titles[0].kind == MAIN
    assert second.titles[2].extra_type == "interviews"
    assert second.titles[2].label == "Am Set"
    assert second.titles[2].note == "as decided in an earlier run"


def test_a_reused_title_is_no_longer_flagged(make_plan, tmp_path):
    plan, folder = sorted_once(make_plan)
    second = make_plan([{"duration": 137}])
    sort2own.classify(second, None, 90, 0.85, 0.01)
    second.titles[0].suggestion = "OFDb: could be A / B"
    second.titles[0].candidates = ["A", "B"]

    sort2own.reuse_decisions(second, folder)
    assert second.titles[0].suggestion == ""
    assert second.titles[0].candidates == []


def test_titles_the_earlier_run_never_saw_are_left_alone(make_plan, tmp_path):
    plan, folder = sorted_once(make_plan)
    # A genuinely different title: its own file, its own length.
    second = make_plan([{"duration": 4242, "name": "Other_t00.mkv"}])
    sort2own.classify(second, None, 90, 0.85, 0.01)
    assert sort2own.reuse_decisions(second, folder) == 0


def test_nothing_to_reuse_is_not_an_error(make_plan, tmp_path):
    plan = make_plan([{"duration": 7200}])
    assert sort2own.reuse_decisions(plan, tmp_path / "empty") == 0


# --- manifests written before this feature ---------------------------------

def test_an_older_manifest_is_read_back_off_the_paths(make_plan, tmp_path):
    """
    Manifests predating this store no label or extra_type — but the path is
    the decision: the folder is the type, the file name is the label.
    """
    plan = make_plan([{"duration": 137}])
    folder = sort2own.library_root(plan)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / sort2own.MANIFEST_NAME).write_text(json.dumps([{
        "when": "2026-09-09T12:00:00", "name": plan.name, "tv": False,
        "actions": [{
            "source": str(plan.titles[0].path),
            "destination": str(folder / "behind the scenes" / "Behind the Scenes.mkv"),
            "kind": EXTRA, "method": "copy",
            "size": plan.titles[0].size, "duration": plan.titles[0].duration,
        }],
    }]))

    second = make_plan([{"duration": 137}])
    sort2own.classify(second, None, 90, 0.85, 0.01)
    assert sort2own.reuse_decisions(second, folder) == 1
    assert second.titles[0].extra_type == "behind the scenes"
    assert second.titles[0].label == "Behind the Scenes"


def test_an_older_version_keeps_its_label(make_plan):
    action = {"kind": VERSION, "destination":
              "/lib/A Film (2024)/A Film (2024) - Director's Cut.mkv"}
    assert sort2own.recorded_naming(action) == ("Director's Cut", "")


def test_an_older_episode_keeps_its_number(make_plan):
    action = {"kind": EPISODE,
              "destination": "/lib/Show (2017)/Season 01/Show (2017) S01E07.mkv"}
    assert sort2own.recorded_naming(action) == ("7", "")


def test_explicit_fields_win_over_the_path(make_plan):
    action = {"kind": EXTRA, "label": "Chosen", "extra_type": "interviews",
              "destination": "/lib/Film/trailers/Something Else.mkv"}
    assert sort2own.recorded_naming(action) == ("Chosen", "interviews")


# --- end to end ------------------------------------------------------------

def test_a_second_library_needs_no_further_answers(make_rip, src_dir, library,
                                                   tmp_path):
    make_rip([{"duration": 20}, {"duration": 4, "tag": "Trailer"}])
    args = [str(src_dir), "--yes", "--copy", "--min-extra", "3",
            "--name", "A Film (2024)"]
    assert sort2own.main(args + ["--library", str(library)]) == 0

    first = library / "A Film (2024)"
    elsewhere = tmp_path / "second"
    assert sort2own.main(args + ["--library", str(elsewhere),
                                 "--reuse-from", str(first)]) == 0
    assert sorted(p.relative_to(elsewhere).as_posix()
                  for p in elsewhere.rglob("*.mkv")) == sorted(
        p.relative_to(library).as_posix() for p in library.rglob("*.mkv"))
