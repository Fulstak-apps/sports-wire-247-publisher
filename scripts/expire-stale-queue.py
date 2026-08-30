#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "queue"
MAX_AGE_HOURS = 48
now = datetime.now(timezone.utc)
changed = 0


def parse_source_date(source_date):
    if not source_date:
        return None
    # Narro RSS commonly supplies RFC 2822 dates such as:
    # Sat, 29 Aug 2026 19:15:00 GMT
    try:
        parsed = parsedate_to_datetime(source_date)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        raw = source_date.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        parsed = datetime.fromisoformat(f"{source_date}T00:00:00+00:00")
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


for path in QUEUE.glob("*.json"):
    try:
        item = json.loads(path.read_text())
    except Exception:
        continue
    if item.get("status") != "ready":
        continue
    if item.get("story_type") == "throwback":
        continue

    source_date = item.get("source_published_at") or item.get("source_post_date")
    published = parse_source_date(source_date)
    if not published:
        item["status"] = "paused"
        item["stale_reason"] = "Unparseable source publication date; held to prevent stale news from publishing."
        path.write_text(json.dumps(item, indent=2) + "\n")
        changed += 1
        continue

    age_hours = (now - published).total_seconds() / 3600
    if age_hours > MAX_AGE_HOURS or age_hours < -1:
        item["status"] = "paused"
        item["stale_reason"] = f"Source item outside the {MAX_AGE_HOURS}-hour current-news window."
        path.write_text(json.dumps(item, indent=2) + "\n")
        changed += 1

print(f"Stale queue cleanup: paused {changed} item(s).")
