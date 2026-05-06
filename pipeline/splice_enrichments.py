#!/usr/bin/env python3
"""
Splice Indeed get_job_details summaries into digest.md.

Reads digest.md, finds <!--ENRICH_INDEED:{hash}--> markers, and replaces each
with a blockquote summary from the enrichments JSON. Markers without a matching
entry are removed silently (so a partial/failed enrichment doesn't leave debris
in the digest).

Usage:
    splice_enrichments.py <digest_md> <enrichments_json>

enrichments_json schema:
    { "<hash>": "<summary text — already trimmed>", ... }
"""

import json
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r"<!--ENRICH_(?:INDEED|LINKEDIN|DICE):([\w-]+)-->")


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    digest_path = Path(sys.argv[1])
    enrich_path = Path(sys.argv[2])

    digest = digest_path.read_text()
    enrichments = json.loads(enrich_path.read_text()) if enrich_path.exists() else {}

    replaced = 0
    cleared = 0

    def sub(m):
        nonlocal replaced, cleared
        h = m.group(1)
        if h in enrichments:
            text = enrichments[h].strip()
            if text:
                replaced += 1
                return f"> {text}"
        cleared += 1
        return ""  # remove the marker if no enrichment was provided

    digest = MARKER_RE.sub(sub, digest)
    # Clean up extra blank lines left behind by removed markers
    digest = re.sub(r"\n{3,}", "\n\n", digest)

    digest_path.write_text(digest)
    print(f"spliced {replaced} enrichments, cleared {cleared} unfilled markers",
          file=sys.stderr)


if __name__ == "__main__":
    main()
