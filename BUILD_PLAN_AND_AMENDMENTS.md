# Combined Spec + Accelerated Plan: Amendments & Build Plan
## VWAP Futures Trading Bot

---

## Decisions Made

| Decision | Choice | Implication |
|---|---|---|
| Environment | **WSL2** | All shell scripts from accelerated plan work as-is |
| Historical data | **Tradovate REST API (free)** | Build `data/tradovate_data_provider.py` |
| Outcome labelling | **Second Gemini pass** | Add `label_trade_outcome()` to pipeline |

---

## Architecture of the Second Gemini Pass (Outcome Labelling)

**Why this is the best option:**

Forward price tracking has a fatal flaw for video data: we don't know the precise real-world UTC timestamp of each trade in the video. Without that synchronisation, we can't look up historical OHLCV data to compute theoretical outcomes. The math is unknowable.

Manual annotation is 17+ hours of work for 200 trades and defeats the automation goal.

The second Gemini pass wins because:
1. Most live-streaming VWAP traders (Drysdale included) display a P&L panel on screen throughout the session — Gemini can read these numbers directly
2. The audio transcript is **already extracted** by Whisper — we can search for exit keywords (`"stopped out"`, `"taking profit"`, `"out"`, `"exiting"`) in the 1–30 minute window after each detected entry, at zero additional cost
3. Gemini can synthesise both the visual P&L and the audio evidence to give a high-confidence label
4. It captures real outcomes including partial exits, moved stops, and early exits — things forward price tracking cannot know

**Expected outcome labelling rate: ~70–80% of detected trades labelled WIN/LOSS** (vs ~0% from forward tracking, ~100% but 17hrs from manual)

### Implementation — `label_trade_outcome()` method

Add to `TradingVideoPipeline` in `video_analysis/pipeline.py`:

```python
GEMINI_OUTCOME_PROMPT = """
A futures trade was entered {direction} at approximately {entry_time:.0f} seconds into this video.
These frames were captured at 2, 5, 10, 20, and 30 minutes after that entry.

Also, the audio transcript in this window contains:
"{transcript_excerpt}"

Look for any of:
  - A P&L panel showing a dollar amount (positive = WIN, negative = LOSS)
  - The position being closed or flat
  - Account balance changing from the entry balance
  - Visual confirmation of a filled exit order

Return ONLY valid JSON:
{{
  "outcome": "WIN" | "LOSS" | "UNKNOWN",
  "confidence": 0.0-1.0,
  "evidence": "brief explanation of what you saw or heard"
}}

If the trade is still open or you cannot determine the result, return UNKNOWN.
"""

def label_trade_outcome(self, video_path: Path, entry_event: dict,
                         entry_analysis: dict, transcript: dict) -> dict:
    """
    Second Gemini pass: extracts frames 2, 5, 10, 20, 30 min after entry.
    Fuses with audio transcript excerpt to label WIN / LOSS / UNKNOWN.
    """
    entry_t = entry_event["timestamp_seconds"]
    direction = entry_analysis.get("direction", "UNKNOWN")

    # Pull audio transcript in the 30-minute window after entry
    excerpt_segments = [
        seg["text"] for seg in transcript.get("segments", [])
        if entry_t < seg["start"] < entry_t + 1800
    ]
    transcript_excerpt = " ".join(excerpt_segments)[:500]  # cap to avoid token overrun

    # Extract frames at fixed intervals after entry
    frames_dir = self.output_dir / "outcome_frames" / video_path.stem
    frames_dir.mkdir(parents=True, exist_ok=True)

    offsets_minutes = [2, 5, 10, 20, 30]
    frame_images = []
    for mins in offsets_minutes:
        t = entry_t + (mins * 60)
        frame_path = frames_dir / f"outcome_{entry_t:.0f}s_plus{mins}m.jpg"
        cmd = [
            "ffmpeg", "-ss", str(t), "-i", str(video_path),
            "-vframes", "1", "-q:v", "2", "-y", str(frame_path)
        ]
        result = subprocess.run(cmd, capture_output=True)
        if frame_path.exists():
            import PIL.Image
            frame_images.append(PIL.Image.open(frame_path))

    if not frame_images:
        return {"outcome": "UNKNOWN", "confidence": 0.0, "evidence": "No frames extracted"}

    prompt = GEMINI_OUTCOME_PROMPT.format(
        direction=direction,
        entry_time=entry_t,
        transcript_excerpt=transcript_excerpt
    )

    response = self.gemini_model.generate_content(
        [prompt] + frame_images,
        generation_config={"response_mime_type": "application/json"}
    )
    try:
        return json.loads(response.text)
    except json.JSONDecodeError:
        return {"outcome": "UNKNOWN", "confidence": 0.0, "evidence": "Parse error"}
```

Update `save_trade_record()` to accept and write the `outcome` and `outcome_confidence` from this second pass.

Update `run_full_pipeline()` to call `label_trade_outcome()` for every detected trade and pass the result to `save_trade_record()`.

---

## All Bugs to Fix Before Building

| # | Location | Bug | Fix |
|---|---|---|---|
| 1 | `prop_firm_simulator.py` | `self.current_day` never set; `close_day()` always computes `daily_pnl` vs `account_size` | Add `self.opening_balance_today` field; set it in `close_day()` on first call per day; compute `daily_pnl = eod_balance - self.opening_balance_today` |
| 2 | `prop_firm_simulator.py` | Apex consistency rule uses `self.daily_pnl` which is only updated EOD; intraday breach never fires | Track intraday daily PnL in `update_intraday()` as `self.intraday_daily_pnl = current_equity - self.opening_balance_today`; use this in `check_breach()` |
| 3 | `VWAP_Bot_Strategy.pine` | Both `alertcondition` payloads hardcode `"quantity":1` | Replace with `"quantity":{{strategy.order.contracts}}` or pass `contracts_to_use` via a dynamic alert variable |
| 4 | `requirements.txt` / Task 1.1 | `pip install sqlite3` installs wrong package | Remove `sqlite3` from pip install — it is stdlib |
| 5 | `walk_forward.py` | `optuna.create_study(n_jobs=4)` is invalid | Move to `study.optimize(objective, n_trials=n_trials, n_jobs=4)` |
| 6 | `VWAP_Bot_Strategy.pine` | `strategy.netprofit[ta.barssince(is_new_session)]` uses dynamic index — fails at runtime | Rewrite using a `var float session_start_netprofit` variable that captures `strategy.netprofit` on `is_new_session` and holds it for the session |

---

## All Gaps to Address

### Gap 1: WSL2 Setup (replaces all Linux/Mac shell commands)

All scripts in the accelerated plan work as-is under WSL2. The only WSL2-specific steps to add to Task 1:

```bash
# Install WSL2 — run this in Windows PowerShell (Admin), then restart
wsl --install

# After restart, open Ubuntu terminal and set up the project there
# Your Windows files are accessible at /mnt/c/Users/tamar/FuturesTraderTool/
cd /mnt/c/Users/tamar/FuturesTraderTool

# Install system dependencies
sudo apt-get update
sudo apt-get install -y ffmpeg python3.11 python3.11-venv python3-pip git

# Verify ffmpeg
ffmpeg -version
```

VS Code connects to WSL2 via the "WSL" extension — open the project folder from within the WSL terminal using `code .`

GPU note: If using Whisper with GPU acceleration in WSL2, CUDA drivers must be installed on the Windows host (not inside WSL). For CPU-only Whisper (medium model), no extra setup needed.

### Gap 2: Tradovate Data Provider (replaces all data source references in spec)

New file: `data/tradovate_data_provider.py`

```python
"""
Fetches historical OHLCV bars from Tradovate REST API.
Free with a Tradovate account (paper or live).
Docs: https://api.tradovate.com
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import os
import time

TRADOVATE_DEMO_URL = "https://demo.tradovateapi.com/v1"
TRADOVATE_LIVE_URL = "https://live.tradovateapi.com/v1"

class TradovateDataProvider:

    def __init__(self, username: str, password: str, app_id: str,
                 use_demo: bool = True):
        self.base_url = TRADOVATE_DEMO_URL if use_demo else TRADOVATE_LIVE_URL
        self.token = None
        self._authenticate(username, password, app_id)

    def _authenticate(self, username: str, password: str, app_id: str):
        resp = requests.post(f"{self.base_url}/auth/accesstokenrequest", json={
            "name": username,
            "password": password,
            "appId": app_id,
            "appVersion": "1.0",
            "cid": 0,
            "sec": ""
        })
        resp.raise_for_status()
        self.token = resp.json()["accessToken"]

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get_bars(self, symbol: str, unit: str, unit_number: int,
                 start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetches historical bars.
        unit: 'Minute', 'Hour', 'Day', 'Week', 'Month'
        unit_number: e.g. 5 for 5-minute bars
        Returns DataFrame: timestamp, open, high, low, close, volume
        """
        # Tradovate chart API: /md/getchart
        payload = {
            "symbol": symbol,           # e.g. "MESM5" (current front month)
            "chartDescription": {
                "underlyingType": "MinuteBar",
                "elementSize": unit_number,
                "elementSizeUnit": "UnderlyingUnits",
                "withHistogram": False
            },
            "timeRange": {
                "asFarAsTimestamp": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "asMuchAsTimestamp": end.strftime("%Y-%m-%dT%H:%M:%SZ")
            }
        }
        resp = requests.post(f"{self.base_url}/md/getchart",
                            json=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()

        bars = data.get("bars", [])
        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                  "c": "close", "v": "volume"})
        return df[["timestamp", "open", "high", "low", "close", "volume"]]

    def get_12_months_5m(self, symbol: str = "MESM5") -> pd.DataFrame:
        """Fetches 12 months of 5-minute bars, chunked to avoid API limits."""
        end = datetime.utcnow()
        start = end - timedelta(days=365)

        # Tradovate limits: fetch in 30-day chunks
        chunks = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=30), end)
            print(f"Fetching {cursor.date()} → {chunk_end.date()}")
            chunk = self.get_bars(symbol, "Minute", 5, cursor, chunk_end)
            if not chunk.empty:
                chunks.append(chunk)
            cursor = chunk_end
            time.sleep(0.5)  # be polite to the API

        if not chunks:
            raise ValueError("No data returned from Tradovate")

        df = pd.concat(chunks).drop_duplicates("timestamp").sort_values("timestamp")
        df = df.reset_index(drop=True)
        print(f"Total bars: {len(df)} | Range: {df['timestamp'].min()} → {df['timestamp'].max()}")
        return df
```

**Note on symbol names:** MES front-month contract rolls quarterly. `MESM5` = June 2025, `MESU5` = September 2025, etc. The data provider script should fetch the correct front-month or use Tradovate's continuous contract if available. Add a helper to `get_current_front_month()`.

### Gap 3: `live_trades` Table Population

New file: `execution/tradovate_poller.py`

```python
"""
Polls Tradovate account for filled orders every 30 seconds.
Writes fills to live_trades and daily_account_summary tables.
Run alongside the main bot as a background process.
"""
import time
import requests
from database.db_manager import DBManager

class TradovatePoller:
    def __init__(self, provider, db_manager: DBManager, poll_interval: int = 30):
        self.provider = provider   # reuse TradovateDataProvider for auth
        self.db = db_manager
        self.poll_interval = poll_interval
        self.seen_fill_ids = set()

    def poll_once(self):
        resp = requests.get(
            f"{self.provider.base_url}/order/list",
            headers=self.provider._headers()
        )
        orders = resp.json()
        for order in orders:
            if order.get("ordStatus") == "Filled" and order["id"] not in self.seen_fill_ids:
                self.seen_fill_ids.add(order["id"])
                self.db.insert_live_trade_from_order(order)

    def run(self):
        print(f"[Poller] Starting — polling every {self.poll_interval}s")
        while True:
            try:
                self.poll_once()
            except Exception as e:
                print(f"[Poller] Error: {e}")
            time.sleep(self.poll_interval)
```

Add `insert_live_trade_from_order()` to `db_manager.py` that maps Tradovate order fields to the `live_trades` schema.

### Gap 4: Outcome Labelling
Addressed above — second Gemini pass added to pipeline.

### Gap 5: Delta Data (live execution)
Accept the `close > open` OHLCV proxy for the Pine Script MVP with explicit documentation. When Sierra Chart upgrade occurs (Task 11.3), replace with real cumulative delta from Rithmic tick data. Add a comment block in `signal_generator.py` and the Pine Script noting this limitation.

### Gap 6: Whisper Multiprocessing on Windows/WSL2
Under WSL2, Python uses `fork` (Linux behaviour) — `multiprocessing.Pool` with Whisper works correctly. The Windows-specific `spawn` issue is resolved by the WSL2 choice. No code change needed.

---

## Amended Build Order

Follows the accelerated plan's task structure with additions:

**Task 1 — Environment** (add WSL2 install as Step 1.0)

**Task 2 — Core Modules** (same as accelerated plan, with bug fixes applied during build):
- 2.1: configs
- 2.2: `prop_firm_simulator.py` (with Bug 1 + 2 fixes)
- 2.3: VWAP + market state + signal generator
- 2.4: `db_manager.py` (add `insert_live_trade_from_order()`)
- **2.5 (new):** `data/tradovate_data_provider.py`
- **2.6 (new):** `execution/tradovate_poller.py`

**Tasks 3 + 4 — Video download + pipeline** (add `label_trade_outcome()` second Gemini pass)

**Task 5 — Pattern mining** (unchanged — now has outcome labels to work with)

**Task 6 — Backtest** (Step 6.1 uses Tradovate data provider instead of yfinance)

**Task 7 — WFO** (Bug 5 fix: `n_jobs` on `optimize()`)

**Task 8 — Pine Script** (Bug 3 + 6 fixes applied before deploying)

**Tasks 9–11** (unchanged)

---

## Disk Space Requirement (Amended)

| Scenario | Estimate |
|---|---|
| 40 videos × 2hr avg × 2GB/hr | 160GB |
| 60 videos × 4hr avg × 6GB/hr | 1.4TB worst case |
| **Recommended minimum free space** | **400GB** |

Pre-flight checklist should say **400GB minimum**, not 100GB.

---

## Summary of All Amendments

### To `CLAUDE_CODE_PROJECT_SPEC.md`:
1. Add WSL2 setup section before Task 1
2. Replace all data source references with Tradovate API + `tradovate_data_provider.py`
3. Add `execution/tradovate_poller.py` to file structure and build order
4. Fix Bug 1 in `PropFirmSimulator.close_day()`
5. Fix Bug 2 in `PropFirmSimulator.check_breach()`
6. Fix Bug 3 in Pine Script alert payloads
7. Fix Bug 6 in Pine Script daily PnL tracking

### To `ACCELERATED_PLAN.md`:
1. Add WSL2 install as Step 1.0 in Task 1
2. Remove `sqlite3` from pip install (Bug 4)
3. Replace Task 6.1 data source section with Tradovate data provider
4. Add `tradovate_poller.py` build instruction in Task 2
5. Fix Optuna `n_jobs` in Task 7 (Bug 5)
6. Add `label_trade_outcome()` instruction in Task 4
7. Update disk space in pre-flight checklist to 400GB
8. All `nohup`/`watch`/`crontab` commands already work under WSL2 — no rewrite needed
