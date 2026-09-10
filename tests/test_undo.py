"""
Item 2 — undo. The only code in the tool that deletes, so most of these
tests are about what it refuses to touch.
"""

import json

import pytest

import sort2own


def sorted_rip(make_plan, mode="copy", **kw):
    """Place a small rip and return (plan, folder)."""
    plan = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"}],
                     **kw)
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    plan.titles[1].extra_type = "trailers"
    sort2own.execute(plan, mode, False)
    return plan, sort2own.library_root(plan)


def runs_of(folder):
    return json.loads((folder / sort2own.MANIFEST_NAME).read_text())


# --- the happy path --------------------------------------------------------

def test_undo_removes_what_it_placed_and_the_empty_folder(make_plan, library):
    plan, folder = sorted_rip(make_plan)
    assert sort2own.undo(folder, None, False, False) == 0
    assert not folder.exists()
    assert list(library.iterdir()) == []


def test_the_sources_survive_an_undo(make_plan, src_dir):
    plan, folder = sorted_rip(make_plan)
    before = sorted(p.name for p in src_dir.iterdir())
    sort2own.undo(folder, None, False, False)
    assert sorted(p.name for p in src_dir.iterdir()) == before


def test_undo_of_hardlinks(make_plan):
    plan, folder = sorted_rip(make_plan, mode="hardlink")
    assert sort2own.undo(folder, None, False, False) == 0
    assert not folder.exists()


def test_a_move_is_put_back(make_plan, src_dir):
    plan, folder = sorted_rip(make_plan, mode="move")
    assert not (src_dir / "Film_t00.mkv").exists()
    assert sort2own.undo(folder, None, False, False) == 0
    assert (src_dir / "Film_t00.mkv").exists()
    assert not folder.exists()


def test_dry_run_removes_nothing(make_plan):
    plan, folder = sorted_rip(make_plan)
    assert sort2own.undo(folder, None, False, True) == 0
    assert (folder / "A Film (2024).mkv").exists()
    assert not runs_of(folder)[0].get("undone")


# --- which run -------------------------------------------------------------

def test_only_the_last_run_is_undone_by_default(make_plan):
    plan, folder = sorted_rip(make_plan)
    plan2, _ = sorted_rip(make_plan, name="A Film (2024)")
    assert len(runs_of(folder)) == 2

    sort2own.undo(folder, None, False, False)
    after = runs_of(folder)
    assert after[1].get("undone")
    assert not after[0].get("undone")
    assert (folder / "A Film (2024).mkv").exists()


def test_all_undoes_every_run(make_plan):
    sorted_rip(make_plan)
    plan2, folder = sorted_rip(make_plan)
    assert sort2own.undo(folder, None, True, False) == 0
    assert not folder.exists()


def test_run_selects_an_earlier_one(make_plan):
    plan, folder = sorted_rip(make_plan)

    # A second, genuinely separate run: its own file, so undoing the first
    # leaves the folder standing.
    later = make_plan([{"duration": 600, "tag": "Making of",
                        "name": "Second_t00.mkv"}])
    later.titles[0].kind = sort2own.EXTRA
    later.titles[0].label, later.titles[0].extra_type = "Making of", "featurettes"
    sort2own.execute(later, "copy", False)

    sort2own.undo(folder, 0, False, False)
    assert runs_of(folder)[0].get("undone")
    assert not runs_of(folder)[1].get("undone")
    assert (folder / "featurettes" / "Making of.mkv").exists()


def test_an_out_of_range_run_is_rejected(make_plan):
    plan, folder = sorted_rip(make_plan)
    with pytest.raises(SystemExit):
        sort2own.undo(folder, 7, False, False)


def test_undoing_twice_says_so(make_plan):
    plan, folder = sorted_rip(make_plan)
    sort2own.undo(folder, None, False, False)
    folder.mkdir(exist_ok=True)          # it removed the folder; recreate bare
    with pytest.raises(SystemExit):
        sort2own.undo(folder, None, False, False)


def test_a_folder_without_a_manifest_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        sort2own.undo(tmp_path, None, False, False)


def test_an_unreadable_manifest_is_never_guessed_at(make_plan):
    plan, folder = sorted_rip(make_plan)
    (folder / sort2own.MANIFEST_NAME).write_text("not json")
    with pytest.raises(SystemExit):
        sort2own.undo(folder, None, False, False)
    assert (folder / "A Film (2024).mkv").exists()


# --- refusals --------------------------------------------------------------

def test_a_replaced_copy_is_left_alone(make_plan):
    plan, folder = sorted_rip(make_plan)
    main = folder / "A Film (2024).mkv"
    main.write_bytes(b"something else entirely")
    assert sort2own.undo(folder, None, False, False) == 1
    assert main.read_bytes() == b"something else entirely"


def test_a_replaced_hardlink_is_left_alone(make_plan):
    plan, folder = sorted_rip(make_plan, mode="hardlink")
    main = folder / "A Film (2024).mkv"
    main.unlink()
    main.write_bytes(b"a different file at the same path")
    assert sort2own.undo(folder, None, False, False) == 1
    assert main.exists()


def test_a_copy_whose_source_is_gone_is_left_alone(make_plan):
    """Removing it would destroy the only remaining copy."""
    plan, folder = sorted_rip(make_plan)
    for t in plan.titles:
        t.path.unlink()
    assert sort2own.undo(folder, None, False, False) == 1
    assert (folder / "A Film (2024).mkv").exists()


def test_a_move_back_onto_an_occupied_path_is_refused(make_plan, src_dir):
    plan, folder = sorted_rip(make_plan, mode="move")
    (src_dir / "Film_t00.mkv").write_bytes(b"a new rip landed here")
    assert sort2own.undo(folder, None, False, False) == 1
    assert (src_dir / "Film_t00.mkv").read_bytes() == b"a new rip landed here"
    assert (folder / "A Film (2024).mkv").exists()


def test_a_refusal_leaves_the_run_marked_as_not_undone(make_plan):
    plan, folder = sorted_rip(make_plan)
    (folder / "A Film (2024).mkv").write_bytes(b"changed")
    sort2own.undo(folder, None, False, False)
    assert not runs_of(folder)[0].get("undone")


def test_a_destination_outside_the_folder_is_refused(make_plan, tmp_path):
    """A hand-edited or corrupted manifest must not reach arbitrary paths."""
    plan, folder = sorted_rip(make_plan)
    outsider = tmp_path / "innocent.mkv"
    outsider.write_bytes(b"not yours")
    runs = runs_of(folder)
    runs[0]["actions"][0]["destination"] = str(outsider)
    (folder / sort2own.MANIFEST_NAME).write_text(json.dumps(runs))

    assert sort2own.undo(folder, None, False, False) == 1
    assert outsider.exists()


def test_a_manifest_predating_verification_is_refused(make_plan):
    plan, folder = sorted_rip(make_plan)
    runs = runs_of(folder)
    for action in runs[0]["actions"]:
        action.pop("sample_hash")
        action.pop("size")
    (folder / sort2own.MANIFEST_NAME).write_text(json.dumps(runs))
    assert sort2own.undo(folder, None, False, False) == 1
    assert (folder / "A Film (2024).mkv").exists()
    assert (folder / "trailers" / "Trailer.mkv").exists()


def test_files_that_are_already_gone_are_not_an_error(make_plan):
    plan, folder = sorted_rip(make_plan)
    (folder / "trailers" / "Trailer.mkv").unlink()
    assert sort2own.undo(folder, None, False, False) == 0
    assert not folder.exists()


def test_unlisted_files_keep_the_folder_alive(make_plan):
    plan, folder = sorted_rip(make_plan)
    (folder / "poster.jpg").write_bytes(b"artwork")
    assert sort2own.undo(folder, None, False, False) == 0
    assert (folder / "poster.jpg").exists()
    assert not (folder / "A Film (2024).mkv").exists()
    assert runs_of(folder)[0]["undone"]


# --- CLI -------------------------------------------------------------------

def test_undo_via_main_needs_no_source(make_plan):
    plan, folder = sorted_rip(make_plan)
    assert sort2own.main(["--undo", str(folder)]) == 0
    assert not folder.exists()


def test_a_missing_source_is_an_error():
    with pytest.raises(SystemExit):
        sort2own.main([])


def test_run_without_undo_is_an_error(src_dir):
    with pytest.raises(SystemExit):
        sort2own.main([str(src_dir), "--run", "0"])
