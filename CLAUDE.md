# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Claude Code Skill** called "podcast-clipper" that automates the "Raw-to-Ready" pipeline for SMEC AI's "The ROI from AI" podcast. It transforms raw podcast recordings into branded social media clips with subtitles, logo overlays, and platform-optimized captions.

The skill lives in `.claude/skills/podcast-clipper/` and is invoked via Claude Code with commands like:
```
Use podcast-clipper on meeting_01.mp4
```

## Architecture

The pipeline is a 5-step sequential process orchestrated by `pipeline.py`:

1. **Transcribe** (`transcribe.py`) — Two-pass Whisper strategy: `tiny` model for fast full transcript, `small` model for accurate per-clip subtitles later. Uses ffmpeg two-pass loudnorm for audio normalization before transcription.

2. **Select Clips** (`select_clips.py`) — Sends transcript to Claude (Sonnet) to identify 3-5 high-value 30-60s clips. Snaps clip boundaries to word-level timestamps with 0.5s padding.

3. **Process Video** (`process_video.py`) — For each clip: slices raw video, renders three formats (horizontal 1920x1080, square 1080x1080, vertical 1080x1920 with blurred background), generates ASS subtitles, burns them in, overlays SMEC logo. Vertical format uses blurred-background technique to solve the aspect ratio problem.

4. **Generate Captions** (`generate_copy.py`) — Uses Claude (Sonnet) to produce platform-specific copy (LinkedIn, YouTube, Instagram, TikTok) for each clip.

5. **Summary** — Generates `summary.md` with clip details and LinkedIn caption previews.

### Key Files

- `.claude/skills/podcast-clipper/SKILL.md` — Skill definition and Claude Code instructions
- `.claude/skills/podcast-clipper/scripts/pipeline.py` — Main orchestrator
- `.claude/skills/podcast-clipper/scripts/transcribe.py` — Whisper transcription with loudnorm
- `.claude/skills/podcast-clipper/scripts/select_clips.py` — Claude-powered clip selection
- `.claude/skills/podcast-clipper/scripts/process_video.py` — Multi-format video rendering with ffmpeg
- `.claude/skills/podcast-clipper/scripts/generate_copy.py` — Claude-powered caption generation
- `.claude/skills/podcast-clipper/assets/smec_logo.png` — Brand logo for overlay

### Data Flow

```
input.mp4 → transcript.json → clips.json → clip_NN_<slug>/{horizontal,square,vertical}.mp4 + captions.json → summary.md
```

Intermediate audio is preserved at a temp path (referenced via `_audio_path.txt`) for pass-2 re-transcription.

## Dependencies

- **Python 3**: `openai-whisper`, `anthropic`, `numpy`
- **System**: `ffmpeg`, `ffprobe`
- **Fonts**: FreeSans Bold at `/usr/share/fonts/truetype/freefont/FreeSansBold.ttf`
- **Environment**: `ANTHROPIC_API_KEY` must be set (used by `select_clips.py` and `generate_copy.py`)

## Running

### Full pipeline
```bash
python3 ~/.claude/skills/podcast-clipper/scripts/pipeline.py <input_video> <output_dir> [--logo <logo.png>]
```

### Individual steps
```bash
python3 ~/.claude/skills/podcast-clipper/scripts/transcribe.py <input_video> <output_dir> [--model MODEL]
python3 ~/.claude/skills/podcast-clipper/scripts/select_clips.py <output_dir>/transcript.json <output_dir>
python3 ~/.claude/skills/podcast-clipper/scripts/process_video.py <input_video> <output_dir> [--logo <logo.png>]
python3 ~/.claude/skills/podcast-clipper/scripts/generate_copy.py <output_dir>
```

## Known Issues

- Whisper may have CUDA/Triton compatibility issues — use `CUDA_VISIBLE_DEVICES=""` to force CPU
- Short inputs (<60s) will produce only 1 clip since Claude falls back to full-length
- The Claude API model used is `claude-sonnet-4-5-20250929` in both `select_clips.py` and `generate_copy.py`
