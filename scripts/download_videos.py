#!/usr/bin/env python3
"""
Multi-channel VWAP/Order Flow video downloader.
Tier 1 targets run by default. See YOUTUBE_CHANNEL_GUIDE.md for channel rankings.

Usage (from WSL2):
    python scripts/download_videos.py              # Tier 1 only (default)
    python scripts/download_videos.py --tier 2     # Tier 2 only
    python scripts/download_videos.py --tier all   # All tiers
    python scripts/download_videos.py --channel robertrother
    python scripts/download_videos.py --tier all --dry-run
    nohup python scripts/download_videos.py --tier 1 > logs/download_tier1.log 2>&1 &
"""

import subprocess
import json
import argparse
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path("/mnt/e/FuturesTraderTool/video_data/downloads")
LOG_DIR = Path("logs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ============================================================
# CHANNEL REGISTRY  (ranked per YOUTUBE_CHANNEL_GUIDE.md)
# ============================================================

MAX_SIZE_GB = 25  # Hard cap per channel folder

TIER_1_TARGETS = [
    {
        "name": "traderdrysdale",
        "display": "Chris Drysdale — VWAP Wave System",
        "url": "https://www.youtube.com/@traderdrysdale/videos",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 18000,
        "priority": 1,
    },
    {
        "name": "traderdrysdale_streams",
        "display": "Chris Drysdale — Live Streams",
        "url": "https://www.youtube.com/@traderdrysdale/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 18000,
        "priority": 1,
    },
    {
        "name": "patrickwieland",
        "display": "Patrick Wieland — BDH Network (Daily NQ)",
        "url": "https://www.youtube.com/@PatrickWieland/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 18000,
        "priority": 2,
    },
    {
        "name": "robertrother",
        "display": "Robert Rother — VWAP + Bookmap (30 yrs exp)",
        "url": "https://www.youtube.com/@RobertRother/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 14400,
        "priority": 2,
    },
    {
        "name": "chartfanaticslive",
        "display": "Chart Fanatics Live — Matt Owen (Daily Order Flow)",
        "url": "https://www.youtube.com/@chartfanaticslive/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 14400,
        "priority": 3,
    },
    {
        "name": "topsteptv",
        "display": "TopstepTV — Prop Firm Daily Streams",
        "url": "https://www.youtube.com/@TopstepOfficial/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 18000,
        "priority": 4,
    },
]

TIER_2_TARGETS = [
    {
        "name": "bookmap_webinars",
        "display": "Bookmap Pro Trader Webinars (Austin Silver, Robert Rother)",
        "url": "https://www.youtube.com/playlist?list=PLzaGy-3oukoSiWjRrKvA8jmboMuUs4L0R",
        "max_videos": 999,
        "min_duration": 2400,
        "max_duration": 7200,
        "priority": 5,
    },
    {
        "name": "speculatorseth",
        "display": "SpeculatorSeth — Daily Order Flow Livestreams",
        "url": "https://www.youtube.com/@SpeculatorSeth/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 14400,
        "priority": 5,
    },
    {
        "name": "axiafutures",
        "display": "Axia Futures — Pro Prop Firm Order Flow",
        "url": "https://www.youtube.com/AxiaFutures/videos",
        "max_videos": 999,
        "min_duration": 1200,
        "max_duration": 7200,
        "priority": 6,
    },
    {
        "name": "tradeproacademy",
        "display": "TRADEPRO Academy — VWAP Wave Strategy Streams",
        "url": "https://www.youtube.com/c/TradeProAcademy/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 10800,
        "priority": 6,
    },
    {
        "name": "tradertv_live",
        "display": "TraderTV Live — Toronto Trading Floor",
        "url": "https://www.youtube.com/@TraderTVLive/streams",
        "max_videos": 999,
        "min_duration": 1800,
        "max_duration": 14400,
        "priority": 7,
    },
]

TIER_3_TARGETS = [
    {
        "name": "orderflowdojo",
        "display": "Order Flow Dojo — Jigsaw DOM Education",
        "url": "https://www.youtube.com/@orderflowdojo/videos",
        "max_videos": 999,
        "min_duration": 600,
        "max_duration": 7200,
        "priority": 8,
    },
    {
        "name": "mattowentrades",
        "display": "Matt Owen — Personal Channel",
        "url": "https://www.youtube.com/@mattowentrades/videos",
        "max_videos": 999,
        "min_duration": 600,
        "max_duration": 7200,
        "priority": 8,
    },
]

ALL_TARGETS = TIER_1_TARGETS + TIER_2_TARGETS + TIER_3_TARGETS


def parse_args():
    parser = argparse.ArgumentParser(description="Download VWAP trading videos")
    parser.add_argument(
        "--tier", choices=["1", "2", "3", "all"], default="1",
        help="Which tier of channels to download (default: 1)"
    )
    parser.add_argument(
        "--channel", type=str, default=None,
        help="Download a single channel by name (e.g. --channel robertrother)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List videos to download without downloading"
    )
    return parser.parse_args()


def get_targets(tier: str) -> list:
    if tier == "1":
        return TIER_1_TARGETS
    elif tier == "2":
        return TIER_2_TARGETS
    elif tier == "3":
        return TIER_3_TARGETS
    return ALL_TARGETS


def get_video_list(target: dict) -> list:
    cmd = [
        "yt-dlp", "--flat-playlist",
        "--print", "%(id)s|%(title)s|%(duration)s|%(upload_date)s",
        "--no-warnings",
        "--match-filter",
        f"duration >= {target['min_duration']} & duration <= {target['max_duration']}",
        target["url"],
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    videos = []
    for line in result.stdout.strip().split("\n"):
        if "|" not in line:
            continue
        parts = line.split("|")
        vid_id = parts[0].strip()
        if not vid_id:
            continue
        videos.append({
            "id":          vid_id,
            "title":       parts[1] if len(parts) > 1 else "",
            "duration":    int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            "upload_date": parts[3] if len(parts) > 3 else "",
            "url":         f"https://www.youtube.com/watch?v={vid_id}",
        })
    # Sort longest-first so highest-value content downloads before the size cap is hit
    videos.sort(key=lambda v: v["duration"], reverse=True)
    return videos[:target.get("max_videos", 999)]


def download_video(video: dict, output_dir: Path, dry_run: bool = False) -> dict:
    output_path = output_dir / f"{video['id']}.mp4"

    if output_path.exists():
        print(f"  [SKIP] Already exists: {video['id']}")
        return {"id": video["id"], "status": "already_exists", "path": str(output_path)}

    if dry_run:
        mins = video["duration"] // 60
        print(f"  [DRY-RUN] {video['title'][:65]} ({mins}m)")
        return {"id": video["id"], "status": "dry_run"}

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(output_path),
        "--no-warnings", "--quiet", "--progress",
        "--retries", "3",
        "--fragment-retries", "3",
        video["url"],
    ]
    try:
        subprocess.run(cmd, check=True, timeout=3600)
        size_mb = output_path.stat().st_size / (1024**2) if output_path.exists() else 0
        print(f"  [OK] {video['id']} — {size_mb:.0f}MB")
        return {"id": video["id"], "title": video["title"],
                "status": "downloaded", "path": str(output_path), "size_mb": size_mb}
    except subprocess.TimeoutExpired:
        return {"id": video["id"], "title": video["title"], "status": "timeout", "path": None}
    except subprocess.CalledProcessError as e:
        return {"id": video["id"], "title": video["title"],
                "status": "error", "error": str(e), "path": None}


def main():
    args = parse_args()

    targets = get_targets(args.tier)
    if args.channel:
        targets = [t for t in ALL_TARGETS if t["name"] == args.channel]
        if not targets:
            print(f"[ERROR] Channel '{args.channel}' not found. Valid names:")
            for t in ALL_TARGETS:
                print(f"  {t['name']} — {t['display']}")
            return

    log_file = LOG_DIR / f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    all_results = []

    print(f"\n{'='*60}")
    print(f" VWAP Bot Video Downloader — Tier {args.tier}")
    print(f" Channels: {len(targets)}  |  Dry run: {args.dry_run}")
    print(f" Output: {OUTPUT_DIR}")
    print(f" Free space: ", end="", flush=True)
    import shutil
    free_gb = shutil.disk_usage(OUTPUT_DIR).free / (1024**3)
    print(f"{free_gb:.0f}GB available")
    print(f"{'='*60}\n")

    for target in sorted(targets, key=lambda x: x["priority"]):
        channel_dir = OUTPUT_DIR / target["name"]
        channel_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n[Priority {target['priority']}] {target['display']}")
        print(f"  Fetching video list...")
        videos = get_video_list(target)
        print(f"  Found {len(videos)} qualifying videos (sorted longest-first)")

        # Track existing folder size so cap accounts for already-downloaded files
        existing_bytes = sum(
            f.stat().st_size for f in channel_dir.rglob("*.mp4") if f.exists()
        )
        channel_bytes = existing_bytes
        cap_bytes = MAX_SIZE_GB * 1024**3

        if existing_bytes > 0:
            print(f"  Existing: {existing_bytes / 1024**3:.2f}GB — cap: {MAX_SIZE_GB}GB")

        for i, video in enumerate(videos, 1):
            if channel_bytes >= cap_bytes:
                print(f"  [CAP] {MAX_SIZE_GB}GB reached — stopping this channel")
                break

            remaining_gb = (cap_bytes - channel_bytes) / 1024**3
            mins = video["duration"] // 60
            print(f"  [{i}/{len(videos)}] {video['title'][:60]} ({mins}m) — {remaining_gb:.1f}GB remaining")
            result = download_video(video, channel_dir, dry_run=args.dry_run)
            result["channel"] = target["name"]
            all_results.append(result)

            if result.get("size_mb"):
                channel_bytes += result["size_mb"] * 1024**2

    log_file.write_text(json.dumps(all_results, indent=2))

    downloaded = sum(1 for r in all_results if r["status"] == "downloaded")
    skipped    = sum(1 for r in all_results if r["status"] == "already_exists")
    failed     = sum(1 for r in all_results if r["status"] in ("error", "timeout"))
    dry_run    = sum(1 for r in all_results if r["status"] == "dry_run")

    print(f"\n{'='*60}")
    print(f" COMPLETE")
    print(f"  Downloaded:  {downloaded}")
    print(f"  Skipped:     {skipped}")
    print(f"  Failed:      {failed}")
    if dry_run:
        print(f"  Would download: {dry_run}")
    print(f"  Total ready: {downloaded + skipped}")
    print(f"  Log: {log_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
