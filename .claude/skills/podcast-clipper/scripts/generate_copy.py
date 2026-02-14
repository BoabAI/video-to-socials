#!/usr/bin/env python3
"""
Step 4: Generate platform-optimized captions for each clip using Claude.
Produces captions.json per clip directory.
"""

import json
import logging
import os
import sys

import anthropic

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("generate_copy")

CAPTION_PROMPT = """You are a social media strategist for SMEC AI, an Australian AI consultancy.
Their podcast is "The ROI from AI" — it helps business leaders understand practical AI adoption.

Generate platform-optimized captions for a podcast clip.

CLIP TITLE: {title}
KEY QUOTE: {key_quote}
HOOK LINE: {hook_line}

CLIP TRANSCRIPT:
{transcript}

Generate captions for ALL FOUR platforms. Return ONLY valid JSON in this exact format:
```json
{{
  "linkedin": {{
    "headline": "Attention-grabbing headline (max 150 chars)",
    "body": "Professional body text. Lead with an insight hook. Include a CTA. 2-3 paragraphs max.",
    "hashtags": ["AIStrategy", "BusinessAI", "TheROIfromAI", "SMECAI", "ArtificialIntelligence"]
  }},
  "youtube": {{
    "title": "SEO-optimized title under 60 characters",
    "description": "Detailed description with context. Include relevant keywords naturally.",
    "tags": ["AI", "business AI", "ROI from AI", "SMEC AI", "artificial intelligence", "podcast"]
  }},
  "instagram": {{
    "caption": "Engaging hook with emojis. Conversational tone. End with a question or CTA.",
    "hashtags": ["AIStrategy", "BusinessAI", "TheROIfromAI", "SMECAI", "ArtificialIntelligence", "TechPodcast", "AIForBusiness", "DigitalTransformation", "Innovation", "FutureOfWork", "AIInsights", "BusinessStrategy", "TechLeadership", "AIAdoption", "SmartBusiness"]
  }},
  "tiktok": {{
    "caption": "Punchy, direct caption. Hook in first line. Keep it under 150 chars.",
    "hashtags": ["AI", "BusinessTips", "TheROIfromAI", "TechTok", "LearnOnTikTok", "AIStrategy", "FYP", "Business"]
  }}
}}
```

GUIDELINES:
- LinkedIn: Professional but not corporate. Lead with insight, not promotion. CTA should drive engagement (comment, share).
- YouTube: SEO-first. Title must include relevant keywords. Description should be 2-3 sentences.
- Instagram: Start with a hook emoji line. Be conversational. 15-20 hashtags mixing broad + niche.
- TikTok: Ultra-punchy. First line must hook. 5-8 trending + niche hashtags.
- ALL platforms: Reference "The ROI from AI" podcast and SMEC AI naturally.
"""


def get_clip_transcript(clip: dict) -> str:
    """Extract readable transcript from clip words."""
    words = clip.get("words", [])
    if words:
        return " ".join(w["word"] for w in words)
    return clip.get("title", "")


def generate_captions_for_clip(clip: dict) -> dict:
    """Use Claude to generate platform captions for a single clip."""
    client = anthropic.Anthropic()

    transcript = get_clip_transcript(clip)

    prompt_text = (
        CAPTION_PROMPT
        .replace("{title}", clip.get("title", "Untitled"))
        .replace("{key_quote}", clip.get("key_quote", ""))
        .replace("{hook_line}", clip.get("hook_line", ""))
        .replace("{transcript}", transcript)
    )

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt_text}],
    )

    response_text = message.content[0].text

    # Extract JSON from response
    if "```json" in response_text:
        json_start = response_text.index("```json") + 7
        json_end = response_text.index("```", json_start)
        response_text = response_text[json_start:json_end].strip()
    elif "```" in response_text:
        json_start = response_text.index("```") + 3
        json_end = response_text.index("```", json_start)
        response_text = response_text[json_start:json_end].strip()

    return json.loads(response_text)


def main() -> None:
    if len(sys.argv) < 2:
        log.error("Usage: generate_copy.py <output_dir>")
        sys.exit(1)

    output_dir = sys.argv[1]

    # Load clips.json
    clips_path = os.path.join(output_dir, "clips.json")
    if not os.path.isfile(clips_path):
        log.error("Error: clips.json not found in %s", output_dir)
        sys.exit(1)

    with open(clips_path) as f:
        clips_data = json.load(f)

    clips = clips_data["clips"]
    log.info("Generating captions for %d clips...", len(clips))

    for clip in clips:
        clip_num = clip["clip_number"]
        slug = clip.get("slug", "untitled")

        # Find clip directory
        clip_dir = None
        for entry in os.listdir(output_dir):
            if entry.startswith(f"clip_{clip_num:02d}_") and os.path.isdir(
                os.path.join(output_dir, entry)
            ):
                clip_dir = os.path.join(output_dir, entry)
                break

        if not clip_dir:
            log.warning("  Warning: directory not found for clip %d, skipping", clip_num)
            continue

        log.info("  Generating captions for clip %d: %s", clip_num, clip["title"])

        captions = generate_captions_for_clip(clip)

        # Save captions.json in clip directory
        captions_path = os.path.join(clip_dir, "captions.json")
        with open(captions_path, "w") as f:
            json.dump(captions, f, indent=2)

        log.info("    Saved to %s", captions_path)

    log.info("All captions generated successfully!")


if __name__ == "__main__":
    main()
