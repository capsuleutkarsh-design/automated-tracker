"""
Per-shot settings in a batch (roadmap 2.3).

Which shots solve with their own saved project and which with the controls is a
pure decision over (saved projects, selection, force flag), so it is tested
here rather than through a window - including the case that matters, a
selection where some shots have been set up and some never have.
"""
import pytest

from core import project as project_file
from core import workers as w


def _saved(**settings_3d):
    """A project file for a shot that has been set up, with these 3D settings."""
    data = project_file.default_project()
    data["settings_3d"].update(settings_3d)
    return project_file.migrate(data)


# --------------------------------------------------------------- the plan
def test_shots_with_a_project_use_it_and_the_rest_use_the_controls():
    saved = {"dolly_a": _saved(frame_step=2), "crane_b": _saved(frame_step=3)}
    plan = project_file.batch_settings_plan(
        saved, ["dolly_a", "crane_b", "handheld_c"])

    assert plan["saved"] == ["dolly_a", "crane_b"]
    assert plan["current"] == ["handheld_c"]
    assert plan["forced"] is False


def test_a_shot_whose_project_would_not_load_falls_back_to_the_controls():
    # load_project() hands back None for a file it had to move aside; that shot
    # must still solve, with what is on screen.
    plan = project_file.batch_settings_plan(
        {"dolly_a": None, "crane_b": _saved()}, ["dolly_a", "crane_b"])
    assert plan == {"saved": ["crane_b"], "current": ["dolly_a"], "forced": False}


def test_the_force_checkbox_puts_every_shot_on_the_controls():
    saved = {"dolly_a": _saved(), "crane_b": _saved()}
    plan = project_file.batch_settings_plan(saved, ["dolly_a", "crane_b"],
                                            force_current=True)
    assert plan["saved"] == []
    assert plan["current"] == ["dolly_a", "crane_b"]
    assert plan["forced"] is True


def test_the_selection_order_is_kept():
    saved = {"b": _saved(), "d": _saved()}
    plan = project_file.batch_settings_plan(saved, ["a", "b", "c", "d"])
    assert plan["saved"] == ["b", "d"]
    assert plan["current"] == ["a", "c"]


# ------------------------------------------------------------ the sentence
def test_the_mixed_summary_counts_both_kinds():
    plan = {"saved": ["a", "b", "c"], "current": ["d", "e"], "forced": False}
    assert project_file.batch_summary(plan) == (
        "Solving 5 shots: 3 with their own saved settings, 2 with the settings "
        "on screen.")


def test_the_summary_reads_for_one_shot_and_for_all_of_them():
    assert project_file.batch_summary(
        {"saved": ["a"], "current": [], "forced": False}
    ) == "Solving 1 shot with its own saved settings."
    assert project_file.batch_summary(
        {"saved": ["a", "b"], "current": [], "forced": False}
    ) == "Solving 2 shots with their own saved settings."
    assert project_file.batch_summary(
        {"saved": [], "current": ["a", "b"], "forced": False}
    ) == "Solving 2 shots with the settings on screen."


def test_the_forced_summary_says_the_saved_settings_are_ignored():
    text = project_file.batch_summary(
        {"saved": [], "current": ["a", "b", "c"], "forced": True})
    assert text.startswith("Solving 3 shots with the settings on screen")
    assert "ignored" in text


def test_an_empty_selection_does_not_claim_to_be_solving_anything():
    assert project_file.batch_summary(
        {"saved": [], "current": [], "forced": False}) == "No shots to solve."


# ------------------------------------------------- the config for one shot
def _base_config():
    """What the 3D tab holds: the settings of whichever shot was open last."""
    return {
        "solver_engine": "Incremental SfM (Standard / All GPUs)",
        "camera_model": "SIMPLE_RADIAL",
        "tri_angle": 2.5,
        "overlap": 35,
        "inliers": 40,
        "frame_step": 1,
        "timeline_start": 1,
        "fps": 24.0,
        "pixel_aspect": 1.0,
        "overscan": 0.0,
        "write_undistort": False,
        "single_camera": True,
        "use_gpu": True,
        "generate_mesh": False,
        "enable_caspar_ba": True,
        "ba_refine_distortion": True,
        "max_image_size": 4096,
        "init_max_forward_motion": 1.0,
        "animated_masks": [{"id": "m1"}],
        "mask_shot": "handheld_c",
        "scene_transform": None,
        "blender_path": None,
    }


def test_a_shots_own_settings_win_over_the_controls():
    data = _saved(camera_model="OPENCV_FISHEYE (Wide-Angle / Action Cam)",
                  frame_step=3, overlap=20, inliers=60, tri_angle=8.0,
                  write_undistort=True, overscan=0.25, pixel_aspect=2.0,
                  single_camera=False, generate_mesh=True, use_gpu=False,
                  caspar_ba=False, ba_refine_distortion=False,
                  solver_engine="Hierarchical Multi-Cluster Mapper (faster on long shots)")
    data["timeline_start"] = 1001
    data["fps"] = 25.0
    data["scene_transform"] = {"scale": 2.0, "rotation": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                               "translation": [0.0, 0.0, 0.0]}

    cfg = project_file.solve_config_from_project(_base_config(), data)

    # COLMAP is handed the first token of the label, exactly as the window does.
    assert cfg["camera_model"] == "OPENCV_FISHEYE"
    assert cfg["solver_engine"].startswith("Hierarchical")
    assert (cfg["frame_step"], cfg["overlap"], cfg["inliers"]) == (3, 20, 60)
    assert cfg["tri_angle"] == 8.0
    assert (cfg["write_undistort"], cfg["overscan"]) == (True, 0.25)
    assert cfg["pixel_aspect"] == 2.0
    assert (cfg["timeline_start"], cfg["fps"]) == (1001, 25.0)
    assert cfg["scene_transform"]["scale"] == 2.0
    assert cfg["single_camera"] is False
    assert cfg["generate_mesh"] is True
    assert cfg["use_gpu"] is False
    assert cfg["enable_caspar_ba"] is False
    assert cfg["ba_refine_distortion"] is False


def test_what_belongs_to_the_run_rather_than_the_shot_is_left_alone():
    cfg = project_file.solve_config_from_project(_base_config(), _saved())
    # Masks still belong to the clip they were drawn on, and the working image
    # size is a property of the machine, not of the shot.
    assert cfg["animated_masks"] == [{"id": "m1"}]
    assert cfg["mask_shot"] == "handheld_c"
    assert cfg["max_image_size"] == 4096


def test_a_saved_preset_brings_back_its_forward_motion_limit():
    presets = {"Walking / Handheld": {"init_max_forward_motion": 0.7}}
    cfg = project_file.solve_config_from_project(
        _base_config(), _saved(preset="Walking / Handheld"), presets)
    assert cfg["init_max_forward_motion"] == 0.7

    # A preset this build does not know simply leaves the value alone.
    cfg = project_file.solve_config_from_project(
        _base_config(), _saved(preset="From A Newer Build"), presets)
    assert cfg["init_max_forward_motion"] == 1.0


def test_an_empty_blender_path_does_not_replace_the_detected_one():
    base = _base_config()
    base["blender_path"] = r"C:\Blender\blender.exe"
    cfg = project_file.solve_config_from_project(base, _saved(blender_path=""))
    assert cfg["blender_path"] == r"C:\Blender\blender.exe"


# --------------------------------------------- one config, or one per video
def test_a_single_config_still_means_the_whole_run():
    videos = ["C:/v/a.mp4", "C:/v/b.mp4"]
    base, mapping = w.configs_for_videos(videos, {"frame_step": 4})
    assert base == {"frame_step": 4}
    assert mapping == {}

    worker = w.TrackerWorker(videos, {"frame_step": 4},
                             ".", ".", "colmap", ".", "ffmpeg", ".")
    assert worker.config == {"frame_step": 4}
    for v in videos:
        assert worker.config_for(v)["frame_step"] == 4


def test_a_dict_keyed_by_video_gives_each_shot_its_own():
    videos = ["C:/v/a.mp4", "C:/v/b.mp4"]
    worker = w.TrackerWorker(
        videos,
        {"C:/v/a.mp4": {"frame_step": 2}, "C:/v/b.mp4": {"frame_step": 5}},
        ".", ".", "colmap", ".", "ffmpeg", ".")
    assert worker.config_for("C:/v/a.mp4")["frame_step"] == 2
    assert worker.config_for("C:/v/b.mp4")["frame_step"] == 5
    # The same path written another way is the same shot.
    assert worker.config_for(r"C:\v\B.mp4")["frame_step"] == 5


def test_a_list_parallel_to_the_videos_works_too():
    videos = ["C:/v/a.mp4", "C:/v/b.mp4"]
    worker = w.TrackerWorker(videos, [{"frame_step": 2}, {"frame_step": 5}],
                             ".", ".", "colmap", ".", "ffmpeg", ".")
    assert worker.config_for("C:/v/a.mp4")["frame_step"] == 2
    assert worker.config_for("C:/v/b.mp4")["frame_step"] == 5


def test_a_shot_missing_from_the_mapping_falls_back_rather_than_raising():
    videos = ["C:/v/a.mp4", "C:/v/b.mp4"]
    worker = w.TrackerWorker(videos, {"C:/v/a.mp4": {"frame_step": 2}},
                             ".", ".", "colmap", ".", "ffmpeg", ".")
    assert worker.config_for("C:/v/b.mp4")["frame_step"] == 2


def test_an_empty_run_keeps_the_single_config_path():
    # test_3d_register builds a worker with no videos at all; that must keep
    # meaning "these settings", not "a mapping of no shots".
    worker = w.TrackerWorker([], {"timeline_start": 1001},
                             ".", ".", "colmap", ".", "ffmpeg", ".")
    assert worker.config == {"timeline_start": 1001}


# ------------------------------------------------------------- end to end
def test_two_shots_with_different_saved_steps_are_batched_with_their_own(tmp_path):
    """The whole 2.3 path: two saved projects in, two solve configs out."""
    scenes = tmp_path / "04 SCENES"
    for name, step, start in (("dolly_a", 2, 1001), ("crane_b", 5, 1)):
        data = project_file.default_project()
        data["settings_3d"]["frame_step"] = step
        data["settings_3d"]["camera_model"] = "OPENCV (Radial + tangential)"
        data["timeline_start"] = start
        project_file.save_project(project_file.project_path(scenes, name), data)

    videos = [tmp_path / "dolly_a.mp4", tmp_path / "crane_b.mp4",
              tmp_path / "handheld_c.mp4"]
    names = [v.stem for v in videos]
    saved = {n: project_file.load_project(project_file.project_path(scenes, n))
             for n in names}
    plan = project_file.batch_settings_plan(saved, names)
    assert project_file.batch_summary(plan) == (
        "Solving 3 shots: 2 with their own saved settings, 1 with the settings "
        "on screen.")

    base = _base_config()
    configs = {
        str(v): (project_file.solve_config_from_project(base, saved[n])
                 if n in set(plan["saved"]) else dict(base))
        for v, n in zip(videos, names)
    }
    worker = w.TrackerWorker(videos, configs, ".", ".", "colmap", ".", "ffmpeg", ".")

    assert worker.config_for(videos[0])["frame_step"] == 2
    assert worker.config_for(videos[0])["timeline_start"] == 1001
    assert worker.config_for(videos[1])["frame_step"] == 5
    assert worker.config_for(videos[1])["camera_model"] == "OPENCV"
    # The shot that has never been saved keeps what is on screen.
    assert worker.config_for(videos[2])["frame_step"] == 1
    assert worker.config_for(videos[2])["camera_model"] == "SIMPLE_RADIAL"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
