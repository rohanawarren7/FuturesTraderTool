"""
Fetches historical OHLCV bars from the Tradovate REST API.
Free with any Tradovate account (paper or live).
API docs: https://api.tradovate.com

Usage:
    from data.tradovate_data_provider import TradovateDataProvider
    provider = TradovateDataProvider(username, password, app_id, use_demo=True)
    df = provider.get_12_months_5m("MESM5")
    df.to_csv("data/MES_5m_12months.csv", index=False)
"""

import requests
import pandas as pd
import time
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


TRADOVATE_DEMO_URL = "https://demo.tradovateapi.com/v1"
TRADOVATE_LIVE_URL = "https://live.tradovateapi.com/v1"

# Quarterly expiry month codes used in Tradovate contract symbols
QUARTERLY_MONTHS = {
    3:  "H",  # March
    6:  "M",  # June
    9:  "U",  # September
    12: "Z",  # December
}


def get_current_front_month(base: str = "MES") -> str:
    """
    Returns the Tradovate front-month symbol for today's date.
    Rolls to next quarter if within 5 days of expiry (third Friday of month).
    e.g. "MESM5" for June 2025, "MESU5" for September 2025.
    """
    now = datetime.utcnow()
    year_2 = now.year % 100

    # Find the next expiry quarter month
    for month in sorted(QUARTERLY_MONTHS.keys()):
        if now.month < month or (now.month == month and now.day < 15):
            return f"{base}{QUARTERLY_MONTHS[month]}{year_2}"

    # Past December — roll to March next year
    return f"{base}H{(year_2 + 1) % 100}"


class TradovateDataProvider:
    """
    Authenticates with Tradovate and fetches historical bar data.
    Handles token refresh and chunked requests for large date ranges.
    """

    TOKEN_LIFETIME_SECONDS = 4800  # Tradovate tokens last ~80 minutes

    def __init__(
        self,
        username: str,
        password: str,
        app_id: str,
        use_demo: bool = True,
    ):
        self.base_url = TRADOVATE_DEMO_URL if use_demo else TRADOVATE_LIVE_URL
        self.username = username
        self.password = password
        self.app_id = app_id
        self._token: Optional[str] = None
        self._token_expiry: datetime = datetime.min
        self._authenticate()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _authenticate(self):
        resp = requests.post(
            f"{self.base_url}/auth/accesstokenrequest",
            json={
                "name": self.username,
                "password": self.password,
                "appId": self.app_id,
                "appVersion": "1.0",
                "cid": 0,
                "sec": "",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errorText" in data:
            raise ValueError(f"Tradovate auth failed: {data['errorText']}")
        self._token = data["accessToken"]
        self._token_expiry = datetime.utcnow() + timedelta(seconds=self.TOKEN_LIFETIME_SECONDS)
        print(f"[TradovateDataProvider] Authenticated. Token valid until {self._token_expiry}")

    def _ensure_token(self):
        if datetime.utcnow() >= self._token_expiry - timedelta(minutes=5):
            print("[TradovateDataProvider] Refreshing token...")
            self._authenticate()

    def _headers(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------
    # Bar data fetching
    # ------------------------------------------------------------------

    def get_bars(
        self,
        symbol: str,
        bar_minutes: int,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetches historical bars for a single date range chunk.

        symbol:      Tradovate contract symbol, e.g. "MESM5"
        bar_minutes: Bar size in minutes (e.g. 5 for 5-minute bars)
        start / end: UTC datetimes

        Returns a DataFrame with columns:
            timestamp (UTC), open, high, low, close, volume
        """
        payload = {
            "symbol": symbol,
            "chartDescription": {
                "underlyingType": "MinuteBar",
                "elementSize": bar_minutes,
                "elementSizeUnit": "UnderlyingUnits",
                "withHistogram": False,
            },
            "timeRange": {
                "asFarAsTimestamp": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "asMuchAsTimestamp": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        }

        resp = requests.post(
            f"{self.base_url}/md/getchart",
            json=payload,
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        bars = data.get("bars", [])
        if not bars:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(bars)

        # Tradovate field names: timestamp, o, h, l, c, upVolume+downVolume
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})

        # Volume = upVolume + downVolume if separate, else 'v'
        if "upVolume" in df.columns and "downVolume" in df.columns:
            df["volume"] = df["upVolume"].fillna(0) + df["downVolume"].fillna(0)
        elif "v" in df.columns:
            df["volume"] = df["v"]
        else:
            df["volume"] = 0

        return df[["timestamp", "open", "high", "low", "close", "volume"]].copy()

    def get_history(
        self,
        symbol: str,
        bar_minutes: int = 5,
        lookback_days: int = 365,
        chunk_days: int = 30,
    ) -> pd.DataFrame:
        """
        Fetches a long history by making chunked requests (to stay within API limits).
        Saves progress to a local cache file to allow resuming.

        symbol:        e.g. "MESM5"
        bar_minutes:   5 for 5m bars
        lookback_days: Total history to fetch (default 365 = 1 year)
        chunk_days:    Days per API request (default 30)
        """
        end = datetime.utcnow().replace(second=0, microsecond=0)
        start = end - timedelta(days=lookback_days)

        cache_path = Path("data") / f"{symbol}_{bar_minutes}m_cache.csv"
        chunks: list[pd.DataFrame] = []

        # Load any previously cached data
        if cache_path.exists():
            cached = pd.read_csv(cache_path, parse_dates=["timestamp"])
            cached["timestamp"] = pd.to_datetime(cached["timestamp"], utc=True)
            if not cached.empty:
                # Only fetch data newer than what's cached
                start = cached["timestamp"].max().to_pydatetime() + timedelta(minutes=bar_minutes)
                chunks.append(cached)
                print(f"[TradovateDataProvider] Loaded {len(cached)} cached bars. "
                      f"Fetching from {start.date()}")

        cursor = start
        total_fetched = 0

        while cursor < end:
            chunk_end = min(cursor + timedelta(days=chunk_days), end)
            print(f"[TradovateDataProvider] Fetching {symbol} "
                  f"{cursor.date()} → {chunk_end.date()} ...")
            try:
                chunk = self.get_bars(symbol, bar_minutes, cursor, chunk_end)
                if not chunk.empty:
                    chunks.append(chunk)
                    total_fetched += len(chunk)
                    print(f"  → {len(chunk)} bars")
            except Exception as e:
                print(f"  [WARN] Fetch failed: {e}. Skipping chunk.")

            cursor = chunk_end
            time.sleep(0.5)  # Rate limiting

        if not chunks:
            raise ValueError(f"No data returned for {symbol}")

        df = (
            pd.concat(chunks, ignore_index=True)
            .drop_duplicates("timestamp")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

        # Save updated cache
        cache_path.parent.mkdir(exist_ok=True)
        df.to_csv(cache_path, index=False)
        print(f"[TradovateDataProvider] Done. {len(df)} total bars "
              f"({df['timestamp'].min()} → {df['timestamp'].max()}). "
              f"Saved to {cache_path}")
        return df

    def get_12_months_5m(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Convenience wrapper: 12 months of 5-minute bars for the given symbol."""
        if symbol is None:
            symbol = get_current_front_month("MES")
            print(f"[TradovateDataProvider] Using front-month symbol: {symbol}")
        return self.get_history(symbol, bar_minutes=5, lookback_days=365)

    @staticmethod
    def from_env(use_demo: bool = True) -> "TradovateDataProvider":
        """Constructs a provider using credentials from environment variables."""
        return TradovateDataProvider(
            username=os.environ["TRADOVATE_USERNAME"],
            password=os.environ["TRADOVATE_PASSWORD"],
            app_id=os.environ["TRADOVATE_APP_ID"],
            use_demo=use_demo,
        )
