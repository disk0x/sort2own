"""Locks in the current heuristics before later items start changing them."""

import sort2own
from sort2own import MAIN, VERSION, EXTRA, EPISODE, SKIP


def classify(plan, runtime=None, min_extra=90, version_ratio=0.85,
             dup_tolerance=0.01):
    sort2own.classify(plan, runtime, min_extra, version_ratio, dup_tolerance)
    return plan.titles


def test_longest_title_is_the_main_feature(make_plan):
    ts = classify(make_plan([{"duration": 300}, {"duration": 7200},
                             {"duration": 600}]))
    assert [t.kind for t in ts] == [EXTRA, MAIN, EXTRA]
    assert ts[1].note == "longest title"


def test_runtime_beats_length_when_given(make_plan):
    """A 'play all extras' title can be longer than the film itself."""
    ts = classify(make_plan([{"duration": 8000}, {"duration": 6000}]),
                  runtime=100)
    assert ts[1].kind == MAIN
    assert ts[0].kind == VERSION      # 8000 is within 85% of 6000


def test_near_main_length_becomes_a_version(make_plan):
    ts = classify(make_plan([{"duration": 7200}, {"duration": 6300}]))
    assert ts[1].kind == VERSION


def test_clearly_shorter_title_is_an_extra_not_a_version(make_plan):
    ts = classify(make_plan([{"duration": 7200}, {"duration": 6000}]))
    assert ts[1].kind == EXTRA


def test_titles_below_min_extra_are_skipped(make_plan):
    ts = classify(make_plan([{"duration": 7200}, {"duration": 30}]))
    assert ts[1].kind == SKIP
    assert "shorter than 90s" in ts[1].note


def test_duplicate_of_an_earlier_title_is_skipped(make_plan):
    ts = classify(make_plan([{"duration": 7200}, {"duration": 7200}]))
    assert ts[0].kind == MAIN
    assert ts[1].kind == SKIP
    assert ts[1].note == "duplicate of Film_t00.mkv"


def test_extra_is_named_from_its_title_tag(make_plan):
    ts = classify(make_plan([{"duration": 7200}, {"duration": 300,
                                                  "tag": "Trailer"}]))
    assert ts[1].label == "Trailer"


def test_tag_repeating_the_film_name_is_discarded(make_plan):
    """MakeMKV writes the disc label into every title on some discs."""
    ts = classify(make_plan([{"duration": 7200},
                             {"duration": 300, "tag": "A Film"}]))
    assert ts[1].label == "Extra 5m00s"


def test_extra_names_stay_unique(make_plan):
    ts = classify(make_plan([{"duration": 7200},
                             {"duration": 300, "tag": "Trailer"},
                             {"duration": 400, "tag": "Trailer"}]))
    assert [t.label for t in ts[1:]] == ["Trailer", "Trailer (2)"]


def test_tv_episodes_cluster_on_the_median_and_number_in_disc_order(make_plan):
    plan = make_plan([{"duration": 2700}, {"duration": 2760},
                      {"duration": 2640}, {"duration": 300}],
                     name="A Series (2016)", tv=True)
    ts = classify(plan)
    assert [t.kind for t in ts] == [EPISODE, EPISODE, EPISODE, EXTRA]
    assert [t.label for t in ts[:3]] == ["1", "2", "3"]


def test_classify_is_repeatable(make_plan):
    """It resets state at the top, so a second pass must not drift."""
    plan = make_plan([{"duration": 7200}, {"duration": 300, "tag": "Trailer"},
                      {"duration": 30}])
    first = [(t.kind, t.label) for t in classify(plan)]
    assert [(t.kind, t.label) for t in classify(plan)] == first
