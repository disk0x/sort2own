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


def test_a_one_frame_difference_is_still_a_duplicate(make_plan):
    """A second playlist over the same content, off by a frame."""
    ts = classify(make_plan([{"duration": 7200.0, "size": 3433},
                             {"duration": 7200.04, "size": 3433}]))
    assert ts[1].kind == SKIP


def test_real_episodes_are_not_mistaken_for_each_other(make_plan):
    """
    Regression: a relative tolerance made 1% of a 45-minute episode 27
    seconds wide, so three of these four were silently dropped as duplicates
    of the first — a whole disc reduced to one episode.
    """
    plan = make_plan([{"duration": d} for d in (2712, 2698, 2705, 2721)],
                     name="A Series (2016)", tv=True)
    ts = classify(plan)
    assert [t.kind for t in ts] == [EPISODE] * 4
    assert [t.label for t in ts] == ["1", "2", "3", "4"]


def test_similar_trailers_on_one_disc_are_all_kept(make_plan):
    """
    Regression, from a real Blu-ray. Its trailer reel carries several clips of
    near-identical length, and at 2 s / 1 % two genuine extras were dropped:
    137.0 s beside 138.5 s (1.6 MB apart on 345 MB), and 126.6 s beside
    126.8 s (0.67 MB apart on 257 MB).
    """
    disc = [{"duration": 126.6, "size": 256_941_482},
            {"duration": 126.8, "size": 257_607_777},
            {"duration": 137.0, "size": 343_379_403},
            {"duration": 138.5, "size": 345_022_622},
            {"duration": 7121.6, "size": 33_758_009_437}]
    ts = classify(make_plan([dict(d) for d in disc]))
    assert SKIP not in [t.kind for t in ts]


def test_an_alternate_cut_is_not_a_duplicate(make_plan):
    """118 minutes against 120 is a different cut, not the same content."""
    ts = classify(make_plan([{"duration": 7200}, {"duration": 7080}]))
    assert ts[0].kind == MAIN
    assert ts[1].kind == VERSION


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
