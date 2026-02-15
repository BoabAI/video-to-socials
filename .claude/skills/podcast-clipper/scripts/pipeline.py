#!/usr/bin/env python3
"""
Podcast Clipper Pipeline — Main Orchestrator

Runs the full Raw-to-Ready pipeline:
  1. Transcribe (Whisper)
  2. Select clips (Claude)
  3. Process video (ffmpeg multi-format + subtitles + logo)
  4. Generate captions (Claude)
  5. Generate summary

Usage:
  python3 pipeline.py <input_video> <output_dir> [--logo <logo.png>]
"""

import json
import logging
import os
import subprocess
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("pipeline")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")


def run_step(step_name: str, cmd: list[str]) -> None:
    """Run a pipeline step, logging progress and timing."""
    log.info("=" * 60)
    log.info("STEP: %s", step_name)
    log.info("=" * 60)

    start = time.time()
    result = subprocess.run(cmd, text=True)

    elapsed = time.time() - start
    if result.returncode != 0:
        log.error("FAILED: %s (exit code %d, %.1fs)", step_name, result.returncode, elapsed)
        sys.exit(result.returncode)

    log.info("DONE: %s (%.1fs)", step_name, elapsed)


def validate_input(video_path: str) -> None:
    """Validate input video exists and is a valid media file."""
    if not os.path.isfile(video_path):
        log.error("Error: Video file not found: %s", video_path)
        sys.exit(1)

    # Validate with ffprobe
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Error: Invalid video file: %s", video_path)
        sys.exit(1)

    info = json.loads(result.stdout)
    duration = float(info["format"].get("duration", 0))
    log.info("Input: %s (%.0f minutes)", os.path.basename(video_path), duration / 60)


def generate_summary(output_dir: str) -> None:
    """Generate summary.md with results overview."""
    clips_path = os.path.join(output_dir, "clips.json")
    with open(clips_path) as f:
        clips_data = json.load(f)

    clips = clips_data["clips"]

    lines = [
        "# Podcast Clipper — Output Summary",
        "",
        f"**Clips generated:** {len(clips)}",
        "",
    ]

    for clip in clips:
        clip_num = clip["clip_number"]
        slug = clip.get("slug", "untitled")
        duration = clip["end_time"] - clip["start_time"]

        # Find clip directory
        clip_dir = None
        for entry in os.listdir(output_dir):
            if entry.startswith(f"clip_{clip_num:02d}_"):
                clip_dir = os.path.join(output_dir, entry)
                break

        lines.append(f"## Clip {clip_num}: {clip['title']}")
        lines.append("")
        lines.append(f"- **Duration:** {duration:.1f}s")
        lines.append(f"- **Time range:** {clip['start_time']:.1f}s — {clip['end_time']:.1f}s")
        lines.append(f"- **Rationale:** {clip.get('rationale', 'N/A')}")

        if clip_dir:
            lines.append(f"- **Directory:** `{clip_dir}`")

            # List files
            files = sorted(os.listdir(clip_dir))
            for f in files:
                fpath = os.path.join(clip_dir, f)
                size_mb = os.path.getsize(fpath) / (1024 * 1024)
                lines.append(f"  - `{f}` ({size_mb:.1f} MB)")

            # Show LinkedIn caption preview if available
            captions_path = os.path.join(clip_dir, "captions.json")
            if os.path.isfile(captions_path):
                with open(captions_path) as f:
                    captions = json.load(f)
                linkedin = captions.get("linkedin", {})
                if linkedin.get("headline"):
                    lines.append("")
                    lines.append(f"**LinkedIn Preview:**")
                    lines.append(f"> **{linkedin['headline']}**")
                    body_preview = linkedin.get("body", "")[:200]
                    if body_preview:
                        lines.append(f"> {body_preview}...")

        lines.append("")

    summary_path = os.path.join(output_dir, "summary.md")
    with open(summary_path, "w") as f:
        f.write("\n".join(lines))

    log.info("Summary written to %s", summary_path)


def main() -> None:
    if len(sys.argv) < 3:
        log.error("Usage: pipeline.py <input_video> <output_dir> [--logo <logo.png>]")
        sys.exit(1)

    video_path = os.path.abspath(sys.argv[1])
    output_dir = os.path.abspath(sys.argv[2])

    logo_path = None
    if "--logo" in sys.argv:
        logo_idx = sys.argv.index("--logo")
        if logo_idx + 1 < len(sys.argv):
            logo_path = os.path.abspath(sys.argv[logo_idx + 1])

    # Default logo location
    if not logo_path:
        default_logo = os.path.join(SKILL_DIR, "assets", "smec_logo.png")
        if os.path.isfile(default_logo):
            logo_path = default_logo

    host_side = "left"
    if "--host-side" in sys.argv:
        hs_idx = sys.argv.index("--host-side")
        if hs_idx + 1 < len(sys.argv):
            host_side = sys.argv[hs_idx + 1]
            if host_side not in ("left", "right"):
                log.error("--host-side must be 'left' or 'right', got '%s'", host_side)
                sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    log.info("=" * 60)
    log.info("PODCAST CLIPPER PIPELINE")
    log.info("=" * 60)

    validate_input(video_path)
    if logo_path:
        log.info("Logo: %s", logo_path)
    else:
        log.info("Logo: none (clips will be rendered without logo)")

    pipeline_start = time.time()

    # Step 1: Transcribe
    run_step("Transcribe", [
        sys.executable,
        os.path.join(SCRIPTS_DIR, "transcribe.py"),
        video_path, output_dir,
    ])

    # Step 2: Select clips
    run_step("Select Clips", [
        sys.executable,
        os.path.join(SCRIPTS_DIR, "select_clips.py"),
        os.path.join(output_dir, "transcript.json"),
        output_dir,
    ])

    # Step 3: Process video
    process_cmd = [
        sys.executable,
        os.path.join(SCRIPTS_DIR, "process_video.py"),
        video_path, output_dir,
    ]
    if logo_path:
        process_cmd.extend(["--logo", logo_path])
    process_cmd.extend(["--host-side", host_side])
    run_step("Process Video", process_cmd)

    # Step 4: Generate captions
    run_step("Generate Captions", [
        sys.executable,
        os.path.join(SCRIPTS_DIR, "generate_copy.py"),
        output_dir,
    ])

    # Step 5: Summary
    log.info("=" * 60)
    log.info("STEP: Generate Summary")
    log.info("=" * 60)
    generate_summary(output_dir)

    total_time = time.time() - pipeline_start
    log.info("")
    log.info("=" * 60)
    log.info("PIPELINE COMPLETE (%.1f minutes)", total_time / 60)
    log.info("Output: %s", output_dir)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
