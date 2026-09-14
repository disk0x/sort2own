"""
Item 6 — telling a trailer from a making-of using what the disc says.

The typing rules are exercised against fabricated Titles so they need no
ffmpeg; only the probing test reads a real file.
"""

import pytest

import sort2own
from sort2own import VERSION, EXTRA


def titled(make_plan, tag="", **fields):
    """One extra-sized title carrying the given disc metadata."""
    plan = make_plan([{"duration": 300, "tag": tag}])
    t = plan.titles[0]
    for key, value in fields.items():
        setattr(t, key, value)
    return plan, t


def typed(make_plan, tag="", main=None, **fields):
    plan, t = titled(make_plan, tag, **fields)
    return sort2own.type_extra(t, plan, main)


# --- keyword matching ------------------------------------------------------

@pytest.mark.parametrize("text,folder", [
    ("Trailer", "trailers"),
    ("Deutscher Trailer", "trailers"),
    ("Teaser", "trailers"),
    ("Making of", "featurettes"),
    ("Making-of the Movie", "featurettes"),
    ("Die Story", "featurettes"),
    ("Hinter den Kulissen", "behind the scenes"),
    ("Behind the Scenes", "behind the scenes"),
    ("Interview mit dem Regisseur", "interviews"),
    ("Gespräch mit Felix Lobrecht", "interviews"),
    ("Entfallene Szenen", "deleted scenes"),
    ("Deleted Scenes", "deleted scenes"),
    ("Musikvideo EINE BAND", "scenes"),
    ("Soundtrack-Video „Zweite Kamera“", "scenes"),
    ("Outtakes", "other"),
    ("Pannen", "other"),
])
def test_disc_wording_maps_to_a_jellyfin_folder(text, folder):
    assert sort2own.match_keyword(text) == folder


def test_unrecognised_wording_matches_nothing():
    assert sort2own.match_keyword("Kapitelanwahl") is None
    assert sort2own.match_keyword("") is None


# --- the title tag ---------------------------------------------------------

def test_a_tagged_extra_is_typed_confidently(make_plan):
    folder, confident, why = typed(make_plan, tag="Deutscher Trailer")
    assert (folder, confident) == ("trailers", True)
    assert "Deutscher Trailer" in why


def test_a_tag_repeating_the_film_name_types_nothing(make_plan):
    """useful_tag() discards it, so there is nothing to match on."""
    folder, _, _ = typed(make_plan, tag="A Film")
    assert folder != "trailers"


# --- chapters --------------------------------------------------------------

def test_a_chapter_name_types_the_extra(make_plan):
    folder, confident, why = typed(make_plan,
                                   chapter_titles=["Hinter den Kulissen"])
    assert (folder, confident) == ("behind the scenes", True)
    assert "chapter" in why


def test_the_title_tag_wins_over_a_chapter(make_plan):
    folder, _, _ = typed(make_plan, tag="Interview",
                         chapter_titles=["Trailer"])
    assert folder == "interviews"


# --- shape-based guesses ---------------------------------------------------

@pytest.mark.parametrize("duration", [90, 150, 900])
def test_shape_alone_is_never_guessed_from(make_plan, duration):
    """
    There was a "short, one audio track, no subtitles → probably a trailer"
    rule. On a real Blu-ray it fired on all ten extras, two of which were
    trailers, and buried the genuine flags in the TUI. Shape is not evidence.
    """
    plan = make_plan([{"duration": duration}])
    t = plan.titles[0]
    assert sort2own.type_extra(t, plan, None)[0] is None


def test_a_foreign_language_extra_hints_at_an_interview(make_plan):
    plan = make_plan([{"duration": 600}, {"duration": 7200}])
    extra, main = plan.titles
    extra.audio_langs = ["eng"]
    main.audio_langs = ["ger"]
    folder, confident, _ = sort2own.type_extra(extra, plan, main)
    assert (folder, confident) == ("interviews", False)


def test_a_shared_language_is_no_evidence(make_plan):
    plan = make_plan([{"duration": 600}, {"duration": 7200}])
    extra, main = plan.titles
    extra.audio_langs = main.audio_langs = ["ger"]
    assert sort2own.type_extra(extra, plan, main)[0] is None


# --- applied through classify ----------------------------------------------

def classify(plan):
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    return plan.titles


def test_confident_types_are_applied(make_plan):
    plan = make_plan([{"duration": 7200},
                      {"duration": 300, "tag": "Trailer"},
                      {"duration": 600, "tag": "Making of"}])
    ts = classify(plan)
    assert ts[1].extra_type == "trailers"
    assert ts[2].extra_type == "featurettes"


def test_a_weak_signal_is_flagged_but_not_applied(make_plan):
    """A foreign-language extra hints at an interview; it is never applied."""
    plan = make_plan([{"duration": 7200}, {"duration": 600}])
    plan.titles[0].audio_langs = ["ger"]
    plan.titles[1].audio_langs = ["eng"]
    ts = classify(plan)
    assert ts[1].extra_type == "extras"          # untouched
    assert ts[1].suggestion.startswith("maybe interviews")


def test_an_untyped_extra_keeps_the_generic_folder(make_plan):
    plan = make_plan([{"duration": 7200}, {"duration": 600}])
    assert classify(plan)[1].extra_type == "extras"


def test_a_commentary_track_makes_it_a_version(make_plan):
    """The film again with a commentary over it — not an extra."""
    plan = make_plan([{"duration": 7200}, {"duration": 7190}])
    plan.titles[1].audio_titles = ["Audiokommentar mit dem Regisseur"]
    ts = classify(plan)
    assert ts[1].kind == VERSION
    assert ts[1].label == "Commentary"


def test_a_short_commentary_titled_extra_is_not_a_version(make_plan):
    """Only something the length of the feature can be a commentary of it."""
    plan = make_plan([{"duration": 7200}, {"duration": 300}])
    plan.titles[1].audio_titles = ["Audio Commentary"]
    assert classify(plan)[1].kind == EXTRA


def test_typing_survives_a_second_classify(make_plan):
    plan = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"}])
    first = [(t.kind, t.extra_type) for t in classify(plan)]
    assert [(t.kind, t.extra_type) for t in classify(plan)] == first


# --- probing ---------------------------------------------------------------

def test_ffprobe_reads_audio_chapters_and_tags(make_rip, src_dir):
    """One ffprobe call has to return all of it — see the D1 research."""
    make_rip([{"duration": 4, "tag": "Making of", "audio": [
        {"title": "Deutsch 5.1", "language": "ger"},
        {"title": "Audio Commentary", "language": "eng"},
    ]}])
    t = sort2own.scan(src_dir)[0]
    assert t.tag == "Making of"
    assert t.audio_titles == ["Deutsch 5.1", "Audio Commentary"]
    assert t.audio_langs == ["ger", "eng"]


def test_a_plain_rip_probes_without_stream_metadata(make_rip, src_dir):
    make_rip([{"duration": 4}])
    t = sort2own.scan(src_dir)[0]
    assert t.audio_titles == []
    assert t.chapter_titles == []
