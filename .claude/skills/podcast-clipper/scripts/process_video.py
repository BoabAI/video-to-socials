#!/usr/bin/env python3
"""
Step 3: Process video clips — slice, render multi-format, generate ASS
subtitles, burn them in, overlay logo, extract thumbnails.

Formats:
  - horizontal (16:9) 1920x1080
  - square (1:1) 1080x1080
  - vertical (9:16) 1080x1920 with blurred background
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("process_video")

FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"

# ASS subtitle style templates per format
ASS_HEADER_TEMPLATE = """[Script Info]
Title: Clip Subtitles
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,FreeSans Bold,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

FORMAT_CONFIGS = {
    "horizontal": {
        "width": 1920,
        "height": 1080,
        "play_res_x": 1920,
        "play_res_y": 1080,
        "font_size": 58,
        "outline": 3,
        "shadow": 1,
        "margin_l": 120,
        "margin_r": 120,
        "margin_v": 60,
        "max_chars_per_line": 40,
    },
    "square": {
        "width": 1080,
        "height": 1080,
        "play_res_x": 1080,
        "play_res_y": 1080,
        "font_size": 52,
        "outline": 3,
        "shadow": 1,
        "margin_l": 80,
        "margin_r": 80,
        "margin_v": 60,
        "max_chars_per_line": 30,
    },
    "vertical": {
        "width": 1080,
        "height": 1920,
        "play_res_x": 1080,
        "play_res_y": 1920,
        "font_size": 56,
        "outline": 3,
        "shadow": 1,
        "margin_l": 60,
        "margin_r": 60,
        "margin_v": 280,  # High margin to avoid platform UI overlaps
        "max_chars_per_line": 25,
    },
}


def get_video_info(video_path: str) -> dict:
    """Get video metadata via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def slice_clip(video_path: str, start: float, end: float, output_path: str) -> None:
    """Extract a clip from the source video with re-encoding for precision."""
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def render_horizontal(input_path: str, output_path: str) -> None:
    """Render 16:9 (1920x1080) — scale to fit, pad if needed."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", (
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def render_square(input_path: str, output_path: str) -> None:
    """Render 1:1 (1080x1080) — center crop."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", (
            "scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1080:1080"
        ),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def render_vertical(input_path: str, output_path: str) -> None:
    """Render 9:16 (1080x1920) — blurred background + centered foreground."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex", (
            # Background: scale to fill 1080x1920, blur heavily
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=20:5[bg];"
            # Foreground: scale to fit width, maintain aspect ratio
            "[0:v]scale=1080:-2[fg];"
            # Overlay foreground centered on blurred background
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[out]"
        ),
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def chunk_words(words: list[dict], max_chars: int) -> list[dict]:
    """Group words into subtitle chunks of appropriate length."""
    chunks = []
    current_words = []
    current_len = 0

    for word in words:
        word_text = word["word"]
        new_len = current_len + len(word_text) + (1 if current_words else 0)

        # Start new chunk if too long or we've hit 6 words
        if current_words and (new_len > max_chars or len(current_words) >= 6):
            chunks.append({
                "text": " ".join(w["word"] for w in current_words),
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
            })
            current_words = []
            current_len = 0

        current_words.append(word)
        current_len = sum(len(w["word"]) for w in current_words) + len(current_words) - 1

    # Flush remaining words
    if current_words:
        chunks.append({
            "text": " ".join(w["word"] for w in current_words),
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"],
        })

    return chunks


def format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp format H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def wrap_text_for_ass(text: str, max_chars: int) -> str:
    """Smart word-wrap for ASS subtitles using \\N line breaks."""
    if len(text) <= max_chars:
        return text

    words = text.split()
    lines = []
    current_line = []
    current_len = 0

    for word in words:
        new_len = current_len + len(word) + (1 if current_line else 0)
        if current_line and new_len > max_chars:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_len = len(word)
        else:
            current_line.append(word)
            current_len = new_len

    if current_line:
        lines.append(" ".join(current_line))

    return "\\N".join(lines)


def generate_ass_subtitles(
    words: list[dict],
    clip_start: float,
    fmt_name: str,
) -> str:
    """Generate ASS subtitle content for a clip in a specific format."""
    config = FORMAT_CONFIGS[fmt_name]

    header = ASS_HEADER_TEMPLATE.format(**config)

    # Chunk words into subtitle groups
    chunks = chunk_words(words, config["max_chars_per_line"])

    events = []
    for chunk in chunks:
        # Times relative to clip start
        start = chunk["start"] - clip_start
        end = chunk["end"] - clip_start

        # Ensure minimum display time of 0.5s
        if end - start < 0.5:
            end = start + 0.5

        start_ts = format_ass_time(max(0, start))
        end_ts = format_ass_time(end)

        # Wrap text for format
        text = wrap_text_for_ass(chunk["text"], config["max_chars_per_line"])

        events.append(
            f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}"
        )

    return header + "\n".join(events) + "\n"


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    """Burn ASS subtitles into video using ffmpeg ass filter."""
    # Escape special characters in path for ffmpeg filter
    escaped_ass = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"ass={escaped_ass}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def overlay_logo(
    video_path: str,
    logo_path: str,
    output_path: str,
    fmt_name: str,
) -> None:
    """Overlay logo on video — bottom-right, ~8% width, semi-transparent."""
    config = FORMAT_CONFIGS[fmt_name]
    logo_width = int(config["width"] * 0.08)

    if fmt_name == "vertical":
        # Position higher to avoid TikTok/Reels controls
        y_pos = f"H-h-{config['margin_v'] + 40}"
    else:
        y_pos = "H-h-20"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", logo_path,
        "-filter_complex", (
            f"[1:v]scale={logo_width}:-1,format=rgba,"
            f"colorchannelmixer=aa=0.7[logo];"
            f"[0:v][logo]overlay=W-w-20:{y_pos}[out]"
        ),
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def extract_thumbnail(video_path: str, output_path: str) -> None:
    """Extract a frame at the midpoint for thumbnail."""
    # Get duration
    probe_cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    duration = float(result.stdout.strip())
    midpoint = duration / 2

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(midpoint),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def slugify(text: str) -> str:
    """Convert text to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:40].rstrip("-")


def retranscribe_clip_segment(
    audio_path: str,
    start: float,
    end: float,
) -> list[dict]:
    """Re-transcribe a clip segment with the 'small' model for accurate subtitles."""
    import whisper
    import numpy as np

    log.info("    Re-transcribing %.1f-%.1f with 'small' model...", start, end)

    model = whisper.load_model("small")
    audio = whisper.load_audio(audio_path)

    sr = 16000
    start_sample = int(start * sr)
    end_sample = int(end * sr)
    segment_audio = audio[start_sample:end_sample]

    result = model.transcribe(
        segment_audio,
        word_timestamps=True,
        condition_on_previous_text=False,
        verbose=False,
    )

    # Offset timestamps back to original timeline
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": round(w["start"] + start, 3),
                "end": round(w["end"] + start, 3),
            })
    return words


def process_single_clip(
    clip: dict,
    video_path: str,
    output_dir: str,
    logo_path: str | None,
    audio_path: str | None = None,
) -> str:
    """Process a single clip through the full pipeline. Returns clip directory."""
    clip_num = clip["clip_number"]
    slug = clip.get("slug", slugify(clip["title"]))
    clip_dir = os.path.join(output_dir, f"clip_{clip_num:02d}_{slug}")
    os.makedirs(clip_dir, exist_ok=True)

    words = clip.get("words", [])
    clip_start = clip["start_time"]

    # Pass 2: Re-transcribe with accurate model if audio available
    if audio_path and os.path.isfile(audio_path):
        words = retranscribe_clip_segment(
            audio_path, clip["start_time"], clip["end_time"]
        )
        clip["words"] = words

    log.info("  Processing clip %d: %s", clip_num, clip["title"])

    # Step A: Slice raw segment
    raw_slice = os.path.join(clip_dir, "_raw_slice.mp4")
    log.info("    Slicing %.1f-%.1f...", clip["start_time"], clip["end_time"])
    slice_clip(video_path, clip["start_time"], clip["end_time"], raw_slice)

    formats = {
        "horizontal": render_horizontal,
        "square": render_square,
        "vertical": render_vertical,
    }

    for fmt_name, render_fn in formats.items():
        log.info("    Rendering %s...", fmt_name)

        # Step B: Render format
        fmt_video = os.path.join(clip_dir, f"_{fmt_name}_nosub.mp4")
        render_fn(raw_slice, fmt_video)

        # Step C: Generate ASS subtitles
        if words:
            ass_content = generate_ass_subtitles(words, clip_start, fmt_name)
            ass_path = os.path.join(clip_dir, f"_{fmt_name}.ass")
            with open(ass_path, "w") as f:
                f.write(ass_content)

            # Step D: Burn subtitles
            sub_video = os.path.join(clip_dir, f"_{fmt_name}_sub.mp4")
            burn_subtitles(fmt_video, ass_path, sub_video)
        else:
            sub_video = fmt_video

        # Step E: Overlay logo (if provided)
        final_output = os.path.join(clip_dir, f"{fmt_name}.mp4")
        if logo_path and os.path.isfile(logo_path):
            overlay_logo(sub_video, logo_path, final_output, fmt_name)
        else:
            os.rename(sub_video, final_output)

        # Clean up intermediate files
        for tmp_file in [fmt_video, sub_video]:
            if os.path.exists(tmp_file) and tmp_file != final_output:
                os.unlink(tmp_file)

    # Extract thumbnail from horizontal version
    log.info("    Extracting thumbnail...")
    thumbnail_path = os.path.join(clip_dir, "thumbnail.jpg")
    extract_thumbnail(
        os.path.join(clip_dir, "horizontal.mp4"),
        thumbnail_path,
    )

    # Clean up raw slice and ASS files
    for f in os.listdir(clip_dir):
        if f.startswith("_"):
            os.unlink(os.path.join(clip_dir, f))

    log.info("    Clip %d complete: %s", clip_num, clip_dir)
    return clip_dir


def main() -> None:
    if len(sys.argv) < 3:
        log.error(
            "Usage: process_video.py <input_video> <output_dir> [--logo <logo.png>]"
        )
        sys.exit(1)

    video_path = sys.argv[1]
    output_dir = sys.argv[2]

    logo_path = None
    if "--logo" in sys.argv:
        logo_idx = sys.argv.index("--logo")
        if logo_idx + 1 < len(sys.argv):
            logo_path = sys.argv[logo_idx + 1]

    if not os.path.isfile(video_path):
        log.error("Error: Video file not found: %s", video_path)
        sys.exit(1)

    # Load clips.json
    clips_path = os.path.join(output_dir, "clips.json")
    if not os.path.isfile(clips_path):
        log.error("Error: clips.json not found in %s", output_dir)
        sys.exit(1)

    with open(clips_path) as f:
        clips_data = json.load(f)

    clips = clips_data["clips"]
    log.info("Processing %d clips from %s", len(clips), video_path)

    if logo_path and os.path.isfile(logo_path):
        log.info("Using logo: %s", logo_path)
    else:
        log.info("No logo file provided — clips will be rendered without logo overlay")

    # Check for preserved audio from transcription step (for pass-2 re-transcription)
    audio_path = None
    audio_ref = os.path.join(output_dir, "_audio_path.txt")
    if os.path.isfile(audio_ref):
        with open(audio_ref) as f:
            audio_path = f.read().strip()
        if os.path.isfile(audio_path):
            log.info("Pass-2 re-transcription enabled (small model for subtitles)")
        else:
            audio_path = None

    for clip in clips:
        process_single_clip(clip, video_path, output_dir, logo_path, audio_path)

    log.info("All %d clips processed successfully!", len(clips))


if __name__ == "__main__":
    main()
