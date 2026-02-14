#!/usr/bin/env python3
"""
Step 1: Extract audio from video, normalize, and transcribe with Whisper.

Two-pass strategy:
  - Pass 1 (fast): 'tiny' model for full transcript (used for clip selection)
  - Pass 2 (accurate): 'small' model only on selected clip segments (for subtitles)

Produces transcript.json with word-level timestamps.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("transcribe")


def extract_and_normalize_audio(video_path: str, output_wav: str) -> None:
    """Extract audio from video, convert to 16kHz mono WAV with loudnorm."""
    log.info("  Extracting and normalizing audio from %s...", video_path)

    # Two-pass loudnorm for best results
    # Pass 1: Measure audio levels
    measure_cmd = [
        "ffmpeg", "-i", video_path,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-"
    ]
    result = subprocess.run(
        measure_cmd, capture_output=True, text=True, check=True
    )
    stderr = result.stderr
    json_start = stderr.rfind("{")
    json_end = stderr.rfind("}") + 1
    if json_start >= 0 and json_end > json_start:
        stats = json.loads(stderr[json_start:json_end])
        measured_i = stats.get("input_i", "-16")
        measured_tp = stats.get("input_tp", "-1.5")
        measured_lra = stats.get("input_lra", "11")
        measured_thresh = stats.get("input_thresh", "-27")
    else:
        measured_i = "-16"
        measured_tp = "-1.5"
        measured_lra = "11"
        measured_thresh = "-27"

    # Pass 2: Apply normalization with measured values
    normalize_cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-af", (
            f"loudnorm=I=-16:TP=-1.5:LRA=11:"
            f"measured_I={measured_i}:"
            f"measured_TP={measured_tp}:"
            f"measured_LRA={measured_lra}:"
            f"measured_thresh={measured_thresh}:"
            f"linear=true"
        ),
        "-ar", "16000",
        "-ac", "1",
        "-vn",
        output_wav
    ]
    subprocess.run(normalize_cmd, capture_output=True, check=True)
    log.info("  Audio extracted and normalized to %s", output_wav)


def transcribe_audio(audio_path: str, model_name: str = "tiny") -> dict:
    """Run Whisper transcription with word-level timestamps."""
    import whisper

    log.info("  Loading Whisper model (%s)...", model_name)
    model = whisper.load_model(model_name)

    log.info("  Transcribing with '%s' model...", model_name)
    result = model.transcribe(
        audio_path,
        word_timestamps=True,
        condition_on_previous_text=True,
        verbose=False,
    )
    return result


def transcribe_segment(
    audio_path: str,
    start: float,
    end: float,
    model_name: str = "small",
) -> dict:
    """Transcribe a specific segment of audio with a higher-quality model."""
    import whisper
    import numpy as np

    log.info("  Re-transcribing segment %.1f-%.1f with '%s' model...", start, end, model_name)

    model = whisper.load_model(model_name)
    audio = whisper.load_audio(audio_path)

    # Extract segment (audio is at 16kHz)
    sr = 16000
    start_sample = int(start * sr)
    end_sample = int(end * sr)
    segment_audio = audio[start_sample:end_sample]

    # Pad to 30 seconds if shorter (Whisper requirement)
    if len(segment_audio) < 30 * sr:
        segment_audio = np.pad(segment_audio, (0, 30 * sr - len(segment_audio)))

    result = model.transcribe(
        segment_audio,
        word_timestamps=True,
        condition_on_previous_text=False,
        verbose=False,
    )

    # Offset timestamps back to original timeline
    for seg in result.get("segments", []):
        seg["start"] += start
        seg["end"] += start
        for w in seg.get("words", []):
            w["start"] += start
            w["end"] += end if w["end"] + start > end else start

    return result


def format_transcript(whisper_result: dict) -> dict:
    """Format Whisper output into our transcript.json structure."""
    segments = []
    for seg in whisper_result.get("segments", []):
        words = []
        for w in seg.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": round(w["start"], 3),
                "end": round(w["end"], 3),
            })
        segments.append({
            "id": seg["id"],
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"].strip(),
            "words": words,
        })

    return {
        "language": whisper_result.get("language", "en"),
        "full_text": whisper_result.get("text", "").strip(),
        "segments": segments,
    }


def main() -> None:
    if len(sys.argv) < 3:
        log.error("Usage: transcribe.py <input_video> <output_dir> [--model MODEL]")
        sys.exit(1)

    video_path = sys.argv[1]
    output_dir = sys.argv[2]

    model_name = "tiny"
    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        if idx + 1 < len(sys.argv):
            model_name = sys.argv[idx + 1]

    if not os.path.isfile(video_path):
        log.error("Error: Video file not found: %s", video_path)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # Extract and normalize audio
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        extract_and_normalize_audio(video_path, audio_path)

        # Pass 1: Fast transcription for clip selection
        whisper_result = transcribe_audio(audio_path, model_name)

        # Format and save
        transcript = format_transcript(whisper_result)
        output_path = os.path.join(output_dir, "transcript.json")
        with open(output_path, "w") as f:
            json.dump(transcript, f, indent=2)

        # Save audio path for pass 2 re-transcription
        audio_ref_path = os.path.join(output_dir, "_audio_path.txt")
        with open(audio_ref_path, "w") as f:
            f.write(audio_path)

        log.info("  Transcript saved to %s", output_path)
        log.info("  Language: %s", transcript["language"])
        log.info("  Segments: %d", len(transcript["segments"]))

        # Don't delete audio yet — needed for pass 2
        log.info("  Audio preserved at %s for clip re-transcription", audio_path)

    except Exception:
        if os.path.exists(audio_path):
            os.unlink(audio_path)
        raise


if __name__ == "__main__":
    main()
