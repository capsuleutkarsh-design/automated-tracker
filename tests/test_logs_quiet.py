"""
Quiet logs and an honest progress bar (roadmap 2.4).

The sample below is what COLMAP actually prints through a solve: a glog INFO
line per image, a couple of real complaints buried in them, and a final report
that mentions "error" without anything being wrong. The console must keep the
second kind and drop the first, and never drop the third by accident.
"""
import pytest

from core import project as project_file
from core import workers as w


COLMAP_SAMPLE = """\
==============================================================================
Feature extraction
==============================================================================
I20240517 11:02:14.883921  8124 feature_extraction.cc:255] Processed file [1/240]
I20240517 11:02:14.883930  8124 feature_extraction.cc:258]   Name:            frame_000001.jpg
I20240517 11:02:14.883940  8124 feature_extraction.cc:259]   Dimensions:      1920 x 1080
I20240517 11:02:14.884000  8124 feature_extraction.cc:262]   Features:        4096
I20240517 11:02:15.100112  8124 feature_extraction.cc:255] Processed file [2/240]
I20240517 11:02:15.100130  8124 feature_extraction.cc:262]   Features:        3987
W20240517 11:02:15.220400  8124 database.cc:1021] Database file already contains images with different camera parameters
Elapsed time: 0.451 [minutes]
==============================================================================
Sequential feature matching
==============================================================================
I20240517 11:04:02.110000  8124 sequential_matching.cc:96] Matching block [1/8, 1/8]
I20240517 11:04:31.340000  8124 sequential_matching.cc:96] Matching block [2/8, 1/8]
Elapsed time: 5.210 [minutes]
==============================================================================
Loading database
==============================================================================
I20240517 11:09:40.100000  8124 database_cache.cc:54] Loading cameras... 1 in 0.000s
I20240517 11:09:40.101000  8124 database_cache.cc:64] Loading matches... 3382 in 0.041s
E20240517 11:09:41.552310  8124 incremental_mapper.cc:214] Failed to initialize the reconstruction
=> No good initial image pair found.
  => Could not register, trying another image.
I20240517 11:10:02.000000  8124 incremental_mapper.cc:300] Registering image #17 (12)
I20240517 11:10:02.010000  8124 incremental_mapper.cc:312]   => Image sees 421 / 1022 points
I20240517 11:10:02.900000  8124 incremental_mapper.cc:355] Triangulated 233 points
==============================================================================
Bundle adjustment report
==============================================================================
  Residuals : 20444
  Parameters : 6141
  Mean reprojection error: 0.641718px
  Filtered observations with a reprojection error above max_error 4.000000
ERROR: Could not open the database at D:/shots/dolly_a/database.db
"""


@pytest.fixture
def colmap_lines():
    return [line.strip() for line in COLMAP_SAMPLE.splitlines() if line.strip()]


# ------------------------------------------------------------- the filter
def test_every_real_problem_is_kept(colmap_lines):
    problems = [l for l in colmap_lines if w.engine_line_kind(l) == "problem"]
    assert problems == [
        "W20240517 11:02:15.220400  8124 database.cc:1021] Database file already "
        "contains images with different camera parameters",
        "E20240517 11:09:41.552310  8124 incremental_mapper.cc:214] Failed to "
        "initialize the reconstruction",
        "=> No good initial image pair found.",
        "=> Could not register, trying another image.",
        "ERROR: Could not open the database at D:/shots/dolly_a/database.db",
    ]


def test_the_info_chatter_is_dropped(colmap_lines):
    kept = [l for l in colmap_lines if w.engine_line_kind(l) != "chatter"]
    # Ten INFO lines are worth a colour of their own when the stream is shown,
    # because they say how far a stage has got; the rest is just chatter.
    assert len([l for l in colmap_lines if w.engine_line_kind(l) == "progress"]) == 10
    assert len(kept) < len(colmap_lines) / 2
    for line in colmap_lines:
        if line.startswith("=====") or "Parameters :" in line:
            assert w.engine_line_kind(line) == "chatter"


def test_a_reported_reprojection_error_is_not_a_problem():
    assert w.engine_line_kind("  Mean reprojection error: 0.641718px") != "problem"
    assert w.engine_line_kind(
        "  Filtered observations with a reprojection error above max_error 4.000000"
    ) != "problem"
    assert w.engine_line_kind("--Mapper.filter_max_reproj_error=4") != "problem"


def test_progress_lines_are_told_from_the_rest():
    assert w.engine_line_kind("Elapsed time: 0.451 [minutes]") == "progress"
    assert w.engine_line_kind(
        "I20240517 11:10:02.000000 8124 incremental_mapper.cc:300] "
        "Registering image #17 (12)") == "progress"
    assert w.engine_line_kind("") == "chatter"


def test_a_glog_warning_reaches_the_panel_whatever_it_says():
    # The severity letter is enough; the wording does not have to be recognised.
    assert w.engine_line_kind(
        "W20240517 11:02:15.220400 8124 sift.cc:88] Something we have never seen"
    ) == "problem"
    assert w.engine_line_kind(
        "F20240517 11:02:15.220400 8124 sift.cc:88] Check failed") == "problem"
    assert w.engine_line_kind(
        "I20240517 11:02:15.220400 8124 sift.cc:88] Something we have never seen"
    ) == "chatter"


def _worker(videos=(), config=None):
    return w.TrackerWorker(list(videos), dict(config or {}),
                           ".", ".", "colmap", ".", "ffmpeg", ".")


def test_the_console_only_sees_the_problems_until_the_toggle_is_on(colmap_lines):
    worker = _worker()
    shown = []
    worker.log_signal.connect(lambda text, colour: shown.append((text.strip(), colour)))

    for line in colmap_lines:
        worker._handle_engine_line(line)
    assert len(shown) == 5
    assert all(colour == "#ff7878" for _t, colour in shown)
    assert "No good initial image pair found." in shown[2][0]

    shown.clear()
    worker.show_engine_output = True
    for line in colmap_lines:
        worker._handle_engine_line(line)
    assert len(shown) == len(colmap_lines)


# ----------------------------------------------------------- the estimate
def test_with_no_stored_timing_the_estimate_comes_from_the_frame_count():
    assert project_file.estimate_solve_seconds(200, []) == pytest.approx(
        200 * project_file.DEFAULT_SECONDS_PER_FRAME)
    assert project_file.estimate_solve_seconds(200, None) == pytest.approx(
        200 * project_file.DEFAULT_SECONDS_PER_FRAME)
    # A handful of frames still gets a bar that has somewhere to travel.
    assert project_file.estimate_solve_seconds(2, []) == project_file.MIN_SOLVE_SECONDS


def test_a_stored_timing_of_a_similar_size_is_scaled_to_this_shot():
    timings = [{"frames": 100, "seconds": 600.0}]
    assert project_file.estimate_solve_seconds(100, timings) == pytest.approx(600.0)
    assert project_file.estimate_solve_seconds(150, timings) == pytest.approx(900.0)
    assert project_file.estimate_solve_seconds(50, timings) == pytest.approx(300.0)


def test_a_solve_of_a_very_different_length_is_not_scaled_from():
    timings = [{"frames": 10, "seconds": 30.0}]
    # Twenty times the frames says nothing useful about this shot, so the
    # default rate is used instead of a wild extrapolation.
    assert project_file.estimate_solve_seconds(200, timings) == pytest.approx(
        200 * project_file.DEFAULT_SECONDS_PER_FRAME)


def test_the_closest_stored_solve_is_the_one_used():
    timings = [{"frames": 240, "seconds": 2400.0}, {"frames": 120, "seconds": 300.0}]
    assert project_file.estimate_solve_seconds(120, timings) == pytest.approx(300.0)


def test_a_finished_solve_is_remembered_newest_first_and_bounded(tmp_path):
    data = project_file.default_project()
    for n in range(10):
        project_file.record_solve_timing(data, 100 + n, 60.0 + n)
    assert len(data["solve_timings"]) == project_file.KEEP_SOLVE_TIMINGS
    assert data["solve_timings"][0]["frames"] == 109

    # A measurement that means nothing is not stored to mislead the next one.
    project_file.record_solve_timing(data, 0, 120.0)
    project_file.record_solve_timing(data, 100, 0)
    assert data["solve_timings"][0]["frames"] == 109

    # And it survives a round trip through the file.
    path = project_file.project_path(tmp_path, "dolly_a")
    project_file.save_project(path, data)
    assert project_file.load_project(path)["solve_timings"][0]["seconds"] == 69.0


def test_a_project_written_before_2_4_simply_has_no_timings(tmp_path):
    old = project_file.default_project()
    del old["solve_timings"]
    assert project_file.migrate(old)["solve_timings"] == []
    # Nonsense in the file is dropped rather than handed to the arithmetic.
    assert project_file.migrate({"solve_timings": [{"frames": "x"}, 7, {}]})[
        "solve_timings"] == []


# ------------------------------------------------------------ the bar itself
def test_the_bar_walks_forward_through_the_stages():
    seen = [w.solve_progress_percent(name, 0.0) for name, _span in w.SOLVE_STAGES]
    assert seen == sorted(seen)
    assert seen[0] == 0
    assert w.solve_progress_percent(w.SOLVE_STAGES[-1][0], 1.0) == 100


def test_a_stage_moves_with_its_clock_instead_of_jumping():
    start, span = w.stage_bounds("match")
    quarter = w.solve_progress_percent("match", w.stage_fraction(60, 240))
    half = w.solve_progress_percent("match", w.stage_fraction(120, 240))
    assert int(start * 100) < quarter < half < int((start + span) * 100) + 1
    # An overrunning stage creeps up to its end but never claims to be past it.
    assert w.stage_fraction(9999, 240) < 1.0
    assert w.stage_fraction(10, 0) == 0.0


def test_one_shot_of_a_batch_only_fills_its_own_share_of_the_bar():
    assert w.batch_progress_percent(1, 4, 100) == 25
    assert w.batch_progress_percent(3, 4, 50) == 62
    assert w.batch_progress_percent(1, 1, 40) == 40


def test_the_time_left_is_rounded_to_something_worth_reading():
    assert w.human_duration(20) == "under a minute"
    assert w.human_duration(260) == "about 4 min"
    assert w.human_duration(3600) == "about 1 h"
    assert w.human_duration(4260) == "about 1 h 11 min"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
