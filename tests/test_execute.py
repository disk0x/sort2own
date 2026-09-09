"""Placement and the manifest — the invariants everything else builds on."""

import json
from pathlib import Path

import sort2own
from sort2own import MAIN, EXTRA, SKIP


def placed(plan, mode="copy", dry_run=False):
    sort2own.execute(plan, mode, dry_run)
    return plan.library / sort2own.safe_name(plan.name)


def a_plan(make_plan):
    plan = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"},
                      {"duration": 30}])
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    plan.titles[1].extra_type = "trailers"
    return plan


def test_dry_run_writes_nothing(make_plan, library):
    plan = a_plan(make_plan)
    placed(plan, dry_run=True)
    assert list(library.iterdir()) == []


def test_copy_places_every_kind_where_it_belongs(make_plan):
    plan = a_plan(make_plan)
    root = placed(plan)
    assert (root / "A Film (2024).mkv").exists()
    assert (root / "trailers" / "Trailer.mkv").exists()


def test_skipped_titles_are_left_in_place(make_plan):
    plan = a_plan(make_plan)
    root = placed(plan)
    junk = plan.titles[2]
    assert junk.kind == SKIP
    assert junk.path.exists()
    assert not list(root.rglob("Extra*.mkv"))


def test_source_directory_is_never_modified(make_plan, src_dir):
    plan = a_plan(make_plan)
    before = sorted(p.name for p in src_dir.iterdir())
    placed(plan)
    assert sorted(p.name for p in src_dir.iterdir()) == before


def test_hardlink_shares_the_inode_with_the_source(make_plan):
    plan = a_plan(make_plan)
    root = placed(plan, mode="hardlink")
    main = next(t for t in plan.titles if t.kind == MAIN)
    assert (root / "A Film (2024).mkv").samefile(main.path)


def test_move_relocates_the_source(make_plan):
    plan = a_plan(make_plan)
    main = next(t for t in plan.titles if t.kind == MAIN)
    root = placed(plan, mode="move")
    assert not main.path.exists()
    assert (root / "A Film (2024).mkv").exists()


def test_manifest_records_one_action_per_placed_title(make_plan):
    plan = a_plan(make_plan)
    root = placed(plan)
    runs = json.loads((root / sort2own.MANIFEST_NAME).read_text())
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "A Film (2024)"
    assert run["tv"] is False
    assert run["when"]
    kinds = [a["kind"] for a in run["actions"]]
    assert kinds == [MAIN, EXTRA]          # the skipped title is not recorded
    for action in run["actions"]:
        assert action["method"] == "copy"
        assert Path(action["source"]).exists()
        assert Path(action["destination"]).exists()


def test_a_second_run_appends_to_the_manifest(make_plan):
    root = placed(a_plan(make_plan))
    placed(a_plan(make_plan))
    assert len(json.loads((root / sort2own.MANIFEST_NAME).read_text())) == 2


def test_existing_destinations_are_never_overwritten(make_plan):
    root = placed(a_plan(make_plan))
    main = root / "A Film (2024).mkv"
    main.write_bytes(b"do not clobber")
    placed(a_plan(make_plan))
    assert main.read_bytes() == b"do not clobber"
    assert (root / "A Film (2024) (2).mkv").exists()
