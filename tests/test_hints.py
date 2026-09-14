"""
Item 8 — naming extras from an OFDb release listing.

Every page here is a hand-written fragment, not a saved copy of someone else's
site: the parser only cares about structure, so the fragments carry the shapes
it has to survive and nothing else. Nothing here touches the network.
"""

import pytest

import hints_ofdb
import sort2own

# An invented release listing carrying the shapes the matcher has to cope
# with: a name the keyword table recognises and one it does not, two entries
# that happen to share a length, and entries a disc lists without a runtime.
EXTRAS_LISTING = [
    ("Die Story", 150),             # the keyword table types this featurettes
    ("Zweite Kamera", 155),         # no keyword matches: stays a plain extra
    ("Behind the Scenes", 137),     # ) equal lengths, so duration alone
    ("Erster Entwurf", 137),        # ) cannot tell these two apart
    ("Trailer", 152),
    ("Trailershow", None),          # a reel, listed without a runtime
    ("Alle abspielen", None),
]


# --- runtime parsing -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Die Story (2:30 Min.)", ("Die Story", 150)),
    ("Kurzfilm (0:57 Min.)", ("Kurzfilm", 57)),
    ("Behind the Scenes (2:17 Min)", ("Behind the Scenes", 137)),
    ('Soundtrack Video "Zweite Kamera" (2:19 Min.)',
     ('Soundtrack Video "Zweite Kamera"', 139)),
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


# --- page shapes -----------------------------------------------------------

EXTRAS_BLOCK = ("<b>Extras:</b></p></div><div class=\"fassung-absatz\">"
                "{}</div></div><b>Bemerkungen:</b>")


def test_a_whole_listing_parses_in_page_order():
    """
    The block boundary is the only reliable landmark, so a release that mixes
    every shape at once has to come out in order: a sub-heading that is not an
    extra, list items, bare text beside a <strong> name, an entry with no
    runtime, and a footnote legend that is not an extra either.
    """
    page = EXTRAS_BLOCK.format(
        "<strong>Featurettes:</strong>"
        "<ul><li>Die Story (2:30 Min.)</li>"
        "<li>Zweite Kamera (2:35 Min.) *</li></ul>"
        "<strong>Kinotrailer </strong>(2:32 Min.) **"
        "<ul><li>Trailershow</li></ul>"
        "<div><em>* = ohne Untertitel<br />** = englisch</em></div>")
    assert hints_ofdb.parse(page) == [
        ("Die Story", 150), ("Zweite Kamera", 155),
        ("Kinotrailer", 152), ("Trailershow", None)]


def test_a_nested_reel_is_read_as_its_own_entries():
    """
    Some releases wrap the extras in a single list item and hang the trailer
    reel off it as a nested list; the reel's entries are extras in their own
    right, not part of the name above them.
    """
    page = EXTRAS_BLOCK.format(
        "<ul><li>Die Story (2:30 Min.)"
        "<ol><li>Trailer A (1:00 Min.)</li>"
        "<li>Trailer B (1:05 Min.)</li></ol></li></ul>")
    assert hints_ofdb.parse(page) == [
        ("Die Story", 150), ("Trailer A", 60), ("Trailer B", 65)]


def test_a_page_without_an_extras_section_yields_nothing():
    assert hints_ofdb.parse("<html><body><b>Bemerkungen:</b>"
                            "<ul><li>16 Kapitel</li></ul></body></html>") == []


def test_bare_text_entries_are_found_as_well_as_list_items():
    """
    Some releases list extras as plain text beside a <strong> name rather than
    in a list, under sub-headings that are themselves bold.
    """
    page = EXTRAS_BLOCK.format(
        "<strong>Featurettes:</strong>"
        "<ul><li>Filming Zone (32:02 Min.) **<br /></li></ul>"
        "<strong>Interview mit X </strong>(18:45 Min.) ***<strong><br /></strong>"
        "<strong>Kinotrailer </strong>(1:17 Min.) ***")
    assert hints_ofdb.parse(page) == [
        ("Filming Zone", 1922), ("Interview mit X", 1125), ("Kinotrailer", 77)]


def test_a_sub_heading_is_not_an_extra():
    page = EXTRAS_BLOCK.format("<strong>Featurettes:</strong>"
                               "<ul><li>Filming Zone (32:02 Min.)</li></ul>")
    assert [n for n, _ in hints_ofdb.parse(page)] == ["Filming Zone"]


def test_the_footnote_legend_is_not_an_extra():
    page = EXTRAS_BLOCK.format(
        "<ul><li>Filming Zone (32:02 Min.) **</li></ul>"
        "<div><em>* = englisch mit optionalen deutschen Untertiteln<br />"
        "** = englisch ohne Untertitel</em></div>")
    assert [n for n, _ in hints_ofdb.parse(page)] == ["Filming Zone"]


def test_the_next_section_is_not_read_as_extras():
    page = EXTRAS_BLOCK.format("<ul><li>Filming Zone (32:02 Min.)</li></ul>")
    page += "<div><p>Audiodeskription (2:00 Min.)</p></div>"
    assert [n for n, _ in hints_ofdb.parse(page)] == ["Filming Zone"]


def test_junk_does_not_raise():
    assert hints_ofdb.parse("<<<not really html") == []


# --- caching ---------------------------------------------------------------

def test_a_cached_listing_is_used_instead_of_fetching(tmp_path, monkeypatch):
    def refuse(_):
        raise AssertionError("should not have gone to the network")
    monkeypatch.setattr(hints_ofdb, "fetch", refuse)

    cache = tmp_path / "ofdb"
    cache.mkdir()
    (cache / "123456_789012.json").write_text(
        '{"version": %d, "items": [["Die Story", 150]]}' % hints_ofdb.CACHE_VERSION)
    assert hints_ofdb.lookup("123456,789012", cache) == [("Die Story", 150)]


def test_a_fetched_listing_is_written_to_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(hints_ofdb, "fetch", lambda ref:
                        "<b>Extras:</b></p></div><div><ul>"
                        "<li>Die Story (2:30 Min.)</li></ul></div></div>")
    cache = tmp_path / "ofdb"
    assert hints_ofdb.lookup("123456,789012", cache) == [("Die Story", 150)]
    assert (cache / "123456_789012.json").is_file()


def test_an_unreadable_cache_just_refetches(tmp_path, monkeypatch):
    monkeypatch.setattr(hints_ofdb, "fetch", lambda ref:
                        "<b>Extras:</b></p></div><div><ul>"
                        "<li>Trailer (2:32 Min.)</li></ul></div></div>")
    cache = tmp_path / "ofdb"
    cache.mkdir()
    (cache / "123456_789012.json").write_text("not json")
    assert hints_ofdb.lookup("123456,789012", cache) == [("Trailer", 152)]


# --- applying the hints ----------------------------------------------------

def hinted(make_plan, specs, listings=EXTRAS_LISTING, apply=True,
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
    assert ts[1].label == "Zweite Kamera"       # OFDb's own spelling, kept
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


def test_a_cache_from_an_older_parser_is_discarded(tmp_path, monkeypatch):
    """
    A fixed parser must reach anyone who already looked the release up. The
    footnote bug otherwise kept being served from disk after it was fixed.
    """
    monkeypatch.setattr(hints_ofdb, "fetch", lambda ref:
                        "<b>Extras:</b></p></div><div><ul>"
                        "<li>Filming Zone (32:02 Min.) **</li></ul></div></div>")
    cache = tmp_path / "ofdb"
    cache.mkdir()
    (cache / "123456_789012.json").write_text(
        '{"version": %d, "items": [["Filming Zone (32:02 Min.) **", null]]}'
        % (hints_ofdb.CACHE_VERSION - 1))

    assert hints_ofdb.lookup("123456,789012", cache) == [("Filming Zone", 1922)]


def test_a_cache_in_the_old_bare_list_layout_is_discarded(tmp_path, monkeypatch):
    monkeypatch.setattr(hints_ofdb, "fetch", lambda ref:
                        "<b>Extras:</b></p></div><div><ul>"
                        "<li>Trailer (2:32 Min.)</li></ul></div></div>")
    cache = tmp_path / "ofdb"
    cache.mkdir()
    (cache / "123456_789012.json").write_text('[["Trailer (2:32 Min.)", null]]')
    assert hints_ofdb.lookup("123456,789012", cache) == [("Trailer", 152)]
