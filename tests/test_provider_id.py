"""Item 9 phase A — [tmdbid-…] / [imdbid-…] in the folder name."""

import json

import pytest

import sort2own
from sort2own import MAIN, VERSION, EXTRA, EPISODE


def test_folder_and_main_file_both_carry_the_id(make_plan, library):
    plan = make_plan([{"duration": 7200}], provider_id="tmdbid-112233")
    plan.titles[0].kind = MAIN
    assert sort2own.destination(plan, plan.titles[0]) == (
        library / "A Film (2024) [tmdbid-112233]"
        / "A Film (2024) [tmdbid-112233].mkv")


def test_version_file_starts_with_the_exact_folder_name(make_plan):
    """The §3 gotcha: a version that loses the ID scans as its own movie."""
    plan = make_plan([{"duration": 7200}, {"duration": 7000}],
                     provider_id="tmdbid-112233")
    plan.titles[0].kind, plan.titles[1].kind = MAIN, VERSION
    plan.titles[1].label = "Director's Cut"
    for t in plan.titles:
        dst = sort2own.destination(plan, t)
        assert dst.name.startswith(plan.folder)
        assert dst.parent.name == plan.folder


def test_extras_sit_under_the_identified_folder(make_plan):
    plan = make_plan([{"duration": 300}], provider_id="imdbid-tt7654321")
    plan.titles[0].kind = EXTRA
    plan.titles[0].extra_type = "trailers"
    plan.titles[0].label = "Kinotrailer"
    dst = sort2own.destination(plan, plan.titles[0])
    assert dst.parent.parent.name == "A Film (2024) [imdbid-tt7654321]"


def test_episode_keeps_the_bare_show_name_under_an_identified_folder(make_plan):
    """Jellyfin matches episodes on SxxEyy; the ID belongs on the series folder."""
    plan = make_plan([{"duration": 2700}], name="A Series (2016)",
                     tv=True, provider_id="tmdbid-42009")
    plan.titles[0].kind, plan.titles[0].label = EPISODE, "3"
    dst = sort2own.destination(plan, plan.titles[0])
    assert dst.name == "A Series (2016) S01E03.mkv"
    assert dst.parent.parent.name == "A Series (2016) [tmdbid-42009]"


def test_no_id_leaves_the_folder_name_untouched(make_plan, library):
    plan = make_plan([{"duration": 7200}])
    plan.titles[0].kind = MAIN
    assert sort2own.destination(plan, plan.titles[0]) == (
        library / "A Film (2024)" / "A Film (2024).mkv")


def test_tag_hygiene_survives_an_id(make_plan):
    """
    The ID is kept out of plan.name precisely so useful_tag()'s year regex and
    film-name comparison keep working.
    """
    plan = make_plan([{"duration": 300, "tag": "A Film"}],
                     provider_id="tmdbid-112233")
    assert sort2own.useful_tag(plan.titles[0], plan) == ""


@pytest.mark.parametrize("tmdb,imdb,expected", [
    ("112233", None, "tmdbid-112233"),
    (None, "tt7654321", "imdbid-tt7654321"),
    (None, None, ""),
])
def test_valid_ids_become_the_folder_suffix(tmdb, imdb, expected):
    assert sort2own.provider_id(tmdb, imdb) == expected


@pytest.mark.parametrize("tmdb,imdb", [
    ("tt7654321", None),          # imdb id passed to --tmdb
    ("112233x", None),
    (None, "15398776"),            # missing tt prefix
    (None, "nm0000123"),           # a person, not a title
])
def test_malformed_ids_are_rejected(tmdb, imdb):
    with pytest.raises(SystemExit):
        sort2own.provider_id(tmdb, imdb)


def test_tmdb_and_imdb_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        sort2own.parse_args(["src", "--tmdb", "1", "--imdb", "tt1"])


def test_manifest_records_the_id(make_plan):
    plan = make_plan([{"duration": 7200}], provider_id="tmdbid-112233")
    plan.titles[0].kind = MAIN
    sort2own.execute(plan, "copy", False)
    runs = json.loads((sort2own.library_root(plan)
                       / sort2own.MANIFEST_NAME).read_text())
    assert runs[0]["provider_id"] == "tmdbid-112233"
    assert runs[0]["name"] == "A Film (2024)"   # stored clean
