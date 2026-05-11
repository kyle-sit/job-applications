#!/usr/bin/env python3
"""
Update the per-profile enrichment cache with fresh summaries.

Reads one or more `{hash: summary_text}` JSON files (the outputs of Steps
3f and 3g — Indeed and LinkedIn post-score enrichments) and merges them
into `enrichment_cache.json` under the profile's `data/` directory.

Each cache entry stores the summary, source label (inferred from filename),
and the date it was cached. If a hash is already present, the entry is
refreshed (newer summaries win and the date is bumped).

Usage:
    update_enrichment_cache.py <cache_path> <enrichments_json> [<enrichments_json> ...]

Filenames hinting at source:
    *_indeed_*.json   → source="Indeed"
    *_linkedin_*.json → source="LinkedIn"
    (anything else)   → source="Unknown"

Exit code is always 0 unless the cache path's parent doesn't exist or a
JSON file is malformed. Missing input files are skipped silently — that
matches the splice step's permissive behavior so a failed enrichment
sub-step doesn't block cache updates from the other source.
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def infer_source(name: str) -> str:
    low = name.lower()
    if "indeed" in low:
        return "Indeed"
    if "linkedin" in low:
        return "LinkedIn"
    return "Unknown"


def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    cache_path = Path(sys.argv[1])
    input_paths = [Path(p) for p in sys.argv[2:]]

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            # Don't blow away a corrupted cache silently — back it up and start fresh.
            backup = cache_path.with_suffix(".corrupt.json")
            backup.write_text(cache_path.read_text())
            print(f"warning: {cache_path} was corrupt; backed up to {backup}",
                  file=sys.stderr)
            cache = {}

    today_str = datetime.now().strftime("%Y-%m-%d")
    added = 0
    refreshed = 0
    skipped_empty = 0

    for p in input_paths:
        if not p.exists():
            continue
        source = infer_source(p.name)
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            print(f"warning: skipping malformed {p}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            print(f"warning: skipping {p} — top-level value is not an object",
                  file=sys.stderr)
            continue
        for h, summary in data.items():
            text = (summary or "").strip() if isinstance(summary, str) else ""
            if not text:
                skipped_empty += 1
                continue
            if h in cache:
                refreshed += 1
            else:
                added += 1
            cache[h] = {
                "summary": text,
                "source": source,
                "date_cached": today_str,
            }

    cache_path.write_text(json.dumps(cache, indent=2))
    total = len(cache)
    print(f"cache updated: +{added} new, {refreshed} refreshed, "
          f"{skipped_empty} skipped (empty); total entries: {total}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
