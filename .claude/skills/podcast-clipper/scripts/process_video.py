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
Style: Default,FreeSans Bold,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,{outline},{shadow},2,{margin_l},{margin_r},{margin_v},1

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


def is_side_by_side(video_path: str) -> bool:
    """Detect if video is side-by-side layout (width:height ratio > 3:1)."""
    info = get_video_info(video_path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            w = int(stream["width"])
            h = int(stream["height"])
            ratio = w / h
            if ratio > 3.0:
                log.info("  Detected side-by-side layout: %dx%d (ratio %.2f:1)", w, h, ratio)
                return True
            return False
    return False


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Get video width and height."""
    info = get_video_info(video_path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    return 0, 0


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


def render_horizontal(input_path: str, output_path: str, sbs: bool = False) -> None:
    """Render 16:9 (1920x1080).

    For side-by-side source: scale the strip to fill 1920px wide, then
    vertically center on a dark background. Each participant gets ~960x540
    which is much larger than the generic letterbox approach.
    """
    if sbs:
        # Side-by-side: scale to full width, center vertically with dark bg
        vf = (
            "scale=1920:-2,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x111111"
        )
    else:
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
        )

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def render_square(input_path: str, output_path: str, sbs: bool = False) -> None:
    """Render 1:1 (1080x1080).

    For side-by-side source: split at midpoint, stack participants vertically.
    Each participant gets ~540x540 with a small divider between them.
    """
    if sbs:
        src_w, src_h = get_video_dimensions(input_path)
        half_w = src_w // 2
        # Split left/right participants, scale to 1080 wide (preserving 16:9),
        # crop to 536px tall, pad first with 8px divider, vstack (536+8+536=1080)
        filter_complex = (
            f"[0:v]crop={half_w}:{src_h}:0:0,scale=1080:-2,crop=1080:536,"
            f"pad=1080:544:0:0:color=0x2a2a2a[left];"
            f"[0:v]crop={half_w}:{src_h}:{half_w}:0,scale=1080:-2,crop=1080:536[right];"
            "[left][right]vstack=inputs=2[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
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


def render_vertical(input_path: str, output_path: str, sbs: bool = False) -> None:
    """Render 9:16 (1080x1920).

    For side-by-side source: split participants and stack them in the upper
    portion of the frame. Each participant fills ~1080x607 (16:9 per person),
    stacked in the upper 2/3. Lower third is a dark area for subtitles.
    """
    if sbs:
        src_w, src_h = get_video_dimensions(input_path)
        half_w = src_w // 2
        # Each participant: crop half, scale to 1080 wide (preserving 16:9),
        # crop to 600px tall. Pad first with 8px divider (600+8+600=1208).
        # Then pad to 1080x1920 with dark bg, offset 40px from top.
        filter_complex = (
            f"[0:v]crop={half_w}:{src_h}:0:0,scale=1080:-2,crop=1080:600,"
            f"pad=1080:608:0:0:color=0x2a2a2a[left];"
            f"[0:v]crop={half_w}:{src_h}:{half_w}:0,scale=1080:-2,crop=1080:600[right];"
            "[left][right]vstack=inputs=2,"
            "pad=1080:1920:0:40:color=0x111111[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=20:5[bg];"
                "[0:v]scale=1080:-2[fg];"
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


def _sample_bg_brightness(video_path: str, x1: int, y1: int, x2: int, y2: int) -> float:
    """Sample the average brightness of a region in the first frame.

    Returns mean luminance (0-255).
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vframes", "1", "-q:v", "2", tmp_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)

        from PIL import Image
        import numpy as np
        img = Image.open(tmp_path).convert("RGB")
        pixels = np.array(img)
        region = pixels[max(0, y1):y2, max(0, x1):x2, :]
        luminance = 0.299 * region[:, :, 0] + 0.587 * region[:, :, 1] + 0.114 * region[:, :, 2]
        return float(luminance.mean())
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _get_logo_position(fmt_name: str, sbs: bool = False) -> tuple[int, int, int, int]:
    """Get logo position (x, y, width, logo_height_approx) for a format.

    Logo is placed top-right of the actual video content area.
    """
    config = FORMAT_CONFIGS[fmt_name]
    frame_w = config["width"]
    logo_width = int(frame_w * 0.18)
    logo_height = int(logo_width * 344 / 1483)  # match source aspect ratio
    padding = 30

    if sbs:
        # SBS layouts have specific video content regions
        if fmt_name == "horizontal":
            # Video strip is centered: top of strip = (1080 - 540) / 2 = 270
            video_top = 270
        elif fmt_name == "square":
            # Participants start at top
            video_top = 0
        else:
            # Vertical: participants start 40px from top
            video_top = 40
    else:
        video_top = 0

    x = frame_w - logo_width - padding
    y = video_top + padding

    return x, y, logo_width, logo_height


def overlay_logo(
    video_path: str,
    logo_path: str,
    output_path: str,
    fmt_name: str,
    sbs: bool = False,
) -> None:
    """Overlay logo top-right of video content, ~18% width, semi-transparent.

    Automatically detects if the logo background is dark and applies a
    white recolor filter so the logo remains visible on any background.
    """
    x, y, logo_width, logo_height = _get_logo_position(fmt_name, sbs)
    padding = 30

    # Sample background brightness at logo position to decide coloring
    bg_brightness = _sample_bg_brightness(video_path, x, y, x + logo_width, y + logo_height)
    log.info("    Logo bg brightness for %s: %.0f (at %d,%d)", fmt_name, bg_brightness, x, y)

    if bg_brightness < 80:
        # Dark background: make logo white
        logo_filter = (
            f"[1:v]scale={logo_width}:-1,format=rgba,"
            f"colorlevels=rimin=0:rimax=0.4:gimin=0:gimax=0.4:bimin=0:bimax=0.4,"
            f"colorchannelmixer=aa=0.9[logo]"
        )
    else:
        # Bright background: use original dark logo
        logo_filter = (
            f"[1:v]scale={logo_width}:-1,format=rgba,"
            f"colorchannelmixer=aa=0.85[logo]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", logo_path,
        "-filter_complex", (
            f"{logo_filter};"
            f"[0:v][logo]overlay={x}:{y}[out]"
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


def _generate_topic_illustration(clip_title: str, width: int, height: int) -> "Image.Image | None":
    """Generate an AI illustration for the clip topic via Gemini.

    Returns a PIL Image on success, None on failure. The illustration uses
    a dark navy/blue palette to blend with the thumbnail background.
    """
    try:
        import base64
        import io

        from dotenv import load_dotenv
        from PIL import Image

        load_dotenv()
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            log.warning("    GOOGLE_API_KEY not set, skipping topic illustration")
            return None

        from google import genai

        client = genai.Client(api_key=api_key)

        prompt = (
            "Generate an abstract decorative background illustration loosely "
            f"inspired by the theme: \"{clip_title}\"\n\n"
            "STYLE (mandatory):\n"
            "- Dark navy/deep blue background (#0a0e27 to #1a1e3a palette)\n"
            "- Abstract glowing geometric shapes: flowing lines, nodes, "
            "circuits, waves, or particle networks\n"
            "- Use glowing blue (#4a5aff), purple (#7b2fff), and cyan accents\n"
            "- Ethereal, atmospheric, and decorative — NOT a literal depiction\n"
            "- Think: abstract wallpaper art, not infographic\n\n"
            "ABSOLUTE RULES:\n"
            "- ZERO text anywhere. No letters. No numbers. No words. No acronyms. "
            "No labels. No symbols resembling characters. This is non-negotiable.\n"
            "- No human faces, hands, or bodies\n"
            "- No screens, monitors, phones, devices, cubes, or large rectangular shapes\n"
            "- No logos or branding\n"
            f"- Output size: {width}x{height} pixels"
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash-exp-image-generation",
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                img_bytes = part.inline_data.data
                illustration = Image.open(io.BytesIO(img_bytes))
                illustration = illustration.resize((width, height), Image.LANCZOS)
                log.info("    Topic illustration generated via Gemini")
                return illustration

        log.warning("    Gemini response contained no image")
        return None

    except Exception as e:
        log.warning("    Topic illustration generation failed: %s", e)
        return None


def generate_thumbnail_pil(
    raw_thumbnail_path: str,
    output_path: str,
    clip_title: str,
    guest_name: str = "",
    host_side: str = "left",
) -> bool:
    """Generate a branded SMEC AI thumbnail using PIL compositing.

    Builds a 1920x1080 thumbnail with dark navy background, glowing border,
    SMEC AI logo, headline text, guest name, and an AI-generated illustration
    of the clip topic on the right side.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError as e:
        log.warning("    Missing PIL for thumbnail generation: %s", e)
        return False

    try:
        CANVAS_W, CANVAS_H = 1920, 1080
        BG_COLOR = (10, 14, 39)  # #0a0e27
        BORDER_COLOR_1 = (74, 90, 255)  # #4a5aff
        BORDER_COLOR_2 = (123, 47, 255)  # #7b2fff

        # --- Canvas with gradient ---
        canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)
        draw = ImageDraw.Draw(canvas)

        # Subtle radial vignette: darken edges
        vignette = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        vig_draw = ImageDraw.Draw(vignette)
        for i in range(40):
            alpha = int(i * 2.5)
            margin = i * 8
            vig_draw.rectangle(
                [0, 0, margin, CANVAS_H], fill=(0, 0, 0, alpha)
            )
            vig_draw.rectangle(
                [CANVAS_W - margin, 0, CANVAS_W, CANVAS_H], fill=(0, 0, 0, alpha)
            )
            vig_draw.rectangle(
                [0, 0, CANVAS_W, margin], fill=(0, 0, 0, alpha)
            )
            vig_draw.rectangle(
                [0, CANVAS_H - margin, CANVAS_W, CANVAS_H], fill=(0, 0, 0, alpha)
            )
        canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), vignette).convert("RGB"))

        # --- AI-generated topic illustration (right side, composited early) ---
        illust_w = int(CANVAS_W * 0.55)
        illust_h = CANVAS_H
        illustration = _generate_topic_illustration(clip_title, illust_w, illust_h)

        if illustration is not None:
            import numpy as np

            # Screen blend: dark pixels become invisible, bright glows show through.
            illust_rgb = illustration.convert("RGB").resize(
                (illust_w, illust_h), Image.LANCZOS
            )
            illust_arr = np.array(illust_rgb, dtype=np.float32) / 255.0

            # Build fade mask for edges
            mask_arr = np.ones((illust_h, illust_w), dtype=np.float32)

            fade_left = int(illust_w * 0.45)
            mask_arr[:, :fade_left] *= np.linspace(0, 1, fade_left)[np.newaxis, :]

            fade_top = int(illust_h * 0.15)
            mask_arr[:fade_top, :] *= np.linspace(0, 1, fade_top)[:, np.newaxis]

            fade_bottom = int(illust_h * 0.15)
            mask_arr[-fade_bottom:, :] *= np.linspace(1, 0, fade_bottom)[:, np.newaxis]

            illust_arr *= mask_arr[:, :, np.newaxis]

            # Subtractive blend: remove Gemini's dark bg, keep only bright elements.
            # Subtract the median dark color (Gemini's bg), clamp to 0, then add.
            # This perfectly eliminates any ghost rectangles from bg mismatch.
            bg_sample = illust_arr[illust_arr.max(axis=2) < 0.15]
            if len(bg_sample) > 0:
                bg_color = np.median(
                    bg_sample.reshape(-1, 3), axis=0
                )
            else:
                bg_color = np.array([0.04, 0.055, 0.15])  # fallback: #0a0e27
            residual = np.clip(illust_arr - bg_color[np.newaxis, np.newaxis, :], 0, 1)

            canvas_arr = np.array(canvas, dtype=np.float32) / 255.0
            ix = CANVAS_W - illust_w
            canvas_arr[:, ix:, :] = np.clip(canvas_arr[:, ix:, :] + residual, 0, 1)
            canvas = Image.fromarray(
                (canvas_arr * 255).clip(0, 255).astype(np.uint8), mode="RGB"
            )

        # --- Glowing rounded-rect border (drawn AFTER illustration) ---
        border_margin = 40
        border_radius = 30

        glow_layer = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        for thickness in range(10, 0, -1):
            alpha = int(80 + (10 - thickness) * 18)
            r = (BORDER_COLOR_1[0] + BORDER_COLOR_2[0]) // 2
            g = (BORDER_COLOR_1[1] + BORDER_COLOR_2[1]) // 2
            b = (BORDER_COLOR_1[2] + BORDER_COLOR_2[2]) // 2
            m = border_margin - thickness
            glow_draw.rounded_rectangle(
                [m, m, CANVAS_W - m, CANVAS_H - m],
                radius=border_radius + thickness,
                outline=(r, g, b, alpha),
                width=2,
            )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=12))

        border_draw = ImageDraw.Draw(glow_layer)
        border_draw.rounded_rectangle(
            [border_margin, border_margin, CANVAS_W - border_margin, CANVAS_H // 2],
            radius=border_radius,
            outline=(*BORDER_COLOR_1, 240),
            width=3,
        )
        border_draw.rounded_rectangle(
            [border_margin, CANVAS_H // 2, CANVAS_W - border_margin, CANVAS_H - border_margin],
            radius=border_radius,
            outline=(*BORDER_COLOR_2, 240),
            width=3,
        )
        border_draw.rounded_rectangle(
            [border_margin, border_margin, CANVAS_W - border_margin, CANVAS_H - border_margin],
            radius=border_radius,
            outline=(
                (BORDER_COLOR_1[0] + BORDER_COLOR_2[0]) // 2,
                (BORDER_COLOR_1[1] + BORDER_COLOR_2[1]) // 2,
                (BORDER_COLOR_1[2] + BORDER_COLOR_2[2]) // 2,
                230,
            ),
            width=3,
        )
        canvas = Image.alpha_composite(canvas.convert("RGBA"), glow_layer).convert("RGB")

        # --- SMEC AI logo (top-left, recolored to white) ---
        assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
        logo_path = os.path.join(assets_dir, "smec_logo.png")
        if os.path.isfile(logo_path):
            logo = Image.open(logo_path).convert("RGBA")
            # Resize to ~200px wide, preserve aspect
            logo_w = 400
            logo_h = int(logo.height * logo_w / logo.width)
            logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

            # Recolor to white: set all non-transparent pixels to white
            r, g, b, a = logo.split()
            white = Image.new("L", logo.size, 255)
            logo = Image.merge("RGBA", (white, white, white, a))

            canvas.paste(logo, (80, 60), logo)

        # --- Headline text ---
        title_text = clip_title.upper()
        text_area_w = int(CANVAS_W * 0.50)  # left 50% of canvas
        text_x = 80

        # Auto-size font to fit
        font_size = 72
        font = None
        while font_size >= 32:
            try:
                font = ImageFont.truetype(FONT_PATH, font_size)
            except (IOError, OSError):
                font = ImageFont.load_default()
                break

            # Word-wrap
            words = title_text.split()
            lines: list[str] = []
            current_line = ""
            for word in words:
                test = f"{current_line} {word}".strip()
                bbox = font.getbbox(test)
                if bbox[2] - bbox[0] <= text_area_w:
                    current_line = test
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)

            # Check total height fits (max 3 lines preferred)
            line_height = int(font_size * 1.25)
            total_h = line_height * len(lines)
            if len(lines) <= 4 and total_h < 350:
                break
            font_size -= 4

        # Calculate total text block height (headline + optional subtitle)
        draw = ImageDraw.Draw(canvas)
        if font:
            line_height = int(font_size * 1.25)
            block_h = line_height * len(lines)
            if guest_name:
                block_h += 30 + 36  # gap + subtitle font size
            text_y_start = (CANVAS_H - block_h) // 2

            for i, line in enumerate(lines):
                y = text_y_start + i * line_height
                # Shadow
                draw.text((text_x + 2, y + 2), line, font=font, fill=(0, 0, 0))
                # Main text
                draw.text((text_x, y), line, font=font, fill=(255, 255, 255))

        # --- Guest name subtitle ---
        if guest_name:
            subtitle_y = text_y_start + len(lines) * int(font_size * 1.25) + 30
            try:
                subtitle_font = ImageFont.truetype(FONT_PATH, 36)
            except (IOError, OSError):
                subtitle_font = ImageFont.load_default()
            subtitle_text = f"WITH {guest_name.upper()}"
            # Accent color for "WITH" line
            draw.text(
                (text_x + 2, subtitle_y + 2),
                subtitle_text,
                font=subtitle_font,
                fill=(0, 0, 0),
            )
            draw.text(
                (text_x, subtitle_y),
                subtitle_text,
                font=subtitle_font,
                fill=(160, 170, 255),  # light blue-purple accent
            )

        # --- Save ---
        canvas.save(output_path, "JPEG", quality=95)
        log.info("    Thumbnail generated via PIL compositing")
        return True

    except Exception as e:
        log.warning("    PIL thumbnail generation failed: %s — keeping raw thumbnail", e)
        return False


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
    host_side: str = "left",
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

    # Detect side-by-side layout for specialized rendering
    sbs = is_side_by_side(raw_slice)

    formats = {
        "horizontal": render_horizontal,
        "square": render_square,
        "vertical": render_vertical,
    }

    # Track the clean horizontal frame path (before subs/logo) for thumbnail
    clean_horizontal_path = None

    for fmt_name, render_fn in formats.items():
        log.info("    Rendering %s...", fmt_name)

        # Step B: Render format
        fmt_video = os.path.join(clip_dir, f"_{fmt_name}_nosub.mp4")
        render_fn(raw_slice, fmt_video, sbs=sbs)

        # Extract clean thumbnail frame from horizontal before subs/logo
        if fmt_name == "horizontal":
            clean_horizontal_path = os.path.join(clip_dir, "_clean_thumbnail.jpg")
            extract_thumbnail(fmt_video, clean_horizontal_path)

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
            overlay_logo(sub_video, logo_path, final_output, fmt_name, sbs=sbs)
        else:
            os.rename(sub_video, final_output)

        # Clean up intermediate files
        for tmp_file in [fmt_video, sub_video]:
            if os.path.exists(tmp_file) and tmp_file != final_output:
                os.unlink(tmp_file)

    # Extract thumbnail — use clean frame (no subs/logo) for Gemini enhancement
    log.info("    Extracting thumbnail...")
    thumbnail_path = os.path.join(clip_dir, "thumbnail.jpg")
    raw_thumbnail_path = clean_horizontal_path or os.path.join(
        clip_dir, "_raw_thumbnail.jpg"
    )
    if not clean_horizontal_path:
        extract_thumbnail(
            os.path.join(clip_dir, "horizontal.mp4"),
            raw_thumbnail_path,
        )

    # Generate branded thumbnail via PIL compositing
    enhanced = generate_thumbnail_pil(
        raw_thumbnail_path,
        thumbnail_path,
        clip_title=clip.get("title", ""),
        guest_name=clip.get("guest_name", ""),
        host_side=host_side,
    )
    if not enhanced:
        # Fall back to raw thumbnail
        import shutil
        shutil.copy2(raw_thumbnail_path, thumbnail_path)
    # Clean up raw thumbnail
    if os.path.exists(raw_thumbnail_path):
        os.unlink(raw_thumbnail_path)

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

    host_side = "left"
    if "--host-side" in sys.argv:
        hs_idx = sys.argv.index("--host-side")
        if hs_idx + 1 < len(sys.argv):
            host_side = sys.argv[hs_idx + 1]
            if host_side not in ("left", "right"):
                log.error("--host-side must be 'left' or 'right', got '%s'", host_side)
                sys.exit(1)

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
        process_single_clip(clip, video_path, output_dir, logo_path, audio_path, host_side)

    log.info("All %d clips processed successfully!", len(clips))


if __name__ == "__main__":
    main()
