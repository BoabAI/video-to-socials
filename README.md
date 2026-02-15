# Podcast Clipper

A Claude Code skill that transforms raw podcast recordings into branded social media clips with subtitles, logo overlays, and platform-optimized captions.

## Prerequisites

- Python 3.10+
- ffmpeg / ffprobe
- `ANTHROPIC_API_KEY` in a `.env` file

```bash
pip install openai-whisper anthropic numpy Pillow
```

## Usage

```bash
# Basic — produces 3-5 clips, 30-60s each
CUDA_VISIBLE_DEVICES="" python3 .claude/skills/podcast-clipper/scripts/pipeline.py <input_video> <output_dir>

# With logo overlay
CUDA_VISIBLE_DEVICES="" python3 .claude/skills/podcast-clipper/scripts/pipeline.py <input_video> <output_dir> --logo <logo.png>

# Custom clip count and duration
CUDA_VISIBLE_DEVICES="" python3 .claude/skills/podcast-clipper/scripts/pipeline.py <input_video> <output_dir> --num-clips 2 --clip-duration 45-90
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--logo` | SMEC logo | Path to logo PNG for overlay |
| `--num-clips` | `3-5` | Number of clips to select |
| `--clip-duration` | `30-60` | Target clip duration in seconds |

## Output

For each clip the pipeline produces:

```
<output_dir>/
  transcript.json
  clips.json
  summary.md
  clip_01_<slug>/
    horizontal.mp4   # 1920x1080
    square.mp4        # 1080x1080
    vertical.mp4      # 1080x1920
    thumbnail.jpg
    captions.json     # LinkedIn, YouTube, Instagram, TikTok copy
```

## Pipeline Steps

1. **Transcribe** — Whisper extracts word-level timestamps
2. **Select Clips** — Claude identifies the best moments
3. **Process Video** — Renders 3 formats with burned-in subtitles and logo
4. **Generate Captions** — Claude writes platform-specific copy
5. **Summary** — Markdown overview of all clips

## Running Tests

```bash
CUDA_VISIBLE_DEVICES="" python3 test_pipeline.py
```

Runs 3 segments through the full pipeline and validates subtitle placement, contrast, logo presence, and file integrity.
