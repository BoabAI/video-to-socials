# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A **Claude Code Skill** called "podcast-clipper" that automates the "Raw-to-Ready" pipeline for SMEC AI's "The ROI from AI" podcast. Transforms raw podcast recordings into branded social media clips with subtitles, logo overlays, and platform-optimized captions.

The source video (`podcast_combined.mp4`) is 1280x360 — two 640x360 participant feeds side-by-side. All rendering logic accounts for this layout.

## Running

```bash
# Full pipeline
python3 .claude/skills/podcast-clipper/scripts/pipeline.py <input_video> <output_dir> [--logo <logo.png>] [--num-clips 3-5] [--clip-duration 30-60]

# E2E test (3 segments × 3 formats, with automated visual quality checks)
CUDA_VISIBLE_DEVICES="" python3 test_pipeline.py

# Individual pipeline steps
python3 .claude/skills/podcast-clipper/scripts/transcribe.py <input_video> <output_dir>
python3 .claude/skills/podcast-clipper/scripts/select_clips.py <output_dir>/transcript.json <output_dir> [--num-clips 3-5] [--clip-duration 30-60]
python3 .claude/skills/podcast-clipper/scripts/process_video.py <input_video> <output_dir> [--logo <logo.png>]
python3 .claude/skills/podcast-clipper/scripts/generate_copy.py <output_dir>
```

## Architecture

Five-step sequential pipeline orchestrated by `pipeline.py`:

1. **Transcribe** (`transcribe.py`) — Two-pass Whisper: `tiny` for fast full transcript, `small` for accurate per-clip subtitles. Uses ffmpeg two-pass loudnorm. Audio path preserved via `_audio_path.txt` for pass-2 re-transcription.

2. **Select Clips** (`select_clips.py`) — Claude Sonnet (`claude-sonnet-4-5-20250929`) identifies 3-5 clips (30-60s). Snaps boundaries to word timestamps with 0.5s padding. Falls back to full-length clip on parse failure.

3. **Process Video** (`process_video.py`) — The most complex step. For each clip:
   - Detects side-by-side layout (width:height > 3:1)
   - Renders 3 formats with SBS-aware logic:
     - **Horizontal** (1920×1080): scales strip to full width, dark `#111111` bg
     - **Square** (1080×1080): splits participants, stacks vertically with 8px divider
     - **Vertical** (1080×1920): stacks participants in upper 2/3, dark lower third for subtitles
   - Generates ASS subtitles (word-chunked, format-specific margins, `BorderStyle=3` semi-transparent box)
   - Burns subtitles via ffmpeg `ass` filter
   - Overlays logo top-right of video content, auto-recolors to white on dark backgrounds using `colorlevels`

4. **Generate Captions** (`generate_copy.py`) — Claude Sonnet produces LinkedIn, YouTube, Instagram, TikTok copy per clip.

5. **Summary** — Generates `summary.md` with clip details and LinkedIn previews.

### Data Flow

```
input.mp4 → transcript.json → clips.json → clip_NN_<slug>/{horizontal,square,vertical}.mp4 + captions.json → summary.md
```

### Key Files

| File | Purpose |
|------|---------|
| `.claude/skills/podcast-clipper/scripts/process_video.py` | Video rendering, SBS detection, subtitles, logo overlay (~700 lines) |
| `.claude/skills/podcast-clipper/scripts/pipeline.py` | Orchestrator |
| `test_pipeline.py` | E2E test suite with PIL/numpy visual quality validation |
| `.claude/skills/podcast-clipper/assets/smec_logo.png` | SMEC AI logo (1483×344, purple on transparent) |

## E2E Test Suite

`test_pipeline.py` extracts 3 diverse 30s segments (intro/mid/deep), runs the full pipeline, then validates:

- **Subtitle placement**: geometric zone check ensures subs don't overlap participant faces
- **Subtitle contrast**: luminance ratio between text (>200) and background (<100), minimum 3.0×
- **Logo detection**: color-channel analysis (blue > 40, blue > green×1.5) for the purple SMEC logo
- **Logo size**: minimum 190px rendered width
- **File integrity**: resolution, codec, bitrate, file size checks

Output: `test_e2e_output/test_report.md` with per-format pass/fail table and extracted analysis frames.

## Dependencies

- **Python**: `openai-whisper`, `anthropic`, `numpy`, `Pillow`
- **System**: `ffmpeg`, `ffprobe`, FreeSans Bold font
- **Environment**: `ANTHROPIC_API_KEY` in `.env`, `CUDA_VISIBLE_DEVICES=""` to avoid Triton bug

## FFmpeg Patterns

- Never use `color=` filter with `d=N` for dividers in `filter_complex` with video inputs — duration mismatch causes massive file bloat. Use `pad` filter instead.
- SBS splitting: `crop=half_w:h:offset:0,scale=W:-2,crop=W:target_h` preserves aspect ratio.
- Logo recoloring: `colorlevels=rimin=0:rimax=0.4:gimin=0:gimax=0.4:bimin=0:bimax=0.4` pushes dark purple to white.
- Background brightness sampling: extract 1 frame with PIL, measure luminance at logo position to decide recolor.
