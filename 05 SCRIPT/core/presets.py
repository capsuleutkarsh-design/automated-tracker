"""
Shot presets for the 3D (COLMAP) solver.

Each preset carries every key the solver config is built from in
tracker_gui._start_tracking_3d, so switching presets never leaves a value
unset. PRESET_KEYS lists those keys; tests check every preset against it.
"""

# Keys every preset must define (besides "description").
PRESET_KEYS = (
    "solver_engine",
    "tri_angle",
    "init_max_forward_motion",
    "overlap",
    "inliers",
    "camera_model",
    "single_camera",
    "use_gpu",
    "max_image_size",
    "frame_step",
)

DEFAULT_PRESET = "Custom (Manual Tuning)"

PRESETS = {
    "Handheld / Walking (Recommended)": {
        "description": "Optimized for moving camera shots (walking, crane, handheld). Uses a low initial triangulation angle and allows forward-dominant motion, which COLMAP otherwise rejects on walk-forward shots.",
        "solver_engine": "Incremental",
        "tri_angle": 2.5,
        "init_max_forward_motion": 1.0,
        "overlap": 35,
        "inliers": 40,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Hierarchical Multi-Cluster (Long Shots)": {
        "description": "Splits a long shot into overlapping clusters and solves them in parallel before merging, which is faster than a single incremental pass on long takes. Falls back to the incremental mapper automatically if the cluster solve fails.",
        "solver_engine": "Hierarchical",
        "tri_angle": 2.5,
        "init_max_forward_motion": 1.0,
        "overlap": 35,
        "inliers": 40,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "360° VR / Panoramic (Insta360 / GoPro Max)": {
        "description": "For 360 rigs: solve the raw fisheye lens footage, not the stitched equirectangular. COLMAP has no spherical model, so OPENCV_FISHEYE is used.",
        "solver_engine": "Incremental",
        "tri_angle": 3.0,
        "init_max_forward_motion": 1.0,
        "overlap": 25,
        "inliers": 50,
        "camera_model": "OPENCV_FISHEYE",  # 360 stitches are not a COLMAP model; fisheye is the closest real one
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Drone / Aerial Orbit": {
        "description": "Optimized for outdoor and high-altitude shots with wide parallax and high keypoint count.",
        "solver_engine": "Incremental",
        "tri_angle": 12.0,
        "init_max_forward_motion": 0.95,
        "overlap": 20,
        "inliers": 100,
        "camera_model": "OPENCV",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Slow / Subtle Motion (Small Movement)": {
        "description": "Very forgiving on small camera movements. Subsamples frames to increase baseline and lowers initialization angle.",
        "solver_engine": "Incremental",
        "tri_angle": 3.0,
        "init_max_forward_motion": 1.0,
        "overlap": 15,
        "inliers": 40,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 2
    },
    "Fast Action / Quick Turns": {
        "description": "Increases matching overlap window (30 frames) to maintain tracking during rapid camera motion.",
        "solver_engine": "Incremental",
        "tri_angle": 8.0,
        "init_max_forward_motion": 1.0,
        "overlap": 30,
        "inliers": 50,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Action Cam / GoPro / Fisheye": {
        "description": "Uses Fisheye distortion model for wide-angle and action camera lenses.",
        "solver_engine": "Incremental",
        "tri_angle": 6.0,
        "init_max_forward_motion": 1.0,
        "overlap": 20,
        "inliers": 60,
        "camera_model": "OPENCV_FISHEYE",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Standard / Default": {
        "description": "Default COLMAP settings.",
        "solver_engine": "Incremental",
        "tri_angle": 16.0,
        "init_max_forward_motion": 1.0,
        "overlap": 15,
        "inliers": 100,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Custom (Manual Tuning)": {
        "description": "Unlock all parameters for full manual control.",
        "solver_engine": "Incremental",
        "tri_angle": 6.0,
        "init_max_forward_motion": 1.0,
        "overlap": 20,
        "inliers": 60,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    }
}
