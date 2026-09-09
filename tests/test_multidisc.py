"""Item 7 — box sets and second discs sorted into one folder."""

import json

import pytest

import sort2own
from sort2own import MAIN, VERSION, EXTRA, EPISODE, SKIP


def classify(plan, min_extra=90):
    sort2own.classify(plan, None, min_extra, 0.85, 0.01)
    return plan.titles


# --- play-all --------------------------------------------------------------

def test_a_play_all_title_is_skipped_not_placed(make_plan):
    eps = [2712, 2698, 2705]
    plan = make_plan([{"duration": d} for d in eps + [sum(eps)]],
                     name="A Series (2016)", tv=True)
    ts = classify(plan)
    assert [t.kind for t in ts[:3]] == [EPISODE] * 3
    assert ts[3].kind == SKIP
    assert ts[3].note == "play-all (every episode end to end)"


def test_a_long_extra_is_not_mistaken_for_a_play_all(make_plan):
    """
    A 20-minute featurette is far from the episode length and far from their
    combined length, so it stays an extra. (Anything within ±25% of the median
    is still numbered as an episode — a pre-existing property of the
    clustering, which the TUI's `k` exists to correct.)
    """
    plan = make_plan([{"duration": d} for d in (2712, 2698, 2705, 1200)],
                     name="A Series (2016)", tv=True)
    ts = classify(plan)
    assert ts[3].kind == EXTRA


# --- episode numbering -----------------------------------------------------

def test_start_episode_seeds_the_numbering(make_plan):
    plan = make_plan([{"duration": d} for d in (2712, 2698, 2705)],
                     name="A Series (2016)", tv=True)
    plan.start_episode = 4
    assert [t.label for t in classify(plan)] == ["4", "5", "6"]


def test_next_episode_reads_the_season_folder(make_plan, library):
    plan = make_plan([{"duration": 2712}], name="A Series (2016)",
                     tv=True)
    season = sort2own.library_root(plan) / "Season 01"
    season.mkdir(parents=True)
    for n in (1, 2, 3):
        (season / f"A Series (2016) S01E{n:02d}.mkv").touch()
    assert sort2own.next_episode(plan) == 4


def test_next_episode_counts_files_added_by_hand(make_plan):
    """The folder is the truth about what Jellyfin sees, not the manifest."""
    plan = make_plan([{"duration": 2712}], name="A Series (2016)",
                     tv=True)
    season = sort2own.library_root(plan) / "Season 01"
    season.mkdir(parents=True)
    (season / "A Series (2016) S01E09.mkv").touch()
    assert sort2own.next_episode(plan) == 10


def test_next_episode_ignores_other_seasons(make_plan):
    plan = make_plan([{"duration": 2712}], name="A Series (2016)",
                     tv=True, season=2)
    root = sort2own.library_root(plan)
    (root / "Season 01").mkdir(parents=True)
    (root / "Season 01" / "A Series (2016) S01E08.mkv").touch()
    assert sort2own.next_episode(plan) == 1


def test_next_episode_on_an_empty_library_starts_at_one(make_plan):
    plan = make_plan([{"duration": 2712}], name="A Series (2016)",
                     tv=True)
    assert sort2own.next_episode(plan) == 1


# --- supplement mode -------------------------------------------------------

def test_supplement_picks_no_main_feature(make_plan):
    plan = make_plan([{"duration": 1800}, {"duration": 300}])
    plan.supplement = True
    plan.main_duration = 7200
    ts = classify(plan)
    assert MAIN not in [t.kind for t in ts]
    assert [t.kind for t in ts] == [EXTRA, EXTRA]


def test_supplement_still_recognises_an_alternate_cut(make_plan):
    """Judged against the feature already in the library, not this disc."""
    plan = make_plan([{"duration": 7000}, {"duration": 300}])
    plan.supplement = True
    plan.main_duration = 7200
    ts = classify(plan)
    assert ts[0].kind == VERSION
    assert ts[1].kind == EXTRA


def test_supplement_without_a_known_feature_length_places_only_extras(make_plan):
    plan = make_plan([{"duration": 7000}])
    plan.supplement = True                    # main_duration left at 0
    assert classify(plan)[0].kind == EXTRA


# --- end to end ------------------------------------------------------------

def sort(src, library, *extra, name="A Series (2016)"):
    return sort2own.main([str(src), "--library", str(library), "--yes",
                          "--name", name, "--copy", "--min-extra", "3", *extra])


def test_two_discs_of_a_season_number_straight_through(make_rip, tmp_path,
                                                       library):
    disc1, disc2 = tmp_path / "disc1", tmp_path / "disc2"
    make_rip([{"duration": 20}, {"duration": 19}, {"duration": 21}], into=disc1)
    make_rip([{"duration": 18}, {"duration": 22}, {"duration": 17}], into=disc2)

    assert sort(disc1, library, "--tv") == 0
    assert sort(disc2, library, "--tv", "--continue") == 0

    season = library / "A Series (2016)" / "Season 01"
    assert sorted(p.name for p in season.glob("*.mkv")) == [
        f"A Series (2016) S01E0{n}.mkv" for n in range(1, 7)]


def test_without_continue_the_second_disc_would_collide(make_rip, tmp_path,
                                                        library):
    """Shows what --continue is for: numbering restarts and lands on (2)."""
    disc1, disc2 = tmp_path / "disc1", tmp_path / "disc2"
    make_rip([{"duration": 20}, {"duration": 19}], into=disc1)
    make_rip([{"duration": 18}, {"duration": 22}], into=disc2)

    sort(disc1, library, "--tv")
    sort(disc2, library, "--tv")
    season = library / "A Series (2016)" / "Season 01"
    assert list(season.glob("* (2).mkv"))


def test_a_supplementary_disc_lands_in_the_existing_film_folder(make_rip,
                                                                tmp_path,
                                                                library):
    disc1, disc2 = tmp_path / "disc1", tmp_path / "disc2"
    make_rip([{"duration": 20}, {"duration": 5, "tag": "Trailer"}], into=disc1)
    make_rip([{"duration": 8, "tag": "Making of"}], into=disc2)

    assert sort(disc1, library, name="A Film (2024)") == 0
    assert sort(disc2, library, "--supplement", "--disc-label", "Disc 2",
                name="A Film (2024)") == 0

    folder = library / "A Film (2024)"
    assert (folder / "A Film (2024).mkv").exists()          # untouched
    assert (folder / "extras" / "Making of.mkv").exists()
    assert not list(folder.glob("* (2).mkv"))


def test_supplement_refuses_when_the_feature_is_not_there_yet(make_rip,
                                                              src_dir, library):
    make_rip([{"duration": 8, "tag": "Making of"}])
    with pytest.raises(SystemExit):
        sort(src_dir, library, "--supplement", name="A Film (2024)")


def test_supplement_previews_without_the_feature_under_dry_run(make_rip,
                                                               src_dir,
                                                               library):
    make_rip([{"duration": 8, "tag": "Making of"}])
    assert sort(src_dir, library, "--supplement", "--dry-run",
                name="A Film (2024)") == 0


def test_a_repeated_trailer_on_disc_two_is_not_placed_twice(make_rip, tmp_path,
                                                            library):
    """Idempotence already dedupes across discs on (size, duration)."""
    disc1, disc2 = tmp_path / "disc1", tmp_path / "disc2"
    make_rip([{"duration": 20}, {"duration": 5, "tag": "Trailer"}], into=disc1)
    make_rip([{"duration": 5, "tag": "Trailer"},
              {"duration": 8, "tag": "Making of"}], into=disc2)

    sort(disc1, library, name="A Film (2024)")
    sort(disc2, library, "--supplement", name="A Film (2024)")

    trailers = list((library / "A Film (2024)").rglob("Trailer*.mkv"))
    assert len(trailers) == 1


def test_the_disc_label_is_recorded(make_rip, src_dir, library):
    make_rip([{"duration": 20}])
    sort(src_dir, library, "--disc-label", "Disc 2 of 3", name="A Film (2024)")
    runs = json.loads((library / "A Film (2024)"
                       / sort2own.MANIFEST_NAME).read_text())
    assert runs[0]["disc"] == "Disc 2 of 3"


# --- flag combinations -----------------------------------------------------

@pytest.mark.parametrize("flags", [
    ["--continue"],
    ["--start-episode", "4"],
])
def test_episode_flags_need_tv(src_dir, library, flags):
    with pytest.raises(SystemExit):
        sort(src_dir, library, *flags)


def test_continue_and_start_episode_are_mutually_exclusive(src_dir, library):
    with pytest.raises(SystemExit):
        sort(src_dir, library, "--tv", "--continue", "--start-episode", "4")


def test_supplement_and_tv_do_not_mix(src_dir, library):
    with pytest.raises(SystemExit):
        sort(src_dir, library, "--tv", "--supplement")
