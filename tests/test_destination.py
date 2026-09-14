"""The Jellyfin layout rules — see DESIGN.md."""

import sort2own
from sort2own import MAIN, VERSION, EXTRA, EPISODE, SKIP


def only(plan, kind, **attrs):
    t = plan.titles[0]
    t.kind = kind
    for k, v in attrs.items():
        setattr(t, k, v)
    return sort2own.destination(plan, t)


def test_main_file_name_equals_the_folder_name(make_plan, library):
    plan = make_plan([{"duration": 7200}])
    dst = only(plan, MAIN)
    assert dst == library / "A Film (2024)" / "A Film (2024).mkv"


def test_version_is_folder_name_space_dash_space_label(make_plan, library):
    plan = make_plan([{"duration": 7000}])
    dst = only(plan, VERSION, label="Director's Cut")
    assert dst.name == "A Film (2024) - Director's Cut.mkv"
    assert dst.parent == library / "A Film (2024)"


def test_unlabelled_version_still_gets_a_name(make_plan):
    plan = make_plan([{"duration": 7000}])
    assert only(plan, VERSION).name == "A Film (2024) - Alternate cut.mkv"


def test_extra_goes_in_its_type_subfolder_with_a_free_name(make_plan, library):
    plan = make_plan([{"duration": 300}])
    dst = only(plan, EXTRA, extra_type="trailers", label="Kinotrailer")
    assert dst == library / "A Film (2024)" / "trailers" / "Kinotrailer.mkv"


def test_unlabelled_extra_falls_back_to_its_duration(make_plan):
    plan = make_plan([{"duration": 760}])
    assert only(plan, EXTRA).name == "Extra 12m40s.mkv"


def test_episode_lands_in_a_zero_padded_season_folder(make_plan, library):
    plan = make_plan([{"duration": 2700}], name="A Series (2016)",
                     tv=True, season=2)
    dst = only(plan, EPISODE, label="3")
    assert dst == (library / "A Series (2016)" / "Season 02" /
                   "A Series (2016) S02E03.mkv")


def test_skipped_title_has_no_destination(make_plan):
    plan = make_plan([{"duration": 30}])
    assert only(plan, SKIP) is None


def test_placeable_file_names_start_with_the_folder_name(make_plan):
    """
    Jellyfin scans a file that does not share the folder-name prefix as a
    separate movie — the original 'eleven copies' failure mode.
    """
    plan = make_plan([{"duration": 7200}, {"duration": 7000}])
    folder = sort2own.safe_name(plan.name)
    plan.titles[0].kind, plan.titles[1].kind = MAIN, VERSION
    plan.titles[1].label = "Uncut"
    for t in plan.titles:
        assert sort2own.destination(plan, t).name.startswith(folder)


def test_safe_name_strips_characters_jellyfin_chokes_on(make_plan):
    assert sort2own.safe_name('A: B/C "D" <E>|F?*') == "A BC D EF"


def test_reserved_characters_never_reach_the_path(make_plan, library):
    plan = make_plan([{"duration": 7200}], name="Face/Off (1997)")
    assert only(plan, MAIN) == library / "FaceOff (1997)" / "FaceOff (1997).mkv"


def test_useful_tag_discards_the_film_name_but_keeps_real_labels(make_plan):
    plan = make_plan([{"duration": 300}])
    t = plan.titles[0]
    for repeated in ("A Film", "a_film",
                     "A Film (2024)"):
        t.tag = repeated
        assert sort2own.useful_tag(t, plan) == ""
    t.tag = "Making of"
    assert sort2own.useful_tag(t, plan) == "Making of"


def test_unique_never_overwrites(tmp_path):
    p = tmp_path / "x.mkv"
    assert sort2own.unique(p) == p
    p.touch()
    assert sort2own.unique(p) == tmp_path / "x (2).mkv"
    (tmp_path / "x (2).mkv").touch()
    assert sort2own.unique(p) == tmp_path / "x (3).mkv"
