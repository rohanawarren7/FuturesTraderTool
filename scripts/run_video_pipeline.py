#!/usr/bin/env python3
"""
Video Analysis Pipeline Runner
Scans the downloads directory and processes each video through the full
AI pipeline (YouTube captions / faster-whisper → Gemini entry + outcome).

Usage (from WSL2):
    # Basic run
    python scripts/run_video_pipeline.py

    # Custom workers and fallback Whisper model size
    python scripts/run_video_pipeline.py --workers 2 --whisper-model small

    # Resume from a specific video ID
    python scripts/run_video_pipeline.py --start-from <video_id>
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from multiprocessing import Pool

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from database.db_manager import DBManager
from video_analysis.pipeline import TradingVideoPipeline

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def process_video(args: tuple) -> dict:
    """
    Worker function for multiprocessing.
    Each worker creates its own pipeline instance (its own Whisper + Gemini client).
    """
    video_path, trader_name, gemini_key, db_path, whisper_model = args
    video_id = video_path.stem

    db = DBManager(db_path)
    pipeline = TradingVideoPipeline(
        db=db,
        gemini_api_key=gemini_key,
        output_dir="./video_data",
        whisper_model_size=whisper_model,
    )

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    return pipeline.run_full_pipeline(youtube_url, trader_name, video_id,
                                       existing_video_path=video_path)


def main():
    parser = argparse.ArgumentParser(description="Run video analysis pipeline")
    parser.add_argument("--input-dir", default="/mnt/e/FuturesTraderTool/video_data/downloads",
                        help="Directory containing downloaded .mp4 files")
    parser.add_argument("--workers", type=int, default=2,
                        help="Number of parallel workers (default: 2)")
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model size (default: medium)")
    parser.add_argument("--start-from", default=None,
                        help="Resume from this video_id (skips earlier videos)")
    args = parser.parse_args()

    gemini_key = os.getenv("GEMINI_API_KEY")
    db_path = os.getenv("DB_PATH", "./database/trading_analysis.db")

    if not gemini_key:
        print("[ERROR] GEMINI_API_KEY not set in .env")
        sys.exit(1)

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"[ERROR] Input directory not found: {input_dir}")
        sys.exit(1)

    # Collect all .mp4 files from all subdirectories
    all_videos = sorted(input_dir.rglob("*.mp4"))
    print(f"[Runner] Found {len(all_videos)} video files in {input_dir}")

    # Resume support: skip videos before start-from ID
    if args.start_from:
        ids = [v.stem for v in all_videos]
        if args.start_from in ids:
            start_idx = ids.index(args.start_from)
            all_videos = all_videos[start_idx:]
            print(f"[Runner] Resuming from {args.start_from} "
                  f"({len(all_videos)} videos remaining)")
        else:
            print(f"[WARN] --start-from ID '{args.start_from}' not found, "
                  f"processing all videos")

    # Build task list: (video_path, trader_name, gemini_key, db_path, whisper_model)
    # trader_name derived from parent folder name (e.g. "traderdrysdale")
    tasks = [
        (v, v.parent.name, gemini_key, db_path, args.whisper_model)
        for v in all_videos
    ]

    log_file = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    all_results = []

    print(f"[Runner] Starting {args.workers} worker(s). Log: {log_file}")

    if args.workers == 1:
        for task in tasks:
            result = process_video(task)
            all_results.append(result)
            _print_progress(result, all_results, len(tasks))
    else:
        with Pool(processes=args.workers) as pool:
            for result in pool.imap_unordered(process_video, tasks):
                all_results.append(result)
                _print_progress(result, all_results, len(tasks))

    log_file.write_text(json.dumps(all_results, indent=2))

    total_trades = sum(r.get("trades_saved", 0) for r in all_results)
    complete = sum(1 for r in all_results if r.get("status") == "complete")
    skipped = sum(1 for r in all_results if r.get("status") == "skipped")

    print(f"\n[Runner] COMPLETE")
    print(f"  Videos processed:  {complete}")
    print(f"  Videos skipped:    {skipped}")
    print(f"  Total trades saved: {total_trades}")

    # Check DB
    db = DBManager(db_path)
    trades = db.get_all_video_trades(min_confidence=0.65)
    labelled = [t for t in trades if t.get("outcome") in ("WIN", "LOSS")]
    print(f"\n  DB — high-confidence trades: {len(trades)}")
    print(f"  DB — labelled (WIN/LOSS):    {len(labelled)}")

    if len(labelled) < 30:
        print(f"\n[WARN] Only {len(labelled)} labelled trades. "
              f"Need 30 minimum for pattern mining. "
              f"Consider: more videos, lower confidence threshold (0.55), "
              f"or use --whisper-model large.")


def _print_progress(result: dict, all_results: list, total: int):
    vid = result.get("video_id", "?")
    status = result.get("status", "?")
    trades = result.get("trades_saved", 0)
    print(f"  [{len(all_results)}/{total}] {vid}: {status} — {trades} trades")


if __name__ == "__main__":
    main()
