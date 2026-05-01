#!/usr/bin/env python3
"""
LinkedIn job alert email parser.

Reads a text file containing concatenated plaintext bodies from LinkedIn
job-alert emails (one email after another, separator irrelevant — the parser
finds job blocks by structure), extracts unique job listings, and writes them
in the markdown format parse_and_score.py expects.

LinkedIn alert email format (plaintext, after `View job:` per listing):

    Your job alert for <role> in <location>
    New jobs match your preferences.

    <Job Title>
    <Company>
    <Location>

    <optional badge — e.g. "1 connection", "This company is actively hiring">
    View job: https://www.linkedin.com/comm/jobs/view/<id>/?...

    ---------------------------------------------------------

    <next job ...>

The job_id `<id>` from the URL is unique to LinkedIn — we prefix it with
`linkedin-` so the parser/scorer auto-detects the source.

Usage:
    linkedin_parser.py <input_text> <output_md> [posted_iso_date]
"""

import re
import sys
from datetime import datetime
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s]+")
JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")
SEPARATOR_RE = re.compile(r"^-{20,}\s*$", re.MULTILINE)


def is_skippable_meta_line(line: str) -> bool:
    """Lines between location and View job that aren't part of the job's identity."""
    l = line.strip().lower()
    if not l:
        return True
    if l.startswith("this company is actively hiring"):
        return True
    if "alumni" in l or "alum" in l:
        return True
    if re.match(r"^\d+\s+(connections?|school|company)", l):
        return True
    if l.startswith("promoted") or l.startswith("active"):
        return True
    if l.startswith("easy apply"):
        return True
    return False


def parse_block(block: str, posted_str: str):
    """Extract a single job record from a block, or None if not a job block."""
    # Normalize line endings
    block = block.replace("\r\n", "\n").replace("\r", "\n")
    lines = block.split("\n")

    # Find the View job: line
    view_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("View job:"):
            view_idx = i
            break
    if view_idx is None:
        return None

    url_match = URL_RE.search(lines[view_idx])
    if not url_match:
        return None
    url = url_match.group(0).rstrip(",.;")

    id_match = JOB_ID_RE.search(url)
    if not id_match:
        return None
    job_id = id_match.group(1)

    # Walk backward from view_idx, collect the last 3 non-skippable, non-empty lines
    # — these are location, company, title (in reverse order).
    fields = []
    for i in range(view_idx - 1, -1, -1):
        line = lines[i].strip()
        if not line or is_skippable_meta_line(line):
            continue
        # Skip header lines that might appear at the start of the email
        if line.lower().startswith("your job alert") or line.lower().startswith("new jobs match"):
            break
        fields.insert(0, line)
        if len(fields) >= 3:
            break

    if len(fields) < 3:
        return None

    title, company, location = fields[0], fields[1], fields[2]

    return {
        "title": title,
        "job_id": f"linkedin-{job_id}",
        "company": company,
        "location": location,
        "posted_on": posted_str,
        "job_type": "Full-time",
        "compensation": "N/A",
        "url": url,
    }


def render(r):
    return (
        f"**Job Title:** {r['title']}\n"
        f"            **Job Id:** {r['job_id']}\n"
        f"            **Company:** {r['company']}\n"
        f"            **Location:** {r['location']}\n"
        f"            **Posted on:** {r['posted_on']}\n"
        f"            **Job Type:** {r['job_type']}\n"
        f"            **Compensation:** {r['compensation']}\n"
        f"            **View Job URL:** {r['url']}\n"
    )


def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    posted_str = sys.argv[3] if len(sys.argv) > 3 else datetime.now().strftime("%B %d, %Y")

    text = in_path.read_text()
    blocks = SEPARATOR_RE.split(text)

    records = []
    seen = set()
    skipped = 0
    for block in blocks:
        rec = parse_block(block, posted_str)
        if not rec:
            skipped += 1
            continue
        if rec["job_id"] in seen:
            continue
        seen.add(rec["job_id"])
        records.append(rec)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(render(r) for r in records))

    # Also emit a sidecar JSON list of jobs for the daily-run agent's pre-score
    # Chrome enrichment pass. Hash is the LinkedIn job number (no prefix).
    import json as _json
    sidecar_path = out_path.with_suffix(".jobs.json")
    sidecar = [
        {
            "hash": r["job_id"].removeprefix("linkedin-"),
            "job_id": r["job_id"],
            "title": r["title"],
            "company": r["company"],
            "url": r["url"],
        }
        for r in records
    ]
    sidecar_path.write_text(_json.dumps(sidecar, indent=2))

    print(f"parsed {len(records)} unique LinkedIn jobs (skipped {skipped} non-job blocks) → {out_path}",
          file=sys.stderr)
    print(f"sidecar list → {sidecar_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
