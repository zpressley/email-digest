"""
Daily JSON snapshot store.
Persists league/roster state for week-over-week trend tracking.
"""
import json
import os
from datetime import date, timedelta
from src.config import SNAPSHOT_DIR


def save_snapshot(data: dict, snapshot_date: date = None):
    if snapshot_date is None:
        snapshot_date = date.today()
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = _path_for(snapshot_date)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Snapshot saved: {path}")


def load_snapshot(snapshot_date: date = None) -> dict:
    if snapshot_date is None:
        snapshot_date = date.today()
    path = _path_for(snapshot_date)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def load_latest_snapshot() -> dict:
    for days_back in range(1, 8):
        d = date.today() - timedelta(days=days_back)
        snap = load_snapshot(d)
        if snap:
            return snap
    return {}


def diff_snapshots(current: dict, previous: dict, key: str) -> dict:
    curr_val = current.get(key, {})
    prev_val = previous.get(key, {})
    delta = {}
    for cat, val in curr_val.items():
        prev = prev_val.get(cat)
        delta[cat] = {
            "current": val,
            "previous": prev,
            "change": (val - prev)
            if (prev is not None and isinstance(val, (int, float))) else None
        }
    return delta


def _path_for(d: date) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{d.isoformat()}.json")
