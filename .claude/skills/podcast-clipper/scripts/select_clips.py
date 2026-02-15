#!/usr/bin/env python3
"""
Step 2: Use Claude to analyze transcript and select 3-5 highlight clips.
Produces clips.json with timestamps, titles, and rationale.
"""

import argparse
import json
import logging
import os
import sys

import anthropic

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("select_clips")

CLIP_SELECTION_PROMPT = """You are an expert podcast editor for "The ROI from AI" by SMEC AI.

Analyze this transcript and identify {num_clips} high-value clips for social media. Each clip should be {clip_duration} seconds long.

SELECTION CRITERIA (prioritize in this order):
1. **Hook potential** — Does it start with a surprising stat, bold claim, or compelling question?
2. **Narrative completeness** — Does the clip tell a complete micro-story or make a full point?
3. **Quotable moments** — Contains a memorable phrase or insight worth sharing?
4. **Actionable insights** — Gives the audience something practical they can use?
5. **Emotional resonance** — Evokes curiosity, surprise, or "I need to share this"?

RULES:
- Each clip must be {clip_duration} seconds long
- Prefer natural sentence boundaries for start/end points
- Avoid clips that start or end mid-sentence
- Ensure clips don't overlap
- Spread clips across the episode (don't cluster them all at the start)
- Create a short, punchy slug (2-4 words, lowercase, hyphens) for each clip

Return ONLY valid JSON in this exact format:
```json
{
  "clips": [
    {
      "clip_number": 1,
      "title": "Short descriptive title",
      "slug": "short-slug-here",
      "start_time": 123.456,
      "end_time": 167.890,
      "rationale": "Why this clip was selected",
      "hook_line": "The opening line that grabs attention",
      "key_quote": "The most shareable quote from this clip"
    }
  ]
}
```

TRANSCRIPT:
{transcript_text}

SEGMENT TIMESTAMPS (for reference):
{segment_timestamps}
"""


def load_transcript(transcript_path: str) -> dict:
    """Load transcript.json."""
    with open(transcript_path) as f:
        return json.load(f)


def format_segment_timestamps(transcript: dict) -> str:
    """Format segment timestamps for the prompt."""
    lines = []
    for seg in transcript["segments"]:
        start_min = int(seg["start"] // 60)
        start_sec = seg["start"] % 60
        lines.append(f"[{start_min:02d}:{start_sec:05.2f}] {seg['text']}")
    return "\n".join(lines)


def select_clips(transcript: dict, num_clips: str = "3-5", clip_duration: str = "30-60") -> list[dict]:
    """Use Claude to select highlight clips from transcript."""
    client = anthropic.Anthropic()

    segment_timestamps = format_segment_timestamps(transcript)

    prompt = CLIP_SELECTION_PROMPT.replace(
        "{num_clips}", num_clips
    ).replace(
        "{clip_duration}", clip_duration
    ).replace(
        "{transcript_text}", transcript["full_text"]
    ).replace(
        "{segment_timestamps}", segment_timestamps
    )

    log.info("  Sending transcript to Claude for clip selection...")

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text

    # Extract JSON from response (handle markdown code blocks)
    if "```json" in response_text:
        json_start = response_text.index("```json") + 7
        json_end = response_text.index("```", json_start)
        response_text = response_text[json_start:json_end].strip()
    elif "```" in response_text:
        json_start = response_text.index("```") + 3
        json_end = response_text.index("```", json_start)
        response_text = response_text[json_start:json_end].strip()

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        log.error("  Claude did not return valid JSON. Response:\n%s", response_text[:500])
        # Fallback: create a single clip from the entire transcript
        log.info("  Falling back to full-length clip")
        segments = transcript.get("segments", [])
        if segments:
            return [{
                "clip_number": 1,
                "title": "Full Highlight",
                "slug": "full-highlight",
                "start_time": segments[0]["start"],
                "end_time": segments[-1]["end"],
                "rationale": "Auto-selected (full content)",
                "hook_line": "",
                "key_quote": "",
            }]
        return []
    return result["clips"]


def snap_to_word_boundaries(clips: list[dict], transcript: dict) -> list[dict]:
    """Snap clip boundaries to nearest word timestamps for clean cuts."""
    all_words = []
    for seg in transcript["segments"]:
        all_words.extend(seg.get("words", []))

    if not all_words:
        return clips

    for clip in clips:
        target_start = clip["start_time"]
        target_end = clip["end_time"]

        # Find nearest word start for clip start
        best_start = min(all_words, key=lambda w: abs(w["start"] - target_start))
        clip["start_time"] = best_start["start"]

        # Find nearest word end for clip end
        best_end = min(all_words, key=lambda w: abs(w["end"] - target_end))
        clip["end_time"] = best_end["end"]

        # Add 0.5s padding for clean cuts
        clip["start_time"] = max(0, clip["start_time"] - 0.5)
        clip["end_time"] = clip["end_time"] + 0.5

        # Extract word-level data for this clip's time range
        clip_words = [
            w for w in all_words
            if w["start"] >= clip["start_time"] and w["end"] <= clip["end_time"]
        ]
        clip["words"] = clip_words

    return clips


def main() -> None:
    parser = argparse.ArgumentParser(description="Select highlight clips from transcript")
    parser.add_argument("transcript", help="Path to transcript.json")
    parser.add_argument("output_dir", help="Output directory")
    parser.add_argument("--num-clips", default="3-5", help="Number of clips to select (e.g. '3-5' or '2')")
    parser.add_argument("--clip-duration", default="30-60", help="Clip duration in seconds (e.g. '30-60' or '45-90')")
    args = parser.parse_args()

    transcript_path = args.transcript
    output_dir = args.output_dir

    if not os.path.isfile(transcript_path):
        log.error("Error: Transcript not found: %s", transcript_path)
        sys.exit(1)

    transcript = load_transcript(transcript_path)
    log.info("  Loaded transcript with %d segments", len(transcript["segments"]))

    clips = select_clips(transcript, num_clips=args.num_clips, clip_duration=args.clip_duration)
    clips = snap_to_word_boundaries(clips, transcript)

    # Save clips.json
    output_path = os.path.join(output_dir, "clips.json")
    with open(output_path, "w") as f:
        json.dump({"clips": clips}, f, indent=2)

    log.info("  Selected %d clips:", len(clips))
    for clip in clips:
        duration = clip["end_time"] - clip["start_time"]
        log.info(
            "    Clip %d: %s (%.1fs, %.1f-%.1f)",
            clip["clip_number"], clip["title"],
            duration, clip["start_time"], clip["end_time"],
        )

    log.info("  Clips saved to %s", output_path)


if __name__ == "__main__":
    main()
