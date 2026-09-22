"""
Background Thread Workers for 3D Camera Tracking & 2D AI Tracking
"""

import os
import json
import shutil
import datetime
import traceback
from pathlib import Path
from PySide6.QtCore import QThread, Signal

from mask_animator import AnimatedMask, rasterize_masks_to_png
from core.media_info import probe_fps
from core.proc import popen_hidden
from core.colmap_model import find_best_model, model_stats


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.exr', '.tif', '.tiff'}


# -----------------------------------------------------------------------------
# images/ cache stamp (A14)
#
# 04 SCENES/<shot>/images is shared by the 2D scrubber (always every frame) and
# the 3D solve (every Nth frame when Frame Step > 1). Reusing whatever is in
# there regardless of how it was made gave a silently wrong solve and fps, so
# the folder is stamped with the step and the source it came from, and anything
# that does not match is thrown away and extracted again.
# -----------------------------------------------------------------------------
def images_stamp_path(img_dir):
    return Path(img_dir).parent / "images_cache.json"


def _source_signature(source):
    p = Path(source)
    try:
        if p.is_dir():
            files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
            mtime = max((f.stat().st_mtime for f in files), default=p.stat().st_mtime)
            return {"source": str(p), "source_mtime": round(mtime, 3), "source_count": len(files)}
        st = p.stat()
        return {"source": str(p), "source_mtime": round(st.st_mtime, 3), "source_size": st.st_size}
    except OSError:
        return {"source": str(p)}


def write_images_stamp(img_dir, source, frame_step, frame_count):
    stamp = _source_signature(source)
    stamp.update({"frame_step": int(frame_step), "frame_count": int(frame_count)})
    try:
        with open(images_stamp_path(img_dir), "w", encoding="utf-8") as f:
            json.dump(stamp, f, indent=2)
    except OSError:
        pass


def images_stamp_matches(img_dir, source, frame_step):
    """True when images/ was extracted from this source at this step."""
    try:
        with open(images_stamp_path(img_dir), "r", encoding="utf-8") as f:
            stamp = json.load(f)
    except (OSError, ValueError):
        return False
    if int(stamp.get("frame_step", 0)) != int(frame_step):
        return False
    return all(stamp.get(k) == v for k, v in _source_signature(source).items())


def list_extracted_frames(img_dir):
    img_dir = Path(img_dir)
    if not img_dir.is_dir():
        return []
    return sorted(f for f in img_dir.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS)


def clear_extracted_frames(img_dir):
    for f in list_extracted_frames(img_dir):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        images_stamp_path(img_dir).unlink()
    except OSError:
        pass


class TrackerWorker(QThread):
    log_signal = Signal(str, str)
    progress_signal = Signal(int, str)
    video_status_signal = Signal(str, str)
    finished_signal = Signal(bool, str)

    def __init__(self, video_paths, config, base_dir, colmap_dir, colmap_exe, ffmpeg_dir, ffmpeg_exe, scenes_dir):
        super().__init__()
        self.video_paths = video_paths
        self.config = config
        self.base_dir = Path(base_dir)
        self.colmap_dir = Path(colmap_dir)
        self.colmap_exe = Path(colmap_exe)
        self.ffmpeg_dir = Path(ffmpeg_dir)
        self.ffmpeg_exe = Path(ffmpeg_exe)
        self.scenes_dir = Path(scenes_dir)
        self.is_cancelled = False
        self.process = None

    def run(self):
        total_videos = len(self.video_paths)
        if total_videos == 0:
            self.finished_signal.emit(False, "No videos selected.")
            return

        solved_count = 0
        finished_emitted = False
        try:
            self.scenes_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()

            colmap_bin = str(self.colmap_dir / "bin")
            colmap_plugins = str(self.colmap_dir / "plugins")
            ffmpeg_bin = str(self.ffmpeg_dir / "bin")
            env["PATH"] = f"{colmap_bin};{ffmpeg_bin};{self.colmap_dir};{self.ffmpeg_dir};" + env.get("PATH", "")
            env["QT_PLUGIN_PATH"] = f"{colmap_plugins};" + env.get("QT_PLUGIN_PATH", "")

            for idx, video_path in enumerate(self.video_paths, start=1):
                if self.is_cancelled:
                    break
                video_name = Path(video_path).name
                # Anything that escapes one shot (EXR the PIL build cannot open, a
                # PermissionError from copy2, ...) used to kill the thread and leave
                # the GUI locked; now it is logged and the batch moves on (B9, C20).
                try:
                    if self._process_video(idx, total_videos, video_path, env):
                        solved_count += 1
                except Exception:
                    self.log_signal.emit(f"✖ Unexpected error while processing {video_name}:", "#ff4b4b")
                    for line in traceback.format_exc().rstrip().splitlines():
                        self.log_signal.emit(f"   {line}", "#ff7878")
                    self.video_status_signal.emit(video_name, "Error ✖")

            if self.is_cancelled:
                self.progress_signal.emit(0, "3D Tracking Cancelled")
                self.finished_signal.emit(False, f"⏹ 3D tracking cancelled ({solved_count}/{total_videos} solved before stopping).")
            elif solved_count > 0:
                self.progress_signal.emit(100, "All 3D Tracking Jobs Finished")
                self.finished_signal.emit(True, f"✔ 3D tracking successfully completed ({solved_count}/{total_videos} solved).")
            else:
                self.progress_signal.emit(100, "All 3D Tracking Jobs Finished")
                self.finished_signal.emit(False, "✖ 3D tracking failed to reconstruct scene. Check engine diagnostics.")
            finished_emitted = True
        except Exception:
            self.log_signal.emit("✖ 3D tracking worker crashed:", "#ff4b4b")
            for line in traceback.format_exc().rstrip().splitlines():
                self.log_signal.emit(f"   {line}", "#ff7878")
        finally:
            self._kill_process()
            if not finished_emitted:
                self.finished_signal.emit(False, "✖ 3D tracking stopped on an internal error. See the console.")

    def _process_video(self, idx, total_videos, video_path, env):
        """One shot end to end. Returns True only when it solved AND exported."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        video = Path(video_path)
        base_name = video.stem
        shot_dir = self.scenes_dir / base_name
        img_dir = shot_dir / "images"
        track_dir = shot_dir / "3D_CAMERA_TRACK" / timestamp
        sparse_dir = track_dir / "sparse"
        db_path = track_dir / "database.db"

        self.log_signal.emit(f"\n==================================================", "#00d2ff")
        self.log_signal.emit(f"[{idx}/{total_videos}] Processing 3D Track: {video.name}", "#00d2ff")
        self.log_signal.emit(f"==================================================", "#00d2ff")
        self.video_status_signal.emit(video.name, "Processing...")

        img_dir.mkdir(parents=True, exist_ok=True)
        sparse_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Frame Extraction / Sequence Loading
        frame_step = max(1, self.config.get("frame_step", 1))
        extracted_frames = list_extracted_frames(img_dir)
        if extracted_frames and not images_stamp_matches(img_dir, video, frame_step):
            # Frames from another step, another clip with this name, or a run that
            # predates the stamp: never trust them (A14).
            self.log_signal.emit(
                f"   images/ holds {len(extracted_frames)} frames that were not extracted from "
                f"{video.name} at step {frame_step} - re-extracting.", "#e0a000")
            clear_extracted_frames(img_dir)
            extracted_frames = []
        if not extracted_frames:
            if video.is_dir():
                self.progress_signal.emit(10, f"[{idx}/{total_videos}] [1/4] Loading image sequence frames...")
                self.log_signal.emit(f"▶ [1/4] Importing image sequence from folder...", "#ffffff")
                seq_files = sorted([f for f in video.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS])
                if frame_step > 1:
                    seq_files = seq_files[::frame_step]
                for s_idx, sf in enumerate(seq_files, start=1):
                    if self.is_cancelled:
                        return False
                    dest_name = f"frame_{s_idx:06d}{sf.suffix.lower()}"
                    shutil.copy2(sf, img_dir / dest_name)
                extracted_frames = list_extracted_frames(img_dir)
            else:
                self.progress_signal.emit(10, f"[{idx}/{total_videos}] [1/4] Extracting frames...")
                self.log_signal.emit(f"▶ [1/4] Extracting frames with FFmpeg...", "#ffffff")

                vf_filter = f"select=not(mod(n\\,{frame_step}))" if frame_step > 1 else None

                ffmpeg_cmd = [
                    str(self.ffmpeg_exe), "-loglevel", "error", "-stats",
                    "-i", str(video)
                ]
                if vf_filter:
                    ffmpeg_cmd.extend(["-vf", vf_filter, "-vsync", "vfr"])
                ffmpeg_cmd.extend(["-qscale:v", "2", str(img_dir / "frame_%06d.jpg")])

                if not self._run_command(ffmpeg_cmd, env, "FFmpeg Extraction"):
                    self.video_status_signal.emit(video.name, "Cancelled" if self.is_cancelled else "FFmpeg Failed ✖")
                    return False

                extracted_frames = list_extracted_frames(img_dir)
            if extracted_frames:
                write_images_stamp(img_dir, video, frame_step, len(extracted_frames))
        else:
            self.log_signal.emit(f"▶ [1/4] Using {len(extracted_frames)} existing frames from images/ folder (step {frame_step}).", "#00ff88")

        if not extracted_frames:
            self.log_signal.emit(f"✖ Error: No frames extracted for {video.name}", "#ff4b4b")
            self.video_status_signal.emit(video.name, "No Frames ✖")
            return False
        self.log_signal.emit(f"✔ Extracted {len(extracted_frames)} frames.", "#00ff88")

        # Step 1.5: Automatic Dynamic Mask Generation for COLMAP
        masks_dir = None
        animated_masks = self.config.get("animated_masks")
        mask_shot = self.config.get("mask_shot")
        if animated_masks and mask_shot and base_name != mask_shot:
            # Roto was drawn against a different clip on the 2D tab; applying it here
            # would mask out the wrong part of this shot.
            self.log_signal.emit(
                f"   Skipping roto masks for '{base_name}' - they were drawn on '{mask_shot}'.",
                "#a0a0b0")
            animated_masks = None
        if animated_masks and extracted_frames:
            self.log_signal.emit("▶ [1.5/4] Rasterizing dynamic roto masks for 3D Camera Tracking...", "#00d2ff")
            masks_dir = shot_dir / "masks"
            masks_dir.mkdir(parents=True, exist_ok=True)
            sorted_frame_names = [f.name for f in sorted(extracted_frames)]
            from PIL import Image
            im_sample = Image.open(extracted_frames[0])
            w, h = im_sample.size

            mask_objs = []
            for m in animated_masks:
                if isinstance(m, dict):
                    mask_objs.append(AnimatedMask.from_dict(m))
                else:
                    mask_objs.append(m)

            rasterize_masks_to_png(
                mask_objs, w, h, masks_dir,
                len(extracted_frames), sorted_frame_names,
                progress_callback=lambda cur, tot: self.progress_signal.emit(
                    int(30 + (cur / tot) * 5), f"Generating 3D Masks ({cur}/{tot})..."
                ),
                frame_step=frame_step,
            )
            self.log_signal.emit(f"✔ Generated {len(extracted_frames)} binary masks in 04 SCENES/{video.stem}/masks/ (Excluding moving actors from 3D solve)!", "#00ff88")

        # Step 2: Feature Extraction
        self.progress_signal.emit(35, f"[{idx}/{total_videos}] [2/4] Feature Extraction...")
        self.log_signal.emit(f"▶ [2/4] Extracting SIFT features...", "#ffffff")
        feat_cmd = [
            str(self.colmap_exe), "feature_extractor",
            "--database_path", str(db_path),
            "--image_path", str(img_dir),
            "--ImageReader.camera_model", self.config.get("camera_model", "SIMPLE_RADIAL"),
            "--ImageReader.single_camera", "1" if self.config.get("single_camera", True) else "0",
            "--SiftExtraction.use_gpu", "1" if self.config.get("use_gpu", True) else "0",
            "--SiftExtraction.max_image_size", str(self.config.get("max_image_size", 4096))
        ]

        if masks_dir and masks_dir.exists():
            feat_cmd.extend(["--ImageReader.mask_path", str(masks_dir)])

        if not self._run_command(feat_cmd, env, "COLMAP Feature Extraction"):
            self.video_status_signal.emit(video.name, "Cancelled" if self.is_cancelled else "Feature Extractor Failed ✖")
            return False

        # Step 3: Sequential Matching (Local Vocab Tree Support)
        overlap = self.config.get("overlap", 35)
        self.progress_signal.emit(60, f"[{idx}/{total_videos}] [3/4] Sequential Matching...")
        self.log_signal.emit(f"▶ [3/4] Matching sequential features (overlap={overlap})...", "#ffffff")

        vocab_tree_path = self.colmap_dir / "vocab_tree_faiss_flickr100K_words256K.bin"
        if not vocab_tree_path.exists():
            for alt in self.colmap_dir.glob("*vocab*.bin"):
                vocab_tree_path = alt
                break

        match_cmd = [
            str(self.colmap_exe), "sequential_matcher",
            "--database_path", str(db_path),
            "--SequentialMatching.overlap", str(overlap),
        ]

        if vocab_tree_path.exists():
            self.log_signal.emit(f"   ✔ Using local Vocabulary Tree: {vocab_tree_path.name} (Offline Loop Detection)", "#00d2ff")
            match_cmd.extend([
                "--SequentialMatching.vocab_tree_path", str(vocab_tree_path),
                "--SequentialMatching.loop_detection", "1"
            ])
        else:
            match_cmd.extend([
                "--SequentialMatching.loop_detection", "0"
            ])

        if not self._run_command(match_cmd, env, "COLMAP Sequential Matcher"):
            self.video_status_signal.emit(video.name, "Cancelled" if self.is_cancelled else "Matching Failed ✖")
            return False

        # Step 4: Mapper (GLOMAP Global Structure-from-Motion vs Incremental Mapper)
        def solved_model():
            """
            The best reconstruction in sparse/, not blindly sparse/0.

            COLMAP writes one folder per reconstruction. When its first
            initialisation produces a degenerate pair and it starts again, the
            good solve lands in sparse/1 while sparse/0 keeps a two-image
            model - and exporting sparse/0 gives a two-camera track from a run
            that actually registered every frame.
            """
            return find_best_model(
                sparse_dir,
                log=lambda m: self.log_signal.emit(m, "#e0a000"))

        solver_engine = self.config.get("solver_engine", "Incremental")
        is_global_solver = any(
            key in solver_engine for key in ("Hierarchical", "GLOMAP", "Global")
        )
        model_0 = sparse_dir / "0"

        if is_global_solver:
            self.progress_signal.emit(80, f"[{idx}/{total_videos}] [4/4] Fast Hierarchical / Global Structure-from-Motion...")
            self.log_signal.emit(f"▶ [4/4] Executing Fast Hierarchical Multi-Cluster Mapper (Parallel Sub-Cluster Solving)...", "#00d2ff")

            hier_cmd = [
                str(self.colmap_exe), "hierarchical_mapper",
                "--database_path", str(db_path),
                "--image_path", str(img_dir),
                "--output_path", str(sparse_dir),
                "--Mapper.ba_use_gpu", "1" if (self.config.get("enable_caspar_ba", True) and self.config.get("use_gpu", True)) else "0",
                "--Mapper.num_threads", str(os.cpu_count() or 4)
            ]
            self._run_command(hier_cmd, env, "COLMAP Hierarchical Mapper")
            if self.is_cancelled:
                self.video_status_signal.emit(video.name, "Cancelled")
                return False

            # If fast mapper wrote model files directly into sparse_dir (cameras.bin/txt, etc.), move them into sparse_dir/0
            direct_cameras = list(sparse_dir.glob("cameras.*"))
            if direct_cameras and solved_model() is None:
                model_0.mkdir(parents=True, exist_ok=True)
                for pattern in ["cameras.*", "images.*", "points3D.*", "*.bin", "*.txt"]:
                    for f in sparse_dir.glob(pattern):
                        if f.is_file():
                            try:
                                shutil.move(str(f), str(model_0 / f.name))
                            except Exception:
                                pass

            if solved_model() is None:
                self.log_signal.emit(f"↻ Notice: Fast Hierarchical solve not produced – falling back seamlessly to Incremental Mapper...", "#e0a000")
            else:
                self.log_signal.emit(f"✔ Fast Mapper successfully solved 3D camera trajectory!", "#00ff88")

        # 'Auto-Refine Lens Distortion (BA)' on the 3D tab used to be ignored entirely.
        refine_flag = "1" if self.config.get("ba_refine_distortion", True) else "0"

        # COLMAP rejects an initial image pair whose motion is mostly forward
        # (init_max_forward_motion, default 0.95). Walking, dolly and drive-by shots
        # are exactly that, so the solve used to fail with 'No good initial image
        # pair found' even with hundreds of thousands of verified matches.
        fwd_motion = str(self.config.get("init_max_forward_motion", 1.0))
        init_trials = str(self.config.get("init_num_trials", 500))

        if solved_model() is None:
            self.progress_signal.emit(80, f"[{idx}/{total_videos}] [4/4] Sparse Reconstruction (Incremental Mapper)...")
            self.log_signal.emit(f"▶ [4/4] Reconstructing 3D camera track with BA Lens Distortion Refinement...", "#ffffff")
            mapper_cmd = [
                str(self.colmap_exe), "mapper",
                "--database_path", str(db_path),
                "--image_path", str(img_dir),
                "--output_path", str(sparse_dir),
                "--Mapper.init_min_tri_angle", str(self.config.get("tri_angle", 2.5)),
                "--Mapper.init_min_num_inliers", str(self.config.get("inliers", 40)),
                "--Mapper.abs_pose_min_num_inliers", str(max(15, self.config.get("inliers", 40) // 2)),
                "--Mapper.init_max_forward_motion", fwd_motion,
                "--Mapper.init_num_trials", init_trials,
                "--Mapper.ba_refine_focal_length", refine_flag,
                "--Mapper.ba_refine_extra_params", refine_flag,
                "--Mapper.ba_refine_principal_point", "0",
                "--Mapper.ba_use_gpu", "1" if (self.config.get("enable_caspar_ba", True) and self.config.get("use_gpu", True)) else "0",
                "--Mapper.num_threads", str(os.cpu_count() or 4)
            ]
            self._run_command(mapper_cmd, env, "COLMAP Mapper")
            if self.is_cancelled:
                self.video_status_signal.emit(video.name, "Cancelled")
                return False

        # Step 4.5: retry ladder when the first solve covers too little of the shot.
        #
        # Video is a trap for COLMAP's initialiser: the most matches are always
        # between adjacent frames, which have almost no parallax, so the first
        # pair it picks triangulates garbage and nothing else registers. That
        # shows up as a "successful" two-frame camera. Relaxing the angle (the
        # old retry) makes it worse. A matchmover's answer is the opposite:
        # force a wide-baseline initial pair. The fully relaxed pass is kept as
        # the last resort for genuinely low-parallax shots. Each attempt writes
        # its own folder and the one that registers the most frames wins.
        extracted = len([f for f in img_dir.iterdir() if f.is_file()]) if img_dir.is_dir() else 0
        min_coverage = float(self.config.get("min_coverage", 0.5))
        good_enough = 0.9    # stop retrying once this share of frames is solved

        def best_of(dirs):
            best, best_st = None, (0, 0)
            for d in dirs:
                if not d.is_dir():
                    continue
                m = find_best_model(d, log=lambda msg: self.log_signal.emit(msg, "#e0a000"))
                if m is None:
                    continue
                st = model_stats(m)
                if st > best_st:
                    best, best_st = m, st
            return best, best_st

        candidates = [sparse_dir]
        best_model, (registered, _pts) = best_of(candidates)
        base_inliers = int(self.config.get("inliers", 40))
        gpu_ba = "1" if (self.config.get("enable_caspar_ba", True) and self.config.get("use_gpu", True)) else "0"
        ladder = [
            ("wide-baseline", sparse_dir.parent / "sparse_wide", [
                "--Mapper.init_min_tri_angle", "8",
                "--Mapper.init_min_num_inliers", str(max(60, base_inliers)),
                "--Mapper.abs_pose_min_num_inliers", "15",
                "--Mapper.init_max_forward_motion", "1.0",
                "--Mapper.init_num_trials", "500",
            ]),
            ("relaxed", sparse_dir.parent / "sparse_relaxed", [
                "--Mapper.init_min_tri_angle", "0.5",
                "--Mapper.init_min_num_inliers", "15",
                "--Mapper.abs_pose_min_num_inliers", "10",
                "--Mapper.init_max_forward_motion", "1.0",
                "--Mapper.init_num_trials", "1000",
                "--Mapper.filter_min_tri_angle", "0.5",
            ]),
        ]
        for name, out_dir, params in ladder:
            if extracted and best_model is not None and registered >= good_enough * extracted:
                break
            self.log_signal.emit(
                f"↻ First solve registered {registered} of {extracted} frames - "
                f"retrying with a {name} initial pair...", "#e0a000")
            self.progress_signal.emit(78, f"[{idx}/{total_videos}] [4/4] Retry: {name} initial pair...")
            out_dir.mkdir(parents=True, exist_ok=True)
            retry_cmd = [
                str(self.colmap_exe), "mapper",
                "--database_path", str(db_path),
                "--image_path", str(img_dir),
                "--output_path", str(out_dir),
                "--Mapper.ba_refine_focal_length", refine_flag,
                "--Mapper.ba_refine_extra_params", refine_flag,
                "--Mapper.ba_refine_principal_point", "0",
                "--Mapper.ba_use_gpu", gpu_ba,
                "--Mapper.num_threads", str(os.cpu_count() or 4),
            ] + params
            self._run_command(retry_cmd, env, f"COLMAP Mapper ({name} retry)")
            if self.is_cancelled:
                self.video_status_signal.emit(video.name, "Cancelled")
                return False
            candidates.append(out_dir)
            new_best, (new_reg, _p) = best_of(candidates)
            if new_best is not None and new_reg > registered:
                self.log_signal.emit(
                    f"✔ {name} retry registered {new_reg} of {extracted} frames "
                    f"(was {registered}).", "#00ff88")
            best_model, registered = new_best, new_reg

        # Step 5: Convert Best Model to TXT & Multi-Format Exports
        if best_model is None:
            self.log_signal.emit(f"✖ Mapper completed but could not reconstruct camera poses.", "#ffaa00")
            self.video_status_signal.emit(video.name, "No Track Found ✖")
            return False

        # A camera with keys on only some frames is not a usable track: Nuke and
        # AE would interpolate across the gaps and the plate would swim. Say so
        # here, in frame counts an artist can act on, instead of reporting
        # "completed" for a two-image solve.
        if extracted:
            coverage = registered / float(extracted)
            if registered < 3 or coverage < min_coverage:
                self.log_signal.emit(
                    f"✖ COLMAP solved only {registered} of {extracted} frames "
                    f"({coverage:.0%}) even after retries. That is not a usable camera "
                    f"track, so nothing was exported. Typical causes: too little parallax "
                    f"(a static or nodal shot), motion blur, or a wrong lens model. Try a "
                    f"different preset, a smaller Frame Step, or mask moving actors.", "#ff4b4b")
                self.video_status_signal.emit(video.name, f"Solved {registered}/{extracted} ✖")
                return False
            if registered < extracted:
                self.log_signal.emit(
                    f"! COLMAP solved {registered} of {extracted} frames ({coverage:.0%}). "
                    f"The exported camera has keys only on solved frames; the "
                    f"{extracted - registered} missing frame(s) will be interpolated by "
                    f"your DCC. Check those frames before relying on the track.", "#e0a000")
            else:
                self.log_signal.emit(f"✔ All {extracted} frames registered.", "#00ff88")

        self.log_signal.emit(f"▶ Exporting best model to TXT format...", "#ffffff")
        conv_cmd = [
            str(self.colmap_exe), "model_converter",
            "--input_path", str(best_model),
            "--output_path", str(sparse_dir),
            "--output_type", "TXT"
        ]
        self._run_command(conv_cmd, env, "COLMAP Model Converter")

        # Step 5.1: Automatic 3D Environment Surface Mesh Generation (Delaunay / Advancing Front)
        if self.config.get("generate_mesh", True):
            mesh_out = track_dir / "environment_mesh.ply"
            self.log_signal.emit("▶ Reconstructing 3D environment collision mesh (Delaunay Mesher)...", "#ffffff")
            mesher_cmd = [
                str(self.colmap_exe), "delaunay_mesher",
                "--input_type", "sparse",
                "--input_path", str(best_model),
                "--output_path", str(mesh_out)
            ]
            success_mesh = self._run_command(mesher_cmd, env, "COLMAP 3D Delaunay Mesher")
            if success_mesh and mesh_out.exists():
                self.log_signal.emit("✔ Generated 3D Environment Mesh: environment_mesh.ply", "#00ff88")
            else:
                # poisson_mesher takes a PLY point cloud, not a sparse model folder;
                # the old fallback passed the folder and could never succeed.
                cloud_ply = track_dir / "sparse_points.ply"
                to_ply_cmd = [
                    str(self.colmap_exe), "model_converter",
                    "--input_path", str(best_model),
                    "--output_path", str(cloud_ply),
                    "--output_type", "PLY"
                ]
                if self._run_command(to_ply_cmd, env, "COLMAP Model To PLY") and cloud_ply.exists():
                    poisson_cmd = [
                        str(self.colmap_exe), "poisson_mesher",
                        "--input_path", str(cloud_ply),
                        "--output_path", str(mesh_out)
                    ]
                    self._run_command(poisson_cmd, env, "COLMAP Poisson Mesher")
                if mesh_out.exists():
                    self.log_signal.emit("✔ Generated 3D Environment Mesh: environment_mesh.ply", "#00ff88")
                else:
                    self.log_signal.emit(
                        "Notice: Could not build an environment mesh from this sparse solve "
                        "(too few 3D points). Camera track is unaffected.", "#e0a000")

        self.log_signal.emit(f"▶ Generating Blender 1-Click Script, USD (.usda), Point Cloud (.ply), Nuke (.chan & .nk), and Alembic (.abc)...", "#ffffff")
        exported = False
        try:
            from export_tools import export_all_formats
            b_path = self.config.get("blender_path")
            # Frame rate comes from the source clip; the exporters used to assume 30.
            if video.is_file():
                src_fps = probe_fps(video)
                fps_note = f"detected from {video.name}"
            else:
                # An image sequence folder carries no frame rate of its own; look for a
                # matching clip in 02 VIDEOS before falling back to a stated default.
                src_fps, fps_note = None, ""
                videos_dir = self.base_dir / "02 VIDEOS"
                for ext in (".mp4", ".mov", ".avi", ".mkv", ".m4v"):
                    cand = videos_dir / (base_name + ext)
                    if cand.exists():
                        src_fps = probe_fps(cand)
                        fps_note = f"detected from {cand.name}"
                        break
                if src_fps is None:
                    src_fps = 24.0
                    fps_note = "image sequence has no frame rate - assuming 24"
            eff_fps = src_fps / frame_step
            self.log_signal.emit(
                f"   Frame rate for exports: {eff_fps:.3f} fps ({fps_note}"
                + (f", step {frame_step}" if frame_step > 1 else "") + ")",
                "#a0a0b0")
            exp_res = export_all_formats(
                track_dir,
                blender_path=b_path,
                log_callback=lambda m, c: self.log_signal.emit(m, c),
                fps=eff_fps,
                start_frame=self.config.get("timeline_start", 1),
                colmap_exe=self.colmap_exe,
            )
            if exp_res.get("success"):
                exported = True
                self.log_signal.emit(f"   ✔ Generated 1-Click Blender Script: import_to_blender.py", "#00d2ff")
                self.log_signal.emit(f"   ✔ Generated Universal Scene Description: camera_track.usda", "#00d2ff")
                self.log_signal.emit(f"   ✔ Generated 3D Point Cloud: points3D.ply", "#00d2ff")
                self.log_signal.emit(f"   ✔ Generated Nuke Camera Track: camera_track.chan & camera_track_nuke.nk", "#00d2ff")
                if exp_res.get("alembic_path"):
                    self.log_signal.emit(f"   ✔ Generated Alembic (.abc) Camera & Point Cloud: camera_track.abc", "#00ff88")
                if exp_res.get("blend_path"):
                    self.log_signal.emit(f"   ✔ Generated Blender (.blend) Project: camera_track.blend", "#00ff88")
            else:
                self.log_signal.emit(f"✖ Export failed: {exp_res.get('error', 'unknown error')}", "#ff4b4b")

            # Sync to _latest - cleared first so nothing from an earlier run lingers (C13)
            try:
                latest_dir = shot_dir / "3D_CAMERA_TRACK" / "_latest"
                shutil.rmtree(latest_dir, ignore_errors=True)
                latest_dir.mkdir(parents=True, exist_ok=True)
                for f in track_dir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, latest_dir / f.name)
                if (track_dir / "sparse").exists():
                    shutil.copytree(track_dir / "sparse", latest_dir / "sparse", dirs_exist_ok=True)
            except Exception as sync_err:
                self.log_signal.emit(f"Notice: could not refresh _latest: {sync_err}", "#e0a000")
        except Exception:
            self.log_signal.emit("✖ Multi-format export raised:", "#ff4b4b")
            for line in traceback.format_exc().rstrip().splitlines():
                self.log_signal.emit(f"   {line}", "#ff7878")

        if not exported:
            self.log_signal.emit(f"✖ '{base_name}' solved but the exports failed. (Model in 3D_CAMERA_TRACK/{timestamp}/sparse)", "#ff4b4b")
            self.video_status_signal.emit(video.name, "Export Failed ✖")
            return False

        self.log_signal.emit(f"✔ Successfully tracked and exported '{base_name}'! (Results in 3D_CAMERA_TRACK/{timestamp}/)", "#00ff88")
        self.video_status_signal.emit(video.name, "Completed ✔")
        return True

    def _kill_process(self):
        """Stop the current child for good: terminate, wait, then kill (B7, B8)."""
        proc = self.process
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()
                    proc.wait(timeout=10)
        except Exception:
            pass
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            self.process = None

    def _run_command(self, cmd, env, step_name):
        if self.is_cancelled:
            return False
        try:
            # utf-8 with replacement: COLMAP prints file names and progress bars that
            # the console codepage cannot decode, and a UnicodeDecodeError here used
            # to leave COLMAP running on the GPU while the next step started (B8).
            self.process = popen_hidden(
                cmd,
                capture=True,
                env=env,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            for line in iter(self.process.stdout.readline, ''):
                if self.is_cancelled:
                    self._kill_process()
                    return False
                line_str = line.strip()
                if line_str:
                    if "Elapsed time:" in line_str or "Registering image" in line_str or "Triangulated" in line_str or "Features:" in line_str:
                        self.log_signal.emit(f"   {line_str}", "#a0d8ef")
                    elif "error" in line_str.lower() or "failed" in line_str.lower():
                        self.log_signal.emit(f"   {line_str}", "#ff7878")
                    else:
                        self.log_signal.emit(f"   {line_str}", "#708090")

            self.process.stdout.close()
            returncode = self.process.wait()
            self.process = None
            if self.is_cancelled:
                return False
            return returncode == 0
        except Exception as e:
            self.log_signal.emit(f"✖ Execution Exception in {step_name}: {str(e)}", "#ff4b4b")
            self._kill_process()
            return False

    def cancel(self):
        self.is_cancelled = True
        proc = self.process
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass


class CoTrackerWorker(QThread):
    log_signal = Signal(str, str)
    progress_signal = Signal(int, str)
    video_status_signal = Signal(str, str)
    finished_signal = Signal(bool, str)

    def __init__(self, video_paths, config):
        super().__init__()
        self.video_paths = video_paths
        self.config = config
        self.is_cancelled = False

    def run(self):
        total = len(self.video_paths)
        if total == 0:
            self.finished_signal.emit(False, "No videos selected for 2D tracking.")
            return

        try:
            import cotracker_2d
        except ImportError as e:
            self.log_signal.emit(f"✖ Error importing cotracker_2d engine: {e}", "#ff4b4b")
            self.finished_signal.emit(False, "CoTracker engine failed to import.")
            return

        for idx, video_path in enumerate(self.video_paths, start=1):
            if self.is_cancelled:
                break

            video = Path(video_path)
            self.log_signal.emit(f"\n==================================================", "#00d2ff")
            self.log_signal.emit(f"[{idx}/{total}] 2D Point Tracking: {video.name}", "#00d2ff")
            self.log_signal.emit(f"==================================================", "#00d2ff")
            self.video_status_signal.emit(video.name, "2D Tracking...")

            def _log(msg, color="#ffffff"):
                self.log_signal.emit(msg, color)

            def _prog(val, text):
                scaled_val = int(((idx - 1) * 100 + val) / total)
                self.progress_signal.emit(scaled_val, f"[{idx}/{total}] {text}")

            try:
                res = cotracker_2d.process_cotracker_2d(
                    video_path,
                    config=self.config,
                    progress_callback=_prog,
                    log_callback=_log,
                    cancel_check=lambda: self.is_cancelled,
                )
                if res.get("success"):
                    self.video_status_signal.emit(video.name, "Completed ✔")
                elif res.get("cancelled"):
                    self.video_status_signal.emit(video.name, "Cancelled")
                else:
                    self.video_status_signal.emit(video.name, "Failed ✖")
            except Exception as e:
                self.log_signal.emit(f"✖ Exception during 2D tracking: {str(e)}", "#ff4b4b")
                for line in traceback.format_exc().rstrip().splitlines():
                    self.log_signal.emit(f"   {line}", "#ff7878")
                self.video_status_signal.emit(video.name, "Error ✖")

        self.progress_signal.emit(100, "All 2D Tracking Jobs Completed")
        self.finished_signal.emit(True, "2D Tracking pipeline completed.")

    def cancel(self):
        self.is_cancelled = True


class FrameExtractorWorker(QThread):
    finished_signal = Signal(str, int)  # (shot_name, count)

    def __init__(self, video_path, output_dir, ffmpeg_exe):
        super().__init__()
        self.video_path = Path(video_path)
        self.output_dir = Path(output_dir)
        self.ffmpeg_exe = Path(ffmpeg_exe)
        self.is_cancelled = False
        self.process = None

    def cancel(self):
        """Stop the running ffmpeg; the GUI calls this before starting another extraction."""
        self.is_cancelled = True
        try:
            self.process.terminate()
        except Exception:
            pass

    def run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # The scrubber wants every frame (step 1). A folder already extracted from
        # this very clip at step 1 is reused; anything else is replaced and the
        # result stamped, so the 3D solve can tell what it is looking at (A14).
        existing = list_extracted_frames(self.output_dir)
        if self.video_path.is_dir() or (existing and images_stamp_matches(self.output_dir, self.video_path, 1)):
            # FFmpeg cannot read a folder; an image sequence is copied in by the 3D worker.
            self.finished_signal.emit(self.video_path.stem, len(existing))
            return
        clear_extracted_frames(self.output_dir)
        cmd = [
            str(self.ffmpeg_exe), "-y", "-loglevel", "error",
            "-i", str(self.video_path),
            "-q:v", "2",
            "-threads", "4",
            str(self.output_dir / "frame_%06d.jpg")
        ]
        try:
            # Explicit handles: a windowed build has none to inherit, and the
            # child silently fails to start without them.
            self.process = popen_hidden(cmd)
            self.process.wait()
            jpgs = list(self.output_dir.glob("*.jpg"))
            if self.is_cancelled:
                # a half-written folder must never be mistaken for a complete cache
                clear_extracted_frames(self.output_dir)
                self.finished_signal.emit(self.video_path.stem, 0)
                return
            if jpgs:
                write_images_stamp(self.output_dir, self.video_path, 1, len(jpgs))
            self.finished_signal.emit(self.video_path.stem, len(jpgs))
        except Exception:
            self.finished_signal.emit(self.video_path.stem, 0)
