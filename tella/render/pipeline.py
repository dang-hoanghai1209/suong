"""ffmpeg render pipeline — turn a fully-composed plan into a final MP4.

Pipeline (continuous-narration model, CEO 2026-06-29):
  1. Per scene: assemble VIDEO-ONLY background + title + caption → scene_NN.mp4
     (no audio map — keeps scene MP4s aligned strictly to visual timing)
  2. Concatenate all scene MP4s → silent_video.mp4
  3. Mix ``plan.narration_audio_filename`` onto the silent video → video.mp4

Why one audio track:
  Per-scene TTS files each carry ~0.3-0.6 s of leading + trailing silence
  baked in by the synth engine. Concatenating N of them stacks ~1 s of
  dead air on every scene boundary — fine for 8-scene videos, severe
  monotony-breakage on 30-scene ones. A single TTS call produces one
  utterance with natural inter-sentence breath pauses (much shorter and
  rhythmically correct).

Background handling per media_source:
  - ai_image / stock_photo : Ken Burns zoompan over the still image
  - stock_video            : scale + crop the clip to canvas, loop if shorter

Text overlays (all positioned inside the safe zone):
  - Title  : top, larger font, scene title in TARGET_LANG
  - Caption: bottom, smaller font, narration text wrapped to fit width

Concat: simple cut by default; themes that declare ``transition:
crossfade`` use ffmpeg ``xfade`` for soft scene boundaries.

Font: Windows-friendly default (Arial). Override via env
``TELLA_FONT_FILE`` if you have something prettier on PATH.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from tella.composer.compose import compose_timing
from tella.composer.safe_zone import render_dims_for, safe_zone_for
from tella.composer.text_wrap import chars_per_line, wrap
from tella.planner.models import TellaScenePlan
from tella.render.text_overlay import practical_step_badge_layout, render_overlay_png
from tella.subtitles import sanitize_highlight_words, subtitle_text_for_style
from tella.themes.loader import ImageGrade

logger = logging.getLogger("tella.render.pipeline")

OUTPUT_FPS = 30
TITLE_FONT_SIZE = 60
CAPTION_FONT_SIZE = 42
TITLE_MAX_LINES = 2
CAPTION_MAX_LINES = 4
TEXT_BOX_PADDING = 22
TEXT_BOX_OPACITY = 0.55

# Bottom band reserved for caption — top of caption sits this far above
# the safe-zone bottom edge so multi-line captions still fit.
CAPTION_BOTTOM_PADDING = 60
TITLE_TOP_PADDING = 50


def _resolve_font_file() -> Path:
    """Find a TTF/OTF font ffmpeg can use. Prefers env override; then
    common Windows/macOS/Linux system paths."""
    env = (os.environ.get("TELLA_FONT_FILE") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
        logger.warning("TELLA_FONT_FILE %s not found — falling back", env)

    candidates: list[str] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidates = [
            f"{windir}\\Fonts\\arial.ttf",
            f"{windir}\\Fonts\\segoeui.ttf",
            f"{windir}\\Fonts\\calibri.ttf",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    else:  # linux — cover Debian/Ubuntu + RHEL/Rocky/Fedora layouts
        candidates = [
            # Debian / Ubuntu layout
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            # RHEL / Rocky / Fedora layout (no truetype/ prefix)
            "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
            "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
            # Google Noto variable fonts (common on modern Rocky/RHEL)
            "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        ]
    for c in candidates:
        if Path(c).is_file():
            return Path(c)
    raise RuntimeError(
        "no usable TTF font found on this OS. Set TELLA_FONT_FILE in .env"
    )


def _ffmpeg_path_escape(p: Path) -> str:
    """Escape a path for use INSIDE an ffmpeg filter_complex string.

    ffmpeg filter syntax treats ``:`` `,`` `'`` `\\` as metacharacters.
    Forward slashes are always safe, even on Windows.
    """
    s = str(p).replace("\\", "/")
    # On Windows, drive letter like 'C:/...' needs the colon escaped
    # because drawtext options use ':' as separator.
    s = s.replace(":", "\\:")
    return s


def _is_video_asset(path: Path) -> bool:
    return path.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv")


def _env_float(name: str, default: float, low: float, high: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %.2f", name, raw, default)
        return default
    return max(low, min(high, value))


def _write_text_file(path: Path, text: str) -> Path:
    """Write text to a temp file ffmpeg's drawtext ``textfile=`` can read.

    drawtext on some ffmpeg builds chokes on BOM — we write plain UTF-8.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _build_bg_filter(
    *,
    is_video: bool,
    canvas_w: int,
    canvas_h: int,
    duration: float,
    ken_burns_max_scale: float,
    motion_profile: str = "",
    scene_index: int = 1,
) -> str:
    """Background-only filter chain (scale+crop+Ken Burns / fps).

    Text overlays are rendered via PNG composite (Pillow + ffmpeg
    ``overlay`` filter) because ffmpeg-static on prod VPS lacks the
    ``drawtext`` filter despite ``--enable-libfreetype`` in the configure
    string. See :mod:`tella.render.text_overlay`.
    """
    chains: list[str] = []
    chains.append(
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
        f"crop={canvas_w}:{canvas_h}"
    )
    if not is_video:
        total_frames = max(2, int(duration * OUTPUT_FPS))
        zoom_step = (ken_burns_max_scale - 1.0) / max(1, total_frames - 1)
        if motion_profile in {
            "controlled_slow_pan",
            "practical_pan_left_to_right",
            "practical_pan_right_to_left",
        }:
            last_frame = max(1, total_frames - 1)
            move_left_to_right = (
                motion_profile == "practical_pan_left_to_right"
                or motion_profile == "controlled_slow_pan" and scene_index % 2
            )
            if move_left_to_right:
                x_expr = f"(iw-iw/zoom)*(0.35+0.30*on/{last_frame})"
            else:
                x_expr = f"(iw-iw/zoom)*(0.65-0.30*on/{last_frame})"
            y_expr = "ih/2-(ih/zoom/2)"
            zoom_expr = f"min(zoom+{zoom_step:.6f},{ken_burns_max_scale})"
        elif motion_profile == "practical_pull_back":
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
            zoom_expr = f"max({ken_burns_max_scale}-{zoom_step:.6f}*on,1.0)"
        elif motion_profile == "practical_stable_hold":
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
            zoom_expr = "1.005"
        else:
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
            zoom_expr = f"min(zoom+{zoom_step:.6f},{ken_burns_max_scale})"
        chains.append(
            f"zoompan=z='{zoom_expr}':"
            f"x='{x_expr}':y='{y_expr}':"
            f"d={total_frames}:s={canvas_w}x{canvas_h}:fps={OUTPUT_FPS}"
        )
    else:
        chains.append(f"fps={OUTPUT_FPS}")
    return ",".join(chains)


def _apply_image_grade(
    source_path: Path,
    out_path: Path,
    *,
    canvas_w: int,
    canvas_h: int,
    grade: ImageGrade,
) -> str:
    """Grade a fitted working copy and return the original asset SHA-256."""
    source_bytes = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    with Image.open(source_path) as source:
        image = ImageOps.fit(
            source.convert("RGB"),
            (canvas_w, canvas_h),
            method=Image.Resampling.LANCZOS,
        )
    image = ImageEnhance.Brightness(image).enhance(grade.brightness)
    image = ImageEnhance.Contrast(image).enhance(grade.contrast)
    image = ImageEnhance.Color(image).enhance(grade.saturation)
    if grade.overlay_opacity > 0:
        overlay = Image.new("RGB", image.size, grade.overlay_color)
        image = Image.blend(
            image,
            overlay,
            max(0.0, min(1.0, grade.overlay_opacity)),
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG", compress_level=6)
    return source_hash


def _prepare_image_asset_for_render(
    source_path: Path,
    out_path: Path,
    *,
    canvas_w: int,
    canvas_h: int,
    grade: ImageGrade,
) -> tuple[Path, str, bool]:
    if not grade.enabled or _is_video_asset(source_path):
        return source_path, "", False
    source_hash = _apply_image_grade(
        source_path,
        out_path,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        grade=grade,
    )
    return out_path, source_hash, True


def _practical_motion_profile(scene) -> str:
    if scene.scene_role == "hook":
        return "practical_zoom_in"
    if scene.scene_role in {"context", "context_part_one", "context_part_two"}:
        return "practical_pan_left_to_right"
    if scene.scene_role == "practical_step":
        return {
            1: "practical_pan_left_to_right",
            2: "practical_pan_right_to_left",
            3: "practical_zoom_in",
        }.get(scene.step_number, "practical_zoom_in")
    if scene.scene_role == "common_mistake":
        return "practical_pull_back"
    if scene.scene_role in {"today_action", "closing"}:
        return "practical_stable_hold"
    return "practical_zoom_in"


def _render_progress_message(
    original_scene_index: int,
    execution_order: int,
    current_total: int,
    duration: float,
) -> str:
    return (
        f"rendered scene original_scene={original_scene_index:02d} "
        f"execution={execution_order}/{current_total} "
        f"({duration:.2f}s, video-only)"
    )


async def _render_scene(
    *,
    asset_path: Path,
    out_path: Path,
    duration: float,
    canvas_w: int,
    canvas_h: int,
    safe_top: int,
    safe_bottom: int,
    safe_left: int,
    safe_right: int,
    title_text: str | None,
    caption_text: str | None,
    work_dir: Path,
    font_file: Path,
    scene_index: int,
    ken_burns_max_scale: float,
    motion_profile: str = "",
    subtitle_style: str = "",
    highlight_words: list[str] | None = None,
    channel_name: str | None = None,
    channel_avatar: str | None = None,
    step_number: int = 0,
) -> Path:
    """Render one VIDEO-ONLY scene MP4. Audio is mixed in once at final-mux."""
    is_video = _is_video_asset(asset_path)

    # Pre-render the text overlay as a transparent PNG (Pillow). This
    # sidesteps ffmpeg's drawtext filter which isn't compiled into
    # ffmpeg-static on production VPS.
    overlay_png: Path | None = None
    if title_text or caption_text or channel_name or step_number:
        overlay_png = render_overlay_png(
            title=title_text,
            caption=caption_text,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            safe_top=safe_top,
            safe_bottom=safe_bottom,
            safe_left=safe_left,
            safe_right=safe_right,
            font_file=font_file,
            out_path=work_dir / f"scene_{scene_index:02d}_overlay.png",
            subtitle_style=subtitle_style,
            highlight_words=highlight_words or [],
            channel_name=channel_name,
            channel_avatar=channel_avatar,
            step_number=step_number,
        )

    bg_filter = _build_bg_filter(
        is_video=is_video,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        duration=duration,
        ken_burns_max_scale=ken_burns_max_scale,
        motion_profile=motion_profile,
        scene_index=scene_index,
    )

    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    if is_video:
        cmd += ["-stream_loop", "-1", "-i", str(asset_path)]
    else:
        cmd += ["-loop", "1", "-i", str(asset_path)]

    if overlay_png is not None:
        # 2nd input: the transparent PNG. ffmpeg overlay composites it
        # on top of the bg chain.
        cmd += ["-i", str(overlay_png)]
        filter_complex = f"[0:v]{bg_filter}[bg];[bg][1:v]overlay=0:0[v]"
    else:
        filter_complex = f"[0:v]{bg_filter}[v]"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-t", f"{duration:.3f}",
        "-r", str(OUTPUT_FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        # CRF 26 is well within the "visually transparent on mobile" band
        # (22-28). The H.264 stream stays ~40% smaller than CRF 22 with
        # no perceptible quality loss on a phone screen — TikTok/Reels
        # re-compress anyway.
        "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-an",                       # video-only scene
        "-movflags", "+faststart",
        str(out_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"ffmpeg render scene {scene_index} failed:\n{msg[-1500:]}"
        )
    return out_path


async def _concat_scenes(
    scene_mp4s: list[Path], out_path: Path, work_dir: Path,
) -> Path:
    """Concatenate VIDEO-ONLY scene MP4s via the concat demuxer (codec copy).

    The concat demuxer resolves each ``file`` line RELATIVE TO THE LIST
    FILE itself — not the cwd. Since our list lives next to the scene
    MP4s, use bare filenames instead of paths.
    """
    list_file = work_dir / "concat.txt"
    list_file.write_text(
        "\n".join(
            f"file '{p.name}'"
            for p in scene_mp4s
        ) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-an",                       # explicit video-only output
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg concat failed:\n{msg[-1500:]}")
    return out_path


async def _concat_scenes_xfade(
    scene_mp4s: list[Path],
    scene_durations: list[float],
    out_path: Path,
    *,
    transition_duration: float = 0.4,
) -> Path:
    """Concatenate VIDEO-ONLY scene MP4s with soft xfade transitions."""
    if len(scene_mp4s) == 1:
        shutil.copyfile(scene_mp4s[0], out_path)
        return out_path

    min_scene_duration = (
        min(scene_durations) if scene_durations else transition_duration
    )
    xfade_duration = min(transition_duration, max(0.1, min_scene_duration / 3.0))

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for p in scene_mp4s:
        cmd += ["-i", str(p)]

    padded_durations = list(scene_durations)
    for i in range(len(padded_durations) - 1):
        padded_durations[i] += xfade_duration

    filters: list[str] = []
    for i in range(len(scene_mp4s)):
        chain = f"[{i}:v]setpts=PTS-STARTPTS"
        if i < len(scene_mp4s) - 1:
            chain += f",tpad=stop_mode=clone:stop_duration={xfade_duration:.3f}"
        filters.append(f"{chain}[v{i}]")

    cumulative_duration = padded_durations[0]
    previous_label = "v0"
    for i in range(1, len(scene_mp4s)):
        offset = max(0.0, cumulative_duration - xfade_duration)
        out_label = f"xf{i}"
        filters.append(
            f"[{previous_label}][v{i}]"
            f"xfade=transition=fade:duration={xfade_duration:.3f}:offset={offset:.3f}"
            f"[{out_label}]"
        )
        cumulative_duration = cumulative_duration + padded_durations[i] - xfade_duration
        previous_label = out_label

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{previous_label}]",
        "-r", str(OUTPUT_FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg xfade concat failed:\n{msg[-1500:]}")
    return out_path


async def _mux_audio(
    silent_video: Path, audio: Path, out_path: Path, *, copy_audio: bool = False,
) -> Path:
    """Mix the continuous narration onto the concatenated silent video.

    Uses ``-shortest`` so any tiny mismatch (round-off between the audio's
    real duration and the sum of scene visual durations) ends cleanly
    without a trailing silent frame. Audio is encoded to AAC 96k mono —
    Edge/Google TTS output is mono speech.
    """
    audio_codec_args = ["-c:a", "copy"] if copy_audio else [
        "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "1",
    ]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(silent_video),
        "-i", str(audio),
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        *audio_codec_args,
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg final mux failed:\n{msg[-1500:]}")
    return out_path


async def render(
    plan: TellaScenePlan,
    job_dir: Path,
    *,
    preserve_timing: bool = False,
    existing_mixed_audio: Path | None = None,
) -> Path:
    """Render the final video MP4. Returns the path to ``<job_dir>/video.mp4``.

    Pre-requirements (in order):
      1. :func:`tella.media.fetch.fetch_assets` populated
         ``scene.image_filenames``.
      2. :func:`tella.tts.synth_all.synthesize_all` populated
         ``scene.audio_filename`` + ``audio_duration``.

    This function calls :func:`tella.composer.compose.compose_timing`
    internally to set ``start`` / ``duration`` / ``total_duration``.
    """
    job_dir = Path(job_dir)
    work_dir = job_dir / "_render"
    work_dir.mkdir(parents=True, exist_ok=True)

    canvas_w, canvas_h = render_dims_for(plan.aspect_ratio)
    sz = safe_zone_for(plan.aspect_ratio)
    font_file = _resolve_font_file()

    if not preserve_timing:
        compose_timing(plan)

    # Pre-compute the caption/title char budget once.
    title_cpl = chars_per_line(sz.width, TITLE_FONT_SIZE)
    caption_cpl = chars_per_line(sz.width, CAPTION_FONT_SIZE)

    # Pull the theme's Ken Burns end scale for image-mode scenes.
    from tella.themes.loader import load_theme

    theme_spec = load_theme(plan.theme)
    plan.image_grade_enabled = theme_spec.image_grade.enabled
    plan.image_grade_brightness = theme_spec.image_grade.brightness
    plan.image_grade_contrast = theme_spec.image_grade.contrast
    plan.image_grade_saturation = theme_spec.image_grade.saturation
    plan.image_grade_overlay_color = theme_spec.image_grade.overlay_color
    plan.image_grade_overlay_opacity = theme_spec.image_grade.overlay_opacity
    ken_burns_max_scale = max(1.01, float(theme_spec.ken_burns.end_scale))
    xfade_duration = 0.4
    if plan.theme == "minimalist_emotional":
        ken_burns_max_scale = _env_float(
            "TELLA_MINIMALIST_ZOOM_MAX",
            min(1.03, ken_burns_max_scale),
            1.01,
            1.08,
        )
        xfade_duration = _env_float(
            "TELLA_MINIMALIST_XFADE_DURATION",
            0.8,
            0.1,
            1.5,
        )
    elif plan.theme == "minimalist_symbolic_reel":
        ken_burns_max_scale = _env_float(
            "TELLA_SYMBOLIC_ZOOM_MAX",
            min(1.04, max(1.02, ken_burns_max_scale)),
            1.02,
            1.05,
        )
        xfade_duration = _env_float(
            "TELLA_SYMBOLIC_XFADE_DURATION",
            0.2,
            0.15,
            0.25,
        )
    use_crossfade = theme_spec.transition.strip().lower() == "crossfade"
    transition_profile = plan.transition_profile_id or (
        "subtle_crossfade" if use_crossfade else "cut"
    )
    motion_profile = plan.motion_profile_id or "slow_ken_burns"
    logger.info(
        "render routing theme=%s subtitle=%s transition=%s motion=%s",
        plan.theme,
        plan.subtitle_style,
        transition_profile,
        motion_profile,
    )

    # Channel brand row — shown on every scene unless demo mode / blank.
    # Name only (no handle/slug) plus an optional circular avatar.
    brand_name = (plan.channel_name or "").strip() if not plan.demo_mode else ""
    brand_avatar = (plan.channel_avatar or "").strip() if not plan.demo_mode else ""
    if brand_name:
        logger.info("brand row: %r avatar=%s", brand_name, bool(brand_avatar))

    body_scenes = [s for s in plan.scenes if s.kind == "scene"]
    scene_mp4s: list[Path] = []

    logger.info(
        "render: %d scenes, canvas=%dx%d, total=%.2fs",
        len(body_scenes), canvas_w, canvas_h, plan.total_duration,
    )

    if not plan.narration_audio_filename:
        raise RuntimeError(
            "plan.narration_audio_filename empty — did synthesize_all run?"
        )

    for execution_order, scene in enumerate(body_scenes, start=1):
        if not scene.image_filenames:
            raise RuntimeError(
                f"scene {scene.scene_index}: no image_filenames "
                "(did fetch_assets run?)"
            )

        source_asset_path = job_dir / scene.image_filenames[0]
        asset_path, source_hash, grade_applied = _prepare_image_asset_for_render(
            source_asset_path,
            work_dir / f"scene_{scene.scene_index:02d}_graded.png",
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            grade=theme_spec.image_grade,
        )
        scene.image_grade_applied = grade_applied
        scene.image_grade_source_asset_hash = source_hash
        if grade_applied:
            if plan.theme == "practical_life_steps":
                logger.info(
                    "practical image grade scene=%02d brightness=%.2f contrast=%.2f "
                    "saturation=%.2f overlay=%s opacity=%.2f",
                    scene.scene_index,
                    theme_spec.image_grade.brightness,
                    theme_spec.image_grade.contrast,
                    theme_spec.image_grade.saturation,
                    theme_spec.image_grade.overlay_color,
                    theme_spec.image_grade.overlay_opacity,
                )
            else:
                logger.info(
                    "symbolic image grade scene=%02d brightness=%.2f contrast=%.2f "
                    "saturation=%.2f overlay=%s opacity=%.2f",
                    scene.scene_index,
                    theme_spec.image_grade.brightness,
                    theme_spec.image_grade.contrast,
                    theme_spec.image_grade.saturation,
                    theme_spec.image_grade.overlay_color,
                    theme_spec.image_grade.overlay_opacity,
                )
        out_mp4 = work_dir / f"scene_{scene.scene_index:02d}.mp4"

        title_lines = [] if plan.theme in {
            "minimalist_emotional",
            "minimalist_symbolic_reel",
            "life_insight_symbolic",
            "practical_life_steps",
        } else wrap(
            scene.title, title_cpl, max_lines=TITLE_MAX_LINES
        )
        caption_result = subtitle_text_for_style(
            scene.voice_script,
            plan.subtitle_style,
        )
        if caption_result.removed_codepoints:
            logger.info(
                "subtitle text sanitized scene=%02d removed_codepoints=%s",
                scene.scene_index,
                json.dumps(list(caption_result.removed_codepoints)),
            )
        caption_lines = wrap(
            caption_result.text,
            caption_cpl,
            max_lines=(
                2
                if plan.subtitle_style in {"insight_reel", "practical_steps_reel"}
                else CAPTION_MAX_LINES
            ),
        )
        title_text = "\n".join(title_lines) if title_lines else None
        caption_text = "\n".join(caption_lines) if caption_lines else None

        scene_motion_profile = motion_profile
        scene_zoom_scale = ken_burns_max_scale
        if (
            plan.theme == "life_insight_symbolic"
            and scene.scene_role == "conclusion"
        ):
            scene_motion_profile = "controlled_slow_hold"
            scene_zoom_scale = min(1.012, ken_burns_max_scale)
        elif plan.theme == "practical_life_steps":
            scene_motion_profile = _practical_motion_profile(scene)
            scene_zoom_scale = (
                min(1.012, ken_burns_max_scale)
                if scene_motion_profile == "practical_stable_hold"
                else ken_burns_max_scale
            )
        scene.render_motion_profile = scene_motion_profile
        badge_layout = practical_step_badge_layout(
            subtitle_style=plan.subtitle_style,
            step_number=(
                scene.step_number if scene.scene_role == "practical_step" else 0
            ),
            canvas_w=canvas_w,
            safe_top=sz.top,
            safe_left=sz.left,
            font_file=font_file,
        )
        if badge_layout is not None:
            scene.step_badge_rendered = True
            scene.step_badge_text = str(badge_layout["text"])
            scene.step_badge_x = int(badge_layout["x"])
            scene.step_badge_y = int(badge_layout["y"])
            scene.step_badge_width = int(badge_layout["width"])
            scene.step_badge_height = int(badge_layout["height"])
            logger.info(
                "practical step badge scene=%02d text=%s x=%d y=%d width=%d height=%d",
                scene.scene_index,
                scene.step_badge_text,
                scene.step_badge_x,
                scene.step_badge_y,
                scene.step_badge_width,
                scene.step_badge_height,
            )
        else:
            scene.step_badge_rendered = False
            scene.step_badge_text = ""
        logger.info(
            "scene motion scene=%02d role=%s profile=%s",
            scene.scene_index,
            scene.scene_role,
            scene_motion_profile,
        )
        await _render_scene(
            asset_path=asset_path,
            out_path=out_mp4,
            duration=scene.duration,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            safe_top=sz.top,
            safe_bottom=sz.bottom,
            safe_left=sz.left,
            safe_right=sz.right,
            title_text=title_text,
            caption_text=caption_text,
            work_dir=work_dir,
            font_file=font_file,
            scene_index=scene.scene_index,
            ken_burns_max_scale=scene_zoom_scale,
            motion_profile=scene_motion_profile,
            subtitle_style=plan.subtitle_style,
            highlight_words=sanitize_highlight_words(
                scene.subtitle_highlight_words,
                plan.subtitle_style,
            ),
            channel_name=brand_name or None,
            channel_avatar=brand_avatar or None,
            step_number=(
                scene.step_number if scene.scene_role == "practical_step" else 0
            ),
        )
        scene_mp4s.append(out_mp4)
        logger.info(
            _render_progress_message(
                scene.scene_index,
                execution_order,
                len(body_scenes),
                scene.duration,
            )
        )

    # Stage 2: concat the video-only scenes → silent_video.mp4
    silent_video = work_dir / "silent_video.mp4"
    if use_crossfade and len(scene_mp4s) > 1:
        await _concat_scenes_xfade(
            scene_mp4s,
            [max(0.1, s.duration) for s in body_scenes],
            silent_video,
            transition_duration=xfade_duration,
        )
        logger.info("xfade concat done → %s (silent)", silent_video.name)
    else:
        await _concat_scenes(scene_mp4s, silent_video, work_dir)
        logger.info("concat done → %s (silent)", silent_video.name)

    # Stage 3: mux the single continuous narration onto the silent video.
    final_path = job_dir / "video.mp4"
    narration_path = job_dir / plan.narration_audio_filename
    if not narration_path.is_file():
        raise RuntimeError(f"missing narration audio: {narration_path}")
    from tella.music.audio import (
        mix_music_and_narration,
        prepare_music,
        run_audio_qc,
    )
    from tella.music.service import record_music_usage, write_music_metadata

    prepared_music = None
    loop_status = "not_applicable"
    if existing_mixed_audio is not None:
        accepted_audio = Path(existing_mixed_audio)
        if not accepted_audio.is_file():
            raise RuntimeError(f"missing accepted mixed audio: {accepted_audio}")
        await _mux_audio(silent_video, accepted_audio, final_path, copy_audio=True)
    elif plan.music_enabled:
        prepared_music, processing = await prepare_music(
            plan,
            job_dir,
            duration=plan.total_duration,
        )
        plan.music_metadata = {
            **(plan.music_metadata or {}),
            "processing": processing,
        }
        write_music_metadata(plan, job_dir)
        loop_status = str(processing["loop_discontinuity_status"])
        await mix_music_and_narration(
            plan,
            silent_video,
            narration_path,
            prepared_music,
            final_path,
        )
    else:
        await _mux_audio(silent_video, narration_path, final_path)
        plan.music_metadata = {
            **(plan.music_metadata or {}),
            "status": (plan.music_metadata or {}).get("status", "disabled"),
            "selected_track": "",
            "output_duration": plan.total_duration,
        }
        write_music_metadata(plan, job_dir)

    await run_audio_qc(
        plan,
        job_dir,
        narration=narration_path,
        prepared_music=prepared_music,
        final_video=final_path,
        expected_duration=plan.total_duration,
        loop_discontinuity_status=loop_status,
    )
    if plan.music_enabled and existing_mixed_audio is None:
        plan.music_metadata = {
            **plan.music_metadata,
            "output_duration": plan.audio_qc.get("output_duration"),
            "loudness_statistics": {
                "music_loudness_lufs": plan.audio_qc.get("music_loudness_lufs"),
                "final_integrated_loudness_lufs": plan.audio_qc.get(
                    "final_integrated_loudness_lufs"
                ),
                "true_peak_dbtp": plan.audio_qc.get("true_peak_dbtp"),
            },
            "qc_result": plan.audio_qc.get("status"),
        }
        write_music_metadata(plan, job_dir)
        record_music_usage(plan, job_dir)
    logger.info(
        "render done: %s (%.2fs, narration=%s music=%s audio_qc=%s)",
        final_path,
        plan.total_duration,
        plan.tts_provider or "continuous",
        plan.selected_music_track_id or "none",
        plan.audio_qc.get("status", "unknown"),
    )
    return final_path


__all__ = [
    "OUTPUT_FPS",
    "render",
]
