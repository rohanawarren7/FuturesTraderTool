import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


class StateStore:
    """
    Lightweight JSON-backed state store for live trading services.

    This is intentionally simple so the webhook server can survive restarts
    without introducing a new infrastructure dependency such as Redis.
    """

    DEFAULT_STATE = {
        "position_sync": None,
        "circuit_breakers": {},
        "recent_orders": [],
        "recent_fills": [],
        "recent_errors": [],
        "last_data_timestamp": None,
        "last_broker_ping": None,
        "account_metrics": {
            "account_id": None,
            "opening_balance": None,
            "closing_balance": None,
            "equity": None,
            "daily_pnl": 0.0,
            "mll_floor": None,
            "dll_floor": None,
            "consecutive_losses": 0,
            "active_risk_tier": None,
            "buffer_to_mll": None,
            "updated_at": None,
        },
        "last_processed_signal_ids": [],
        "last_emergency_action": None,
    }

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state = deepcopy(self.DEFAULT_STATE)
        self.load()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.save()
            return self._state

        try:
            data = json.loads(self.path.read_text())
            self._state = self._merge_defaults(data)
        except Exception:
            backup = self.path.with_suffix(".corrupt.json")
            try:
                self.path.replace(backup)
            except Exception:
                pass
            self._state = deepcopy(self.DEFAULT_STATE)
            self.save()
        return self._state

    def save(self):
        self.path.write_text(json.dumps(self._state, indent=2, sort_keys=True, default=str))

    def get_state(self) -> dict[str, Any]:
        return self._state

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any):
        self._state[key] = value
        self.save()

    def update(self, mapping: dict[str, Any]):
        self._state.update(mapping)
        self.save()

    def update_account_metrics(self, **kwargs):
        metrics = self._state.setdefault("account_metrics", {})
        metrics.update(kwargs)
        metrics["updated_at"] = datetime.utcnow().isoformat()
        self.save()

    def append_recent(self, key: str, value: Any, max_items: int = 100):
        items = self._state.setdefault(key, [])
        items.append(value)
        if len(items) > max_items:
            self._state[key] = items[-max_items:]
        self.save()

    def remember_signal(self, signal_id: str, max_items: int = 200):
        items = self._state.setdefault("last_processed_signal_ids", [])
        items.append(signal_id)
        if len(items) > max_items:
            self._state["last_processed_signal_ids"] = items[-max_items:]
        self.save()

    def has_seen_signal(self, signal_id: str) -> bool:
        return signal_id in self._state.get("last_processed_signal_ids", [])

    def _merge_defaults(self, incoming: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(self.DEFAULT_STATE)
        for key, value in incoming.items():
            if key == "account_metrics" and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged
