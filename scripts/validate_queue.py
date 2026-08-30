#!/usr/bin/env python3
import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

def fail(message):
    print(f"ERROR: {message}")
    return 1


def main():
    if len(sys.argv) != 2:
        print("usage: validate_queue.py QUEUE_ITEM.json")
        return 2

    item_path = Path(sys.argv[1]).resolve()
    root = item_path.parents[1]
    item = json.loads(item_path.read_text())
    errors = 0

    required = [
        "status", "brand", "league", "story_type", "confirmation_status",
        "headline", "body", "slides", "story", "caption", "threads_text",
        "source_urls", "verification_source_count", "visual_asset_rights",
        "source_image_url", "source_image_role",
    ]
    for field in required:
        if field not in item or item[field] in ("", [], None):
            errors += fail(f"missing required field {field}")

    if item.get("status") != "ready":
        errors += fail("status must be ready")
    if item.get("brand") != "Sports Wire 24/7":
        errors += fail("brand must be Sports Wire 24/7")
    if item.get("verification_source_count", 0) < 2 or len(item.get("source_urls", [])) < 2:
        errors += fail("at least two verification sources are required")
    if item.get("source_photo_used") is not True:
        errors += fail("source_photo_used must be true; generic or invented visual scenes are not permitted")

    text_blob = json.dumps(item).casefold()
    if "automated" in text_blob:
        errors += fail("the word automated is not permitted in Sports Wire 24/7 editorial assets")

    for handle_field in (
        "person_instagram_handle", "lead_source_instagram_handle", "reporting_source_instagram_handle",
    ):
        handle = item.get(handle_field)
        if handle and not handle.startswith("@"):
            errors += fail(f"{handle_field} must begin with @")

    for url_field in ("source_urls", "visual_asset_source_urls"):
        for url in item.get(url_field, []):
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                errors += fail(f"invalid HTTPS URL in {url_field}: {url}")

    slides = item.get("slides", [])
    if len(slides) not in {1, 2, 3}:
        errors += fail("feed post must contain one, two, or three slides")
    for relative in slides:
        path = root / relative
        if not path.exists():
            errors += fail(f"missing slide {relative}")
            continue
        with Image.open(path) as image:
            if image.size != (1080, 1350):
                errors += fail(f"{relative} must be 1080x1350, found {image.size}")

    story_path = root / item.get("story", "")
    if not story_path.exists():
        errors += fail(f"missing Story asset {item.get('story')}")
    else:
        with Image.open(story_path) as image:
            if image.size != (1080, 1920):
                errors += fail(f"{item.get('story')} must be 1080x1920, found {image.size}")

    if len(item.get("threads_text", "")) > 500:
        errors += fail("threads_text exceeds 500 characters")
    if item.get("visual_asset_rights") not in {
        "owned", "licensed", "press_use", "reuse_permitted", "source_post_repost",
        "CC BY 3.0", "CC BY 2.0", "CC BY-SA 4.0",
    }:
        errors += fail("visual_asset_rights is not an approved rights basis")

    if errors:
        return 1
    print(f"VALID: {item_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
