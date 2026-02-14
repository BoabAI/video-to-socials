---
name: podcast-clipper
description: >
  Process podcast/video recordings into branded social media clips.
  Transcribes audio, identifies highlight moments, renders multi-format video
  (16:9, 1:1, 9:16) with burned-in subtitles and logo overlay,
  and generates platform-optimized captions for LinkedIn, YouTube, Instagram, and TikTok.
allowed-tools: Bash(python3 *), Bash(ffmpeg *), Bash(ffprobe *), Bash(pip *), Read, Write, Glob
---

# Podcast Clipper Skill

You are a podcast video processing assistant. When the user asks you to process a podcast, video recording, or create social media clips from a video, follow this workflow.

## Prerequisites

Before running the pipeline, verify:

1. **Input video exists** — validate with ffprobe:
   ```bash
   ffprobe -v quiet -print_format json -show_format -show_streams "<input_video>"
   ```

2. **Logo file** — check for `~/.claude/skills/podcast-clipper/assets/smec_logo.png`. If missing, ask the user to provide it.

3. **Dependencies installed** — run:
   ```bash
   python3 -c "import whisper" 2>/dev/null || pip install openai-whisper
   ```

## Pipeline Execution

Run each step sequentially. The scripts are in `~/.claude/skills/podcast-clipper/scripts/`.

### Step 1: Transcribe
```bash
python3 ~/.claude/skills/podcast-clipper/scripts/transcribe.py "<input_video>" "<output_dir>"
```
Extracts audio, normalizes volume, runs Whisper with word-level timestamps.
Output: `<output_dir>/transcript.json`

### Step 2: Select Clips
```bash
python3 ~/.claude/skills/podcast-clipper/scripts/select_clips.py "<output_dir>/transcript.json" "<output_dir>"
```
Uses Claude to analyze the transcript and pick 3-5 high-value clips (30-60s each).
Output: `<output_dir>/clips.json`

### Step 3: Process Video
```bash
python3 ~/.claude/skills/podcast-clipper/scripts/process_video.py "<input_video>" "<output_dir>" --logo ~/.claude/skills/podcast-clipper/assets/smec_logo.png
```
For each clip: slices video, renders horizontal/square/vertical formats, generates ASS subtitles, burns them in, overlays logo.
Output: `<output_dir>/clip_NN_<slug>/horizontal.mp4`, `square.mp4`, `vertical.mp4`, `thumbnail.jpg`

### Step 4: Generate Captions
```bash
python3 ~/.claude/skills/podcast-clipper/scripts/generate_copy.py "<output_dir>"
```
Generates platform-optimized captions (LinkedIn, YouTube, Instagram, TikTok) for each clip.
Output: `<output_dir>/clip_NN_<slug>/captions.json`

### Step 5: Generate Summary
After all steps complete, create a `summary.md` in the output directory listing:
- Total clips generated
- For each clip: title, duration, file paths, and the LinkedIn caption as a preview
- Any warnings or issues encountered

## Presenting Results

After the pipeline completes:
1. Show the user the summary
2. List all generated files with their paths
3. Show the LinkedIn caption for each clip (as a preview of caption quality)
4. Ask if they want to adjust any clips or regenerate captions

## Error Handling

- If transcription fails, check audio codec and try re-extracting with different settings
- If Claude API calls fail, check ANTHROPIC_API_KEY is set
- If ffmpeg fails on a specific format, skip that format and continue with others
- Always show progress: "Processing clip 2 of 5..."

## Usage Examples

- "Process my podcast recording podcast.mp4 for social media"
- "Use podcast-clipper on meeting_01.mp4 for LinkedIn and Instagram"
- "Create social clips from /path/to/video.mp4"
