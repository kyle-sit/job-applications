#!/usr/bin/env python3
"""
Apply Chrome-extracted enrichments to the LinkedIn-normalized markdown file.

Updates per LinkedIn job (matched by linkedin-{hash} job id):
  - Compensation: replaces "N/A" with the extracted salary range
  - Summary: inserts a new field after Compensation (or replaces if already present)

Input enrichments JSON schema:
    {
      "<hash>": {
        "compensation": "$466,000 - $750,000 a year",   // optional
        "summary": "Builds infrastructure for distributed training..."  // optional
      },
      ...
    }

Usage:
    enrich_linkedin_md.py <linkedin_md_path> <enrichments_json>
"""

import json
import re
import sys
from pathlib import Path

JOB_ID_RE = re.compile(r"\*\*Job Id:\*\*\s*linkedin-(\S+)")
COMP_RE = re.compile(r"(\*\*Compensation:\*\*)\s*[^\n]*")
SUMMARY_RE = re.compile(r"(\*\*Summary:\*\*)\s*[^\n]*")
COMP_LINE_FOR_INSERT = re.compile(r"(            \*\*Compensation:\*\*[^\n]*\n)")


def update_block(block: str, enrichments: dict) -> str:
    m = JOB_ID_RE.search(block)
    if not m:
        return block
    hash_id = m.group(1)
    info = enrichments.get(hash_id)
    if not info:
        return block

    if "compensation" in info and info["compensation"]:
        block = COMP_RE.sub(rf"\1 {info['compensation']}", block, count=1)

    if "summary" in info and info["summary"]:
        # Sanitize: collapse newlines so the field stays one line
        summary = re.sub(r"\s+", " ", info["summary"]).strip()
        if SUMMARY_RE.search(block):
            block = SUMMARY_RE.sub(rf"\1 {summary}", block, count=1)
        else:
            block = COMP_LINE_FOR_INSERT.sub(
                rf"\1            **Summary:** {summary}\n", block, count=1
            )
    return block


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    md_path = Path(sys.argv[1])
    enrich_path = Path(sys.argv[2])

    if not md_path.exists():
        print(f"No markdown file at {md_path}; skipping enrichment", file=sys.stderr)
        return
    if not enrich_path.exists():
        print(f"No enrichments file at {enrich_path}; skipping", file=sys.stderr)
        return

    text = md_path.read_text()
    enrichments = json.loads(enrich_path.read_text())

    # Each block starts with "**Job Title:**"
    blocks = re.split(r"(?=\*\*Job Title:\*\*)", text)
    updated = [update_block(b, enrichments) for b in blocks]
    md_path.write_text("".join(updated))

    applied = sum(
        1 for b in updated
        if (m := JOB_ID_RE.search(b)) and m.group(1) in enrichments
    )
    print(f"Applied {applied} enrichments to {md_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
