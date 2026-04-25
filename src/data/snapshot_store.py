"""
Daily JSON snapshot store.
Persists league/roster state for week-over-week trend tracking.
"""
import json
import os
from datetime import date
from src.config import SNAPSHOT_DIR


def save_snapshot(data: dict, snapshot_date: date = None):
    if snapshot_date is None:
        snapshot_date = date.today()
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = _path_for(snapshot_date)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Snapshot saved: {path}")


def _path_for(d: date) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{d.isoformat()}.json")
