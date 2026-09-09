"""The one part that needs real media: ffprobe. Skips when ffmpeg is absent."""

import sort2own


def test_scan_reads_duration_and_tag_in_disc_order(make_rip, src_dir):
    make_rip([{"duration": 8}, {"duration": 4, "tag": "Trailer"}])
    titles = sort2own.scan(src_dir)
    assert [t.path.name for t in titles] == ["Film_t00.mkv", "Film_t01.mkv"]
    assert round(titles[0].duration) == 8
    assert titles[1].tag == "Trailer"
    assert all(t.size > 0 for t in titles)


def test_scan_ignores_non_mkv_files(make_rip, src_dir):
    make_rip([{"duration": 4}])
    (src_dir / "disc.log").write_text("MakeMKV log")
    assert len(sort2own.scan(src_dir)) == 1


def test_classify_end_to_end_on_real_rips(make_rip, src_dir, library):
    make_rip([{"duration": 20}, {"duration": 18}, {"duration": 4,
                                                   "tag": "Trailer"}])
    plan = sort2own.Plan(name="Test Film (2024)", library=library,
                         titles=sort2own.scan(src_dir))
    sort2own.classify(plan, None, 3, 0.85, 0.01)
    assert [t.kind for t in plan.titles] == [
        sort2own.MAIN, sort2own.VERSION, sort2own.EXTRA]
