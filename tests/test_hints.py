"""
Item 8 — naming extras from an OFDb release listing.

The parser is exercised against a saved page under tests/fixtures/, which is
gitignored because it is someone else's HTML; those tests skip when it is not
there. Nothing here touches the network.
"""

from pathlib import Path

import pytest

import hints_ofdb
import sort2own
from sort2own import EXTRA, MAIN, VERSION

FIXTURE = Path(__file__).parent / "fixtures" / "ofdb_a_film.html"

# What the real page lists, as of Sept 2026. Nine of the fifteen timed entries
# are unique; the disc's trailer reel repeats three of the featurette lengths.
A_FILM = [
    ("Die Story", 150), ("Realitiy Check", 155), ("Behind the Scenes", 137),
    ('Soundtrack Video "Girls in the Bus"', 117),
    ('Soundtrack Video "Berlin Heist"', 139),
    ('Musikvideo EINE BAND "Hinterm Block"', 172),
    ("Trailer", 152), ("Unsere DVD/BD-Empfehlung: Perfect Addiction", 127),
    ("Trailershow", None), ("Alle abspielen", None),
    ("Erster Entwurf", 137), ("Contra", 117), ("Caveman", 57),
    ("Tiger Girl", 90), ("Jugend ohne Gott", 110),
    ("Zeiten ändern dich", 127), ("Blutzbrüdaz", 143),
]


# --- runtime parsing -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Die Story (2:30 Min.)", ("Die Story", 150)),
    ("Caveman (0:57 Min.)", ("Caveman", 57)),
    ("Behind the Scenes (2:17 Min)", ("Behind the Scenes", 137)),
    ('Soundtrack Video "Berlin Heist" (2:19 Min.)',
     ('Soundtrack Video "Berlin Heist"', 139)),
    ("Trailershow", ("Trailershow", None)),
    ("Alle abspielen ", ("Alle abspielen", None)),
    # Footnote markers follow the runtime on some releases. Anchoring the
    # pattern to the end of the line matched none of these, so a whole disc
    # came back untimed and nothing could be named.
    ("Filming Zone (32:02 Min.) **", ("Filming Zone", 1922)),
    ("Eine seltene Perspektive (2:00 Min.) *", ("Eine seltene Perspektive", 120)),
    ("Auf dem Boden (2:32 Min.)  †", ("Auf dem Boden", 152)),
])
def test_a_listing_splits_into_name_and_seconds(text, expected):
    assert hints_ofdb.split_runtime(text) == expected


# --- references ------------------------------------------------------------

@pytest.mark.parametrize("ref", [
    "https://www.ofdb.de/fassung/123456,789012,A-Film/",
    "www.ofdb.de/fassung/123456,789012,A-Film",
    "123456,789012",
])
def test_a_url_or_a_bare_id_pair_both_work(ref):
    assert hints_ofdb.reference(ref) == "123456,789012"


def test_something_that_is_not_a_reference_is_rejected():
    with pytest.raises(ValueError):
        hints_ofdb.reference("A Film")


# --- the real page ---------------------------------------------------------

@pytest.mark.skipif(not FIXTURE.is_file(),
                    reason="saved OFDb page not present (it is gitignored)")
def test_the_saved_page_parses_exactly():
    assert hints_ofdb.parse(FIXTURE.read_text(encoding="utf-8",
                                              errors="replace")) == A_FILM


@pytest.mark.skipif(not FIXTURE.is_file(), reason="saved OFDb page not present")
def test_parsing_stops_before_the_next_section():
    """The page continues with "Bemerkungen:" — those are not extras."""
    names = [n for n, _ in hints_ofdb.parse(
        FIXTURE.read_text(encoding="utf-8", errors="replace"))]
    assert not any("codiert" in n or "Kapitel" in n for n in names)


def test_a_page_without_an_extras_section_yields_nothing():
    assert hints_ofdb.parse("<html><body><b>Bemerkungen:</b>"
                            "<ul><li>16 Kapitel</li></ul></body></html>") == []


def test_junk_does_not_raise():
    assert hints_ofdb.parse("<<<not really html") == []


# --- caching ---------------------------------------------------------------

def test_a_cached_listing_is_used_instead_of_fetching(tmp_path, monkeypatch):
    def refuse(_):
        raise AssertionError("should not have gone to the network")
    monkeypatch.setattr(hints_ofdb, "fetch", refuse)

    cache = tmp_path / "ofdb"
    cache.mkdir()
    (cache / "123456_789012.json").write_text('[["Die Story", 150]]')
    assert hints_ofdb.lookup("123456,789012", cache) == [("Die Story", 150)]


def test_a_fetched_listing_is_written_to_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(hints_ofdb, "fetch", lambda ref:
                        "<b>Extras:</b><ul><li>Die Story (2:30 Min.)</li></ul>"
                        "<b>Bemerkungen:</b>")
    cache = tmp_path / "ofdb"
    assert hints_ofdb.lookup("123456,789012", cache) == [("Die Story", 150)]
    assert (cache / "123456_789012.json").is_file()


def test_an_unreadable_cache_just_refetches(tmp_path, monkeypatch):
    monkeypatch.setattr(hints_ofdb, "fetch", lambda ref:
                        "<b>Extras:</b><ul><li>Trailer (2:32 Min.)</li></ul>"
                        "<b>Bemerkungen:</b>")
    cache = tmp_path / "ofdb"
    cache.mkdir()
    (cache / "123456_789012.json").write_text("not json")
    assert hints_ofdb.lookup("123456,789012", cache) == [("Trailer", 152)]


# --- applying the hints ----------------------------------------------------

def hinted(make_plan, specs, listings=A_FILM, apply=True,
           monkeypatch=None):
    plan = make_plan([{"duration": 7200}] + specs)
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    monkeypatch.setattr(sort2own.hints_ofdb, "lookup", lambda ref, cache: listings)
    sort2own.apply_release_hints(plan, "123456,789012", apply=apply)
    return plan.titles


def test_a_unique_length_match_names_and_types_the_extra(make_plan, monkeypatch):
    ts = hinted(make_plan, [{"duration": 150}], monkeypatch=monkeypatch)
    assert ts[1].label == "Die Story"
    assert ts[1].extra_type == "featurettes"     # via the keyword table
    assert ts[1].note == "OFDb: Die Story"


def test_a_match_the_keywords_do_not_know_still_gets_its_name(make_plan,
                                                              monkeypatch):
    ts = hinted(make_plan, [{"duration": 155}], monkeypatch=monkeypatch)
    assert ts[1].label == "Realitiy Check"       # OFDb's own spelling, kept
    assert ts[1].extra_type == "extras"


def test_an_ambiguous_length_is_reported_and_not_applied(make_plan, monkeypatch):
    """137s is both "Behind the Scenes" and a trailer in the reel."""
    ts = hinted(make_plan, [{"duration": 137}], monkeypatch=monkeypatch)
    assert ts[1].label == "Extra 2m17s"          # left as it was
    assert "could be" in ts[1].suggestion        # flagged for the TUI
    assert "Behind the Scenes" in ts[1].suggestion
    assert "Erster Entwurf" in ts[1].suggestion


def test_the_tied_names_are_kept_for_the_tui_to_offer(make_plan, monkeypatch):
    """The TUI's `c` needs the names as a list, not just as a sentence."""
    ts = hinted(make_plan, [{"duration": 137}], monkeypatch=monkeypatch)
    assert ts[1].candidates == ["Behind the Scenes", "Erster Entwurf"]


def test_an_unambiguous_match_leaves_nothing_to_choose(make_plan, monkeypatch):
    ts = hinted(make_plan, [{"duration": 150}], monkeypatch=monkeypatch)
    assert ts[1].candidates == []
    assert ts[1].suggestion == ""                # settled, so not flagged


def test_a_length_nobody_lists_is_left_alone(make_plan, monkeypatch):
    ts = hinted(make_plan, [{"duration": 999}], monkeypatch=monkeypatch)
    assert ts[1].label == "Extra 16m39s"
    assert "OFDb" not in ts[1].note + ts[1].suggestion


def test_matching_is_within_a_second_either_way(make_plan, monkeypatch):
    ts = hinted(make_plan, [{"duration": 150.9}], monkeypatch=monkeypatch)
    assert ts[1].label == "Die Story"


def test_two_seconds_out_is_not_a_match(make_plan, monkeypatch):
    ts = hinted(make_plan, [{"duration": 152.5}], monkeypatch=monkeypatch)
    assert ts[1].label != "Die Story"


def test_without_apply_the_name_is_only_suggested(make_plan, monkeypatch):
    ts = hinted(make_plan, [{"duration": 150}], apply=False,
                monkeypatch=monkeypatch)
    assert ts[1].suggestion == "OFDb: Die Story"
    assert ts[1].label == "Extra 2m30s"          # untouched


def test_untimed_listings_never_match(make_plan, monkeypatch):
    ts = hinted(make_plan, [{"duration": 300}],
                listings=[("Trailershow", None)], monkeypatch=monkeypatch)
    assert "OFDb" not in ts[1].note + ts[1].suggestion


# --- failures stay advisory ------------------------------------------------

def test_a_lookup_failure_warns_and_changes_nothing(make_plan, monkeypatch,
                                                    capsys):
    plan = make_plan([{"duration": 7200}, {"duration": 150}])
    sort2own.classify(plan, None, 90, 0.85, 0.01)

    def explode(ref, cache):
        raise OSError("nas is on fire")
    monkeypatch.setattr(sort2own.hints_ofdb, "lookup", explode)

    sort2own.apply_release_hints(plan, "123456,789012", apply=True)
    assert "OFDb lookup failed" in capsys.readouterr().err
    assert plan.titles[1].label == "Extra 2m30s"


def test_a_missing_sidecar_warns_and_changes_nothing(make_plan, monkeypatch,
                                                     capsys):
    plan = make_plan([{"duration": 7200}, {"duration": 150}])
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    monkeypatch.setattr(sort2own, "hints_ofdb", None)

    sort2own.apply_release_hints(plan, "123456,789012", apply=True)
    assert "hints_ofdb.py is missing" in capsys.readouterr().err
    assert plan.titles[1].label == "Extra 2m30s"


def test_the_run_still_sorts_when_hints_fail(make_rip, src_dir, library,
                                             monkeypatch):
    make_rip([{"duration": 20}, {"duration": 4, "tag": "Trailer"}])

    def explode(ref, cache):
        raise OSError("no network")
    monkeypatch.setattr(sort2own.hints_ofdb, "lookup", explode)

    assert sort2own.main([str(src_dir), "--library", str(library), "--yes",
                          "--copy", "--min-extra", "3", "--ofdb", "1,2",
                          "--name", "Test Film (2024)"]) == 0
    assert (library / "Test Film (2024)" / "Test Film (2024).mkv").exists()
