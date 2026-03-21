"""
AI Video Analysis Pipeline
Processes YouTube trading session recordings to extract codifiable trade rules.

Pipeline per video:
  1. Download video (yt-dlp)
  2. Extract audio (FFmpeg → 16kHz mono MP3)
  3. Transcribe audio (Whisper medium)
  4. Detect trade events from transcript (keyword cluster matching)
  5. Extract video frames at event timestamps (FFmpeg)
  6. Analyse each frame with Gemini Vision — entry pass (Setup 1)
  7. Analyse post-entry frames with Gemini Vision — outcome pass (Setup 2)
  8. Fuse audio + visual results → write to raw_video_trades table
"""

import subprocess
import json
import os
import sys
from pathlib import Path
from datetime import datetime
import sqlite3

sys.path.insert(0, str(Path(__file__).parent.parent))

import whisper
from google import genai
from google.genai import types
import PIL.Image

from database.db_manager import DBManager


# ------------------------------------------------------------------
# Gemini prompts
# ------------------------------------------------------------------

GEMINI_ENTRY_PROMPT = """
You are an expert futures trader analyst. Analyse this screenshot from a live trading session.

Return ONLY valid JSON matching this exact schema (no extra text):
{
  "trade_detected": true/false,
  "instrument": "MES/MNQ/ES/NQ/CL/null",
  "direction": "BUY/SELL/null",
  "entry_trigger": "MEAN_REVERSION_LONG/MEAN_REVERSION_SHORT/VWAP_CONTINUATION_LONG/VWAP_CONTINUATION_SHORT/SD2_EXTREME_FADE_LONG/SD2_EXTREME_FADE_SHORT/OTHER/null",
  "vwap_position": "ABOVE_SD2/ABOVE_SD1/ABOVE_VWAP/BELOW_VWAP/BELOW_SD1/BELOW_SD2/null",
  "market_state": "BALANCED/IMBALANCED_BULL/IMBALANCED_BEAR/VOLATILE_TRANS/LOW_ACTIVITY/null",
  "delta_direction": "POSITIVE/NEGATIVE/NEUTRAL/null",
  "delta_flip": true/false,
  "volume_spike": true/false,
  "session_phase": "OPEN/MID/CLOSE/null",
  "vwap_bands_visible": true/false,
  "order_flow_tool_visible": true/false,
  "confidence": 0.0-1.0,
  "notes": "brief explanation of what you see"
}
"""

GEMINI_OUTCOME_PROMPT = """
A futures trade was entered {direction} at approximately {entry_time:.0f} seconds into this video.
These frames were captured at 2, 5, 10, 20, and 30 minutes after that entry.

The audio transcript in this window contains:
"{transcript_excerpt}"

Look for any of:
  - A P&L panel showing a dollar amount (positive = WIN, negative = LOSS)
  - The position being closed or showing as flat
  - Account balance changing from the entry balance
  - Visual confirmation of a filled exit order
  - The trader explicitly stating the result

Return ONLY valid JSON:
{{
  "outcome": "WIN/LOSS/UNKNOWN",
  "confidence": 0.0-1.0,
  "evidence": "brief explanation of what you saw or heard"
}}

If the trade is still open or you cannot determine the result, return UNKNOWN.
"""

# Audio keywords that flag potential trade events
AUDIO_TRADE_KEYWORDS = {
    "entry":        ["entering", "going long", "going short", "taking", "buying",
                     "selling", "in the trade", "filled", "entry", "i'm in"],
    "exit":         ["exiting", "out", "closing", "taking profit", "stopped out",
                     "covering", "we're out", "i'm out", "exit"],
    "vwap":         ["vwap", "v-wap", "weighted average", "bands", "standard deviation"],
    "delta":        ["delta", "cumulative delta", "order flow", "aggressive buyers",
                     "aggressive sellers", "footprint"],
    "market_state": ["balanced", "imbalanced", "trending", "ranging", "rotation"],
    "reasoning":    ["because", "reason", "waiting for", "looking for",
                     "setup", "criteria", "the reason"],
}


class TradingVideoPipeline:
    """
    Orchestrates the full video → trade record pipeline for one video at a time.
    """

    def __init__(
        self,
        db: DBManager,
        gemini_api_key: str,
        output_dir: str = "./video_data",
        whisper_model_size: str = "medium",
    ):
        self.db = db
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.gemini_client = genai.Client(api_key=gemini_api_key)
        self.gemini_model_name = "gemini-2.0-flash"

        print(f"[Pipeline] Loading Whisper {whisper_model_size} model...")
        self.whisper_model = whisper.load_model(whisper_model_size)
        print("[Pipeline] Whisper ready.")

    # ------------------------------------------------------------------
    # Step 1: Download
    # ------------------------------------------------------------------

    def download_video(self, youtube_url: str, video_id: str) -> Path:
        output_path = self.output_dir / "downloads" / f"{video_id}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists():
            print(f"[Pipeline] Already downloaded: {video_id}")
            return output_path

        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
            "--merge-output-format", "mp4",
            "-o", str(output_path),
            "--no-warnings", "--quiet", "--progress",
            youtube_url,
        ]
        subprocess.run(cmd, check=True)
        return output_path

    # ------------------------------------------------------------------
    # Step 2: Extract audio
    # ------------------------------------------------------------------

    def extract_audio(self, video_path: Path) -> Path:
        audio_path = self.output_dir / "audio" / f"{video_path.stem}_audio.mp3"
        audio_path.parent.mkdir(parents=True, exist_ok=True)

        if audio_path.exists():
            return audio_path

        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-ac", "1", "-ar", "16000",
            "-y", str(audio_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return audio_path

    # ------------------------------------------------------------------
    # Step 3: Transcribe
    # ------------------------------------------------------------------

    def transcribe_audio(self, audio_path: Path) -> dict:
        transcript_path = self.output_dir / "transcripts" / f"{audio_path.stem}.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)

        if transcript_path.exists():
            return json.loads(transcript_path.read_text())

        result = self.whisper_model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language="en",
        )
        transcript_path.write_text(json.dumps(result, indent=2))
        return result

    # ------------------------------------------------------------------
    # Step 4: Detect trade events from transcript
    # ------------------------------------------------------------------

    def detect_trade_events(self, transcript: dict) -> list[dict]:
        """
        Scans transcript segments for keyword clusters.
        Flags a segment if 2+ category groups match (reduces false positives ~70%).
        """
        events = []
        for segment in transcript.get("segments", []):
            text = segment["text"].lower()
            matched = [
                cat for cat, kws in AUDIO_TRADE_KEYWORDS.items()
                if any(kw in text for kw in kws)
            ]
            if len(matched) >= 2:
                events.append({
                    "timestamp_seconds":  segment["start"],
                    "text":               segment["text"],
                    "matched_categories": matched,
                    "confidence":         min(1.0, len(matched) / 4),
                })
        return events

    # ------------------------------------------------------------------
    # Step 5: Extract frames at event timestamps
    # ------------------------------------------------------------------

    def extract_frame(self, video_path: Path, timestamp_s: float,
                      output_path: Path) -> bool:
        """Extracts a single frame at timestamp_s seconds. Returns True if successful."""
        cmd = [
            "ffmpeg",
            "-ss", str(max(0, timestamp_s - 2)),  # 2s before event
            "-i", str(video_path),
            "-vframes", "1", "-q:v", "2",
            "-y", str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True)
        return output_path.exists() and result.returncode == 0

    def extract_frames_at_events(
        self, video_path: Path, events: list[dict]
    ) -> list[dict]:
        frames_dir = self.output_dir / "frames" / video_path.stem
        frames_dir.mkdir(parents=True, exist_ok=True)

        for event in events:
            t = event["timestamp_seconds"]
            frame_path = frames_dir / f"entry_{t:.1f}s.jpg"
            if self.extract_frame(video_path, t, frame_path):
                event["frame_path"] = str(frame_path)

        return events

    # ------------------------------------------------------------------
    # Step 6: Gemini entry analysis
    # ------------------------------------------------------------------

    def analyse_entry_frame(self, frame_path: Path) -> dict:
        img = PIL.Image.open(frame_path)
        response = self.gemini_client.models.generate_content(
            model=self.gemini_model_name,
            contents=[GEMINI_ENTRY_PROMPT, img],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        try:
            return json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return {"trade_detected": False, "confidence": 0.0,
                    "notes": "Gemini parse error (entry)"}

    # ------------------------------------------------------------------
    # Step 7: Gemini outcome analysis (second pass)
    # ------------------------------------------------------------------

    def label_trade_outcome(
        self,
        video_path: Path,
        entry_event: dict,
        entry_analysis: dict,
        transcript: dict,
    ) -> dict:
        """
        Extracts frames at 2, 5, 10, 20, 30 minutes after entry.
        Fuses with audio transcript excerpt to label WIN / LOSS / UNKNOWN.
        """
        entry_t = entry_event["timestamp_seconds"]
        direction = entry_analysis.get("direction", "UNKNOWN")

        # Pull transcript excerpt for the 30-minute window after entry
        excerpt = " ".join(
            seg["text"]
            for seg in transcript.get("segments", [])
            if entry_t < seg["start"] < entry_t + 1800
        )[:500]

        # Extract post-entry frames
        outcome_dir = self.output_dir / "outcome_frames" / video_path.stem
        outcome_dir.mkdir(parents=True, exist_ok=True)

        frame_images = []
        for mins in [2, 5, 10, 20, 30]:
            t = entry_t + (mins * 60)
            frame_path = outcome_dir / f"outcome_{entry_t:.0f}s_plus{mins}m.jpg"
            if self.extract_frame(video_path, t, frame_path):
                try:
                    frame_images.append(PIL.Image.open(frame_path))
                except Exception:
                    pass

        if not frame_images:
            return {
                "outcome": "UNKNOWN",
                "confidence": 0.0,
                "evidence": "No outcome frames extracted (video too short?)",
            }

        prompt = GEMINI_OUTCOME_PROMPT.format(
            direction=direction or "UNKNOWN",
            entry_time=entry_t,
            transcript_excerpt=excerpt,
        )

        response = self.gemini_client.models.generate_content(
            model=self.gemini_model_name,
            contents=[prompt] + frame_images,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        try:
            return json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return {
                "outcome": "UNKNOWN",
                "confidence": 0.0,
                "evidence": "Gemini parse error (outcome)",
            }

    # ------------------------------------------------------------------
    # Step 8: Save fused record
    # ------------------------------------------------------------------

    def save_trade_record(
        self,
        video_id: str,
        trader_name: str,
        event: dict,
        entry_analysis: dict,
        outcome: dict,
    ):
        if not entry_analysis.get("trade_detected"):
            return

        self.db.insert_video_trade({
            "video_id":           video_id,
            "trader_name":        trader_name,
            "timestamp_video":    event.get("timestamp_seconds"),
            "timestamp_utc":      None,
            "instrument":         entry_analysis.get("instrument"),
            "direction":          entry_analysis.get("direction"),
            "entry_trigger":      entry_analysis.get("entry_trigger"),
            "vwap_position":      entry_analysis.get("vwap_position"),
            "market_state":       entry_analysis.get("market_state"),
            "delta_direction":    entry_analysis.get("delta_direction"),
            "delta_flip":         entry_analysis.get("delta_flip", False),
            "volume_spike":       entry_analysis.get("volume_spike", False),
            "session_phase":      entry_analysis.get("session_phase"),
            "audio_confidence":   event.get("confidence", 0.5),
            "visual_confidence":  entry_analysis.get("confidence", 0.5),
            "outcome":            outcome.get("outcome", "UNKNOWN"),
            "outcome_confidence": outcome.get("confidence", 0.0),
            "outcome_evidence":   outcome.get("evidence"),
            "r_multiple":         None,
            "notes":              entry_analysis.get("notes"),
        })

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run_full_pipeline(
        self,
        youtube_url: str,
        trader_name: str,
        video_id: str,
        existing_video_path: Path = None,
    ) -> dict:
        """
        Runs the complete pipeline for one video.
        Returns a summary dict with counts and status.
        Skips the video if it's already in the database.

        If existing_video_path is provided, skips the download step entirely
        and uses the pre-downloaded file directly.
        """
        if self.db.video_already_processed(video_id):
            print(f"[Pipeline] {video_id} already processed — skipping.")
            return {"video_id": video_id, "status": "skipped"}

        print(f"[Pipeline] Processing {video_id} by {trader_name}")

        if existing_video_path is not None and Path(existing_video_path).exists():
            video_path = Path(existing_video_path)
            print(f"[Pipeline] Using pre-downloaded file: {video_path}")
        else:
            video_path = self.download_video(youtube_url, video_id)
        audio_path = self.extract_audio(video_path)
        transcript = self.transcribe_audio(audio_path)
        events = self.detect_trade_events(transcript)
        print(f"[Pipeline] {len(events)} audio trade events detected")

        events = self.extract_frames_at_events(video_path, events)

        trades_saved = 0
        for event in events:
            if "frame_path" not in event:
                continue

            entry_analysis = self.analyse_entry_frame(Path(event["frame_path"]))
            outcome = {"outcome": "UNKNOWN", "confidence": 0.0, "evidence": ""}

            if entry_analysis.get("trade_detected"):
                outcome = self.label_trade_outcome(
                    video_path, event, entry_analysis, transcript
                )
                self.save_trade_record(
                    video_id, trader_name, event, entry_analysis, outcome
                )
                trades_saved += 1

        print(f"[Pipeline] {video_id} complete — {trades_saved} trades saved")
        return {
            "video_id":           video_id,
            "audio_events":       len(events),
            "trades_saved":       trades_saved,
            "status":             "complete",
        }
