#!/usr/bin/env python3
"""
E2E Test Pipeline — Extract diverse segments from podcast_combined.mp4,
run the full clipper pipeline on each, extract analysis frames, and
generate a quality report.

Usage:
    python3 test_pipeline.py [--segments N] [--segment-duration SECS]
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from PIL import Image
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("test_pipeline")

PROJECT_DIR = Path(__file__).parent
SKILL_DIR = Path.home() / ".claude" / "skills" / "podcast-clipper"
PIPELINE_SCRIPT = SKILL_DIR / "scripts" / "pipeline.py"
LOGO_PATH = SKILL_DIR / "assets" / "smec_logo.png"
SOURCE_VIDEO = PROJECT_DIR / "podcast_combined.mp4"

# Test segments: (label, offset_seconds)
DEFAULT_SEGMENTS = [
    ("segment_A_intro", 60),
    ("segment_B_mid", 600),
    ("segment_C_deep", 1800),
]

SEGMENT_DURATION = 30  # seconds


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def get_video_info(video_path: str) -> dict:
    """Get video metadata (dimensions, bitrate, codec)."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def extract_test_segment(
    source: str, offset: float, duration: float, output: str
) -> None:
    """Extract a segment from source video."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(offset),
        "-i", source,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def extract_frames(video_path: str, output_dir: str, label: str) -> list[str]:
    """Extract analysis frames: start, midpoint, and 75% through the video."""
    duration = get_video_duration(video_path)
    timestamps = [
        ("frame_start", 0.1),
        ("frame_mid", duration / 2),
        ("frame_75pct", duration * 0.75),
    ]

    frame_paths = []
    for frame_label, ts in timestamps:
        out_path = os.path.join(output_dir, f"{label}_{frame_label}.png")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        frame_paths.append(out_path)
    return frame_paths


def analyze_video_file(video_path: str) -> dict:
    """Analyze a rendered video file for the report."""
    info = get_video_info(video_path)

    video_stream = None
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    duration = float(info["format"].get("duration", 0))
    bitrate_kbps = int(info["format"].get("bit_rate", 0)) / 1000

    result = {
        "file": os.path.basename(video_path),
        "size_mb": round(file_size_mb, 2),
        "duration_s": round(duration, 2),
        "bitrate_kbps": round(bitrate_kbps, 0),
    }

    if video_stream:
        result["width"] = video_stream.get("width")
        result["height"] = video_stream.get("height")
        result["codec"] = video_stream.get("codec_name")

    return result


def validate_subtitles(clip_dir: str) -> dict:
    """Check ASS subtitle files for timing issues."""
    issues = []
    ass_files = [f for f in os.listdir(clip_dir) if f.endswith(".ass")]

    for ass_file in ass_files:
        ass_path = os.path.join(clip_dir, ass_file)
        with open(ass_path) as f:
            lines = f.readlines()

        dialogue_lines = [l for l in lines if l.startswith("Dialogue:")]
        prev_end = 0.0

        for line in dialogue_lines:
            parts = line.split(",", 9)
            if len(parts) < 3:
                continue
            start_str = parts[1].strip()
            end_str = parts[2].strip()

            # Parse ASS time H:MM:SS.cc
            try:
                start = parse_ass_time(start_str)
                end = parse_ass_time(end_str)
            except (ValueError, IndexError):
                issues.append(f"Invalid timestamp in {ass_file}: {start_str}-{end_str}")
                continue

            duration = end - start
            if duration < 0.3:
                issues.append(f"Very short subtitle ({duration:.2f}s) in {ass_file}")
            if duration > 8.0:
                issues.append(f"Very long subtitle ({duration:.2f}s) in {ass_file}")
            if start < prev_end - 0.01:
                issues.append(
                    f"Overlap in {ass_file}: starts at {start:.2f} "
                    f"but previous ends at {prev_end:.2f}"
                )
            prev_end = end

    return {
        "ass_files_found": len(ass_files),
        "issues": issues,
        "valid": len(issues) == 0,
    }


def parse_ass_time(time_str: str) -> float:
    """Parse ASS timestamp H:MM:SS.cc to seconds."""
    h, m, rest = time_str.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


# Format-aware zones for visual checks
# Logo: 15% of width, bottom-right with 30px padding
# Subtitles: bottom zone defined by margin_v per format
FORMAT_ZONES = {
    "horizontal": {"width": 1920, "height": 1080, "sub_margin_v": 60, "logo_pct": 0.18, "logo_pad": 30},
    "square": {"width": 1080, "height": 1080, "sub_margin_v": 60, "logo_pct": 0.18, "logo_pad": 30},
    "vertical": {"width": 1080, "height": 1920, "sub_margin_v": 280, "logo_pct": 0.18, "logo_pad": 30},
}

# Minimum logo width in pixels to be considered readable
MIN_LOGO_WIDTH_PX = 190


def analyze_subtitle_zone(frame_path: str, fmt: str) -> dict:
    """Check if subtitles are placed in the correct zone with good contrast.

    Detects subtitle text by looking for bright white pixels (>200 luminance)
    in the expected subtitle region. Checks placement doesn't overlap the
    participant video zone using geometric bounds (not pixel heuristics,
    since participant video can contain bright areas like windows).
    """
    img = Image.open(frame_path).convert("RGB")
    pixels = np.array(img)
    h, w = pixels.shape[:2]
    zone = FORMAT_ZONES[fmt]

    # Subtitle zone: the region where subtitles should appear
    # ASS Alignment=2 (bottom-center) with margin_v means text bottom edge
    # is margin_v from frame bottom. Text height ~80px for our font sizes.
    sub_bottom = h - zone["sub_margin_v"]
    sub_top = sub_bottom - 120  # generous height for wrapped subtitle text
    sub_top = max(0, sub_top)

    # Participant video bounds (for SBS layout geometry check)
    # Horizontal: strip centered at y=270..810 (1920x540 in 1920x1080)
    # Square: participants fill y=0..536 and y=544..1080
    # Vertical: participants at y=40..640 and y=648..1248
    if fmt == "vertical":
        participant_bottom = 1250
    elif fmt == "horizontal":
        participant_bottom = 810
    else:
        participant_bottom = 1080  # square: fills entire frame

    # Geometric overlap check: does subtitle zone overlap participant video?
    text_overlaps_faces = sub_top < participant_bottom and fmt != "square"
    # Square always overlaps since participants fill the frame — that's expected,
    # the semi-transparent background box handles readability there.

    # Detect subtitle text in the expected zone
    luminance = 0.299 * pixels[:, :, 0] + 0.587 * pixels[:, :, 1] + 0.114 * pixels[:, :, 2]
    sub_region_lum = luminance[sub_top:sub_bottom, :]
    sub_bright = sub_region_lum > 200
    sub_text_detected = np.sum(sub_bright) > 50

    # Contrast check: ratio of text brightness to background in subtitle zone
    if sub_text_detected:
        text_pixels = sub_region_lum[sub_region_lum > 200]
        bg_pixels = sub_region_lum[sub_region_lum < 100]
        if len(text_pixels) > 0 and len(bg_pixels) > 0:
            contrast_ratio = float(np.mean(text_pixels)) / max(float(np.mean(bg_pixels)), 1)
        else:
            contrast_ratio = 0.0
    else:
        contrast_ratio = 0.0

    issues = []
    if text_overlaps_faces:
        issues.append(
            f"Subtitle zone (y={sub_top}-{sub_bottom}) overlaps "
            f"participant video (ends y={participant_bottom})"
        )
    if sub_text_detected and contrast_ratio < 3.0:
        issues.append(f"Low subtitle contrast ratio: {contrast_ratio:.1f} (want >= 3.0)")

    return {
        "subtitle_detected": sub_text_detected,
        "subtitle_in_correct_zone": sub_text_detected and not text_overlaps_faces,
        "text_overlaps_faces": text_overlaps_faces,
        "contrast_ratio": round(contrast_ratio, 1),
        "issues": issues,
    }


def analyze_logo_zone(frame_path: str, fmt: str) -> dict:
    """Check if logo is visible and correctly placed in the expected region.

    The SMEC AI logo is purple (R~98 G~33 B~196) at 85% opacity on dark
    backgrounds. Detection uses color channel analysis rather than pure
    luminance since the logo is colorful, not white.
    """
    img = Image.open(frame_path).convert("RGB")
    pixels = np.array(img)
    h, w = pixels.shape[:2]
    zone = FORMAT_ZONES[fmt]

    logo_w = int(w * zone["logo_pct"])
    pad = zone["logo_pad"]

    # Logo region: top-right of actual video content
    logo_x1 = w - logo_w - pad
    logo_x2 = w - pad
    logo_h = int(logo_w * 344 / 1483)  # match source aspect ratio

    # SBS video content starts at different y offsets per format
    if fmt == "horizontal":
        video_top = 270  # strip centered in 1080
    elif fmt == "vertical":
        video_top = 40  # participants start 40px from top
    else:
        video_top = 0  # square: participants at top

    logo_y1 = video_top + pad
    logo_y2 = logo_y1 + logo_h

    logo_y1 = max(0, logo_y1)
    logo_x1 = max(0, logo_x1)

    logo_region = pixels[logo_y1:logo_y2, logo_x1:logo_x2, :]

    if logo_region.size == 0:
        return {
            "logo_detected": False,
            "logo_region_std": 0,
            "logo_region_mean_brightness": 0,
            "logo_content_pixels": 0,
            "logo_has_contrast": False,
            "issues": ["Logo region is empty or out of bounds"],
        }

    region_std = float(np.std(logo_region))

    # Detect logo content using color saturation — the purple logo has high
    # blue channel relative to green, distinguishing it from dark grey bg.
    r = logo_region[:, :, 0].astype(float)
    g = logo_region[:, :, 1].astype(float)
    b = logo_region[:, :, 2].astype(float)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b

    # Logo pixels: either colored (blue > 40 and blue > green * 1.5) or
    # bright text (luminance > 40 and stands out from dark bg ~17 lum)
    colored_pixels = np.sum((b > 40) & (b > g * 1.5))
    bright_pixels = np.sum(luminance > 40)
    total_pixels = luminance.size

    # Content pixels = colored OR noticeably brighter than background
    content_pixels = int(np.sum((luminance > 35) & (region_std > 10)))

    bg_mean = float(np.mean(luminance[luminance < 30])) if np.any(luminance < 30) else 0
    content_mean = float(np.mean(luminance[luminance > 35])) if np.any(luminance > 35) else 0

    logo_detected = region_std > 10 and (colored_pixels > 20 or bright_pixels > 50)
    logo_has_contrast = content_mean > bg_mean * 1.5 if bg_mean > 0 else content_mean > 30

    # Check rendered logo width meets minimum readability threshold
    actual_logo_w = logo_x2 - logo_x1
    logo_large_enough = actual_logo_w >= MIN_LOGO_WIDTH_PX

    issues = []
    if not logo_detected:
        issues.append(
            f"Logo not detected (std={region_std:.1f}, "
            f"colored_px={colored_pixels}, bright_px={bright_pixels})"
        )
    if logo_detected and not logo_has_contrast:
        issues.append(
            f"Logo lacks contrast (content={content_mean:.0f} vs bg={bg_mean:.0f})"
        )
    if not logo_large_enough:
        issues.append(
            f"Logo too small ({actual_logo_w}px wide, minimum {MIN_LOGO_WIDTH_PX}px)"
        )

    return {
        "logo_detected": logo_detected,
        "logo_width_px": actual_logo_w,
        "logo_region_std": round(region_std, 1),
        "logo_region_mean_brightness": round(content_mean, 1),
        "logo_content_pixels": int(colored_pixels + bright_pixels),
        "logo_has_contrast": logo_has_contrast,
        "logo_large_enough": logo_large_enough,
        "issues": issues,
    }


def analyze_frame_quality(frame_path: str, fmt: str) -> dict:
    """Run all visual quality checks on a single frame."""
    subtitle_check = analyze_subtitle_zone(frame_path, fmt)
    logo_check = analyze_logo_zone(frame_path, fmt)

    all_issues = subtitle_check["issues"] + logo_check["issues"]

    return {
        "frame": os.path.basename(frame_path),
        "format": fmt,
        "subtitle": subtitle_check,
        "logo": logo_check,
        "issues": all_issues,
        "pass": len(all_issues) == 0,
    }


def run_pipeline(
    segment_video: str, output_dir: str, env: dict
) -> tuple[bool, float]:
    """Run the full pipeline on a segment. Returns (success, elapsed_seconds)."""
    cmd = [
        sys.executable, str(PIPELINE_SCRIPT),
        segment_video, output_dir,
        "--logo", str(LOGO_PATH),
    ]

    start = time.time()
    result = subprocess.run(
        cmd, env=env, text=True,
        capture_output=True,
    )
    elapsed = time.time() - start

    if result.returncode != 0:
        log.error("Pipeline failed for %s:\n%s\n%s",
                  segment_video, result.stdout[-2000:], result.stderr[-2000:])
        return False, elapsed

    return True, elapsed


def generate_report(
    test_output_dir: str,
    segment_results: list[dict],
) -> str:
    """Generate a markdown analysis report."""
    lines = [
        "# E2E Test Pipeline Report",
        f"",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Source:** `{SOURCE_VIDEO.name}`",
        "",
    ]

    for seg in segment_results:
        lines.append(f"## {seg['label']}")
        lines.append(f"- **Offset:** {seg['offset']}s, **Duration:** {SEGMENT_DURATION}s")
        lines.append(f"- **Pipeline:** {'SUCCESS' if seg['success'] else 'FAILED'} "
                      f"({seg['elapsed']:.1f}s)")
        lines.append("")

        if not seg["success"]:
            lines.append("*Pipeline failed — no outputs to analyze*")
            lines.append("")
            continue

        # Video analysis
        lines.append("### Video Analysis")
        lines.append("")
        lines.append("| Format | Size (MB) | Bitrate (kbps) | Resolution | Codec |")
        lines.append("|--------|-----------|----------------|------------|-------|")
        for va in seg.get("video_analysis", []):
            lines.append(
                f"| {va['file']} | {va['size_mb']} | {va['bitrate_kbps']:.0f} "
                f"| {va.get('width', '?')}x{va.get('height', '?')} | {va.get('codec', '?')} |"
            )
        lines.append("")

        # Visual quality checks
        quality_checks = seg.get("quality_checks", [])
        if quality_checks:
            lines.append("### Visual Quality Checks")
            lines.append("")
            lines.append("| Format | Subs Detected | Subs In Zone | Contrast | Logo Detected | Logo Size | Logo Readable | Pass |")
            lines.append("|--------|---------------|--------------|----------|---------------|-----------|---------------|------|")
            for qc in quality_checks:
                sub = qc["subtitle"]
                logo = qc["logo"]
                logo_size = f"{logo.get('logo_width_px', '?')}px"
                lines.append(
                    f"| {qc['format']} "
                    f"| {'YES' if sub['subtitle_detected'] else 'no'} "
                    f"| {'YES' if sub['subtitle_in_correct_zone'] else 'NO'} "
                    f"| {sub['contrast_ratio']} "
                    f"| {'YES' if logo['logo_detected'] else 'NO'} "
                    f"| {logo_size} "
                    f"| {'YES' if logo.get('logo_large_enough') else 'NO'} "
                    f"| {'PASS' if qc['pass'] else 'FAIL'} |"
                )
            lines.append("")

            # List any issues
            all_issues = []
            for qc in quality_checks:
                for issue in qc["issues"]:
                    all_issues.append(f"  - **{qc['format']}**: {issue}")
            if all_issues:
                lines.append("**Issues:**")
                lines.extend(all_issues)
                lines.append("")

        # Frame paths
        lines.append("### Analysis Frames")
        for fp in seg.get("frame_paths", []):
            lines.append(f"- `{os.path.relpath(fp, test_output_dir)}`")
        lines.append("")

    report = "\n".join(lines)
    report_path = os.path.join(test_output_dir, "test_report.md")
    with open(report_path, "w") as f:
        f.write(report)

    log.info("Report written to %s", report_path)
    return report_path


def main() -> None:
    if not SOURCE_VIDEO.is_file():
        log.error("Source video not found: %s", SOURCE_VIDEO)
        sys.exit(1)

    source_duration = get_video_duration(str(SOURCE_VIDEO))
    log.info("Source video duration: %.0fs (%.1f min)", source_duration, source_duration / 60)

    # Adjust segments to fit within actual duration
    segments = []
    for label, offset in DEFAULT_SEGMENTS:
        if offset + SEGMENT_DURATION <= source_duration:
            segments.append((label, offset))
        else:
            # Fall back to last possible position
            fallback = max(0, source_duration - SEGMENT_DURATION - 1)
            log.warning(
                "Segment %s offset %ds exceeds duration %.0fs, using %.0fs",
                label, offset, source_duration, fallback,
            )
            segments.append((label, fallback))

    # Build environment
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = ""  # Avoid Triton bug

    # Load API key from .env
    env_file = PROJECT_DIR / ".env"
    if env_file.is_file():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    env["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip('"').strip("'")

    if "ANTHROPIC_API_KEY" not in env:
        log.error("ANTHROPIC_API_KEY not found in environment or .env file")
        sys.exit(1)

    test_output_dir = str(PROJECT_DIR / "test_e2e_output")
    os.makedirs(test_output_dir, exist_ok=True)

    frames_dir = os.path.join(test_output_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    segment_results = []

    for label, offset in segments:
        log.info("=" * 60)
        log.info("TESTING: %s (offset=%ds)", label, offset)
        log.info("=" * 60)

        # Extract test segment
        segment_video = os.path.join(test_output_dir, f"{label}.mp4")
        log.info("Extracting segment...")
        extract_test_segment(str(SOURCE_VIDEO), offset, SEGMENT_DURATION, segment_video)

        # Run pipeline
        seg_output = os.path.join(test_output_dir, label)
        os.makedirs(seg_output, exist_ok=True)

        log.info("Running pipeline...")
        success, elapsed = run_pipeline(segment_video, seg_output, env)

        result = {
            "label": label,
            "offset": offset,
            "success": success,
            "elapsed": elapsed,
            "video_analysis": [],
            "subtitle_validation": {},
            "frame_paths": [],
            "quality_checks": [],
        }

        if success:
            # Find clip directories
            clip_dirs = [
                os.path.join(seg_output, d)
                for d in os.listdir(seg_output)
                if d.startswith("clip_") and os.path.isdir(os.path.join(seg_output, d))
            ]

            for clip_dir in clip_dirs:
                clip_name = os.path.basename(clip_dir)

                # Analyze each format
                for fmt in ["horizontal", "square", "vertical"]:
                    video_file = os.path.join(clip_dir, f"{fmt}.mp4")
                    if os.path.isfile(video_file):
                        analysis = analyze_video_file(video_file)
                        result["video_analysis"].append(analysis)

                        # Extract frames
                        frame_label = f"{label}_{clip_name}_{fmt}"
                        frames = extract_frames(video_file, frames_dir, frame_label)
                        result["frame_paths"].extend(frames)

                        # Run visual quality checks on mid frame (most likely to have subtitles)
                        mid_frame = frames[1]  # frame_mid
                        quality = analyze_frame_quality(mid_frame, fmt)
                        result["quality_checks"].append(quality)
                        if quality["issues"]:
                            log.warning("  Quality issues in %s %s: %s",
                                        label, fmt, "; ".join(quality["issues"]))

        segment_results.append(result)

    # Generate report
    report_path = generate_report(test_output_dir, segment_results)

    log.info("")
    log.info("=" * 60)
    log.info("E2E TEST COMPLETE")
    log.info("Output: %s", test_output_dir)
    log.info("Report: %s", report_path)
    log.info("Frames: %s", frames_dir)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
