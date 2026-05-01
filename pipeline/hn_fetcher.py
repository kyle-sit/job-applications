#!/usr/bin/env python3
"""
Hacker News 'Who is Hiring' fetcher.

Finds the most recent 'Ask HN: Who is hiring?' thread (posted by 'whoishiring'
on the first business day of every month) and extracts top-level comments as
job postings.

Outputs in the same markdown format Indeed produces, so it plugs straight into
parse_and_score.py with no parser changes.

Usage:
    hn_fetcher.py <output_file>

Notes:
- Only top-level comments are considered (job postings, not replies).
- Comments are filtered to those mentioning a relevant role + a senior/tech
  signal — keeps signal high without sending thousands of comments through
  the scorer.
- HN's API is public and unauthenticated; no rate limit issues for one run.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

ALGOLIA = ("https://hn.algolia.com/api/v1/search?"
           "query=Ask+HN+Who+is+hiring&tags=story,author_whoishiring&hitsPerPage=1")
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
HN_COMMENT_URL = "https://news.ycombinator.com/item?id={id}"


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks = []

    def handle_data(self, data):
        self._chunks.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br"):
            self._chunks.append("\n")

    def text(self):
        return "".join(self._chunks)


def strip_html(html: str) -> str:
    p = _HTMLStripper()
    p.feed(html)
    return p.text()


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "kyle-job-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def find_latest_thread():
    data = get_json(ALGOLIA)
    if not data.get("hits"):
        return None
    hit = data["hits"][0]
    return {"id": int(hit["objectID"]), "title": hit["title"], "created_at": hit["created_at"]}


def is_relevant(text: str) -> bool:
    """Heuristic filter: keep comments that mention a software role AND something senior or tech-stack-relevant."""
    t = text.lower()
    if not any(k in t for k in ["software", "engineer", "developer", "swe", "sde"]):
        return False
    signals = ["senior", "staff", "principal", "lead", "remote",
               "react", "typescript", "java", "kotlin", "aws", "node",
               "fullstack", "full stack", "full-stack",
               "backend", "back-end", "back end",
               "frontend", "front-end", "front end"]
    return any(s in t for s in signals)


SEPARATORS = re.compile(r"\s*[|·•—–]\s*")
DOLLAR_RE = re.compile(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*([Kk])?")


def extract_compensation(text: str) -> str:
    """Pull a $ amount out of the first ~500 chars; normalize to 'a year'."""
    nums = []
    for m, k in DOLLAR_RE.findall(text[:600]):
        n = float(m.replace(",", ""))
        if k or n < 1000:
            n *= 1000
        nums.append(n)
    # Reasonable annual salary band
    nums = [n for n in nums if 50_000 <= n <= 1_000_000]
    if not nums:
        return "N/A"
    if len(nums) == 1:
        return f"${int(nums[0]):,} a year"
    return f"${int(min(nums)):,} - ${int(max(nums)):,} a year"


def comment_to_record(comment: dict) -> dict | None:
    text = strip_html(comment.get("text", ""))
    if not text or not is_relevant(text):
        return None

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return None

    first = lines[0]
    parts = SEPARATORS.split(first)
    parts = [p.strip() for p in parts if p.strip()]

    company = parts[0] if parts else "(unspecified)"
    location = parts[1] if len(parts) > 1 else ""
    role = parts[2] if len(parts) > 2 else ""

    first_lower = first.lower()
    if "remote" in first_lower:
        if "remote" not in location.lower():
            location = f"{location} / Remote".strip(" /") if location else "Remote"

    title = (role or first)[:120]

    job_type = "Full-time"
    if "part-time" in first_lower or "part time" in first_lower:
        job_type = "Part-time"
    elif "contract" in first_lower or "contractor" in first_lower:
        job_type = "Contract"
    elif "intern" in first_lower:
        job_type = "Internship"

    posted_dt = datetime.fromtimestamp(comment["time"])

    return {
        "title": title,
        "job_id": f"hn-{comment['id']}",
        "company": company[:80] or "(unspecified)",
        "location": location[:80] or "(unspecified)",
        "posted_on": posted_dt.strftime("%B %d, %Y"),
        "job_type": job_type,
        "compensation": extract_compensation(text),
        "url": HN_COMMENT_URL.format(id=comment["id"]),
    }


def render(r: dict) -> str:
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
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    out_path = Path(sys.argv[1])

    print("Finding latest 'Who is Hiring' thread...", file=sys.stderr)
    thread = find_latest_thread()
    if not thread:
        print("No 'Who is Hiring' thread found", file=sys.stderr)
        sys.exit(1)
    print(f"Thread: {thread['title']} (id={thread['id']}, posted {thread['created_at']})",
          file=sys.stderr)

    thread_data = get_json(HN_ITEM.format(id=thread["id"]))
    kids = thread_data.get("kids", []) or []
    print(f"Top-level comments: {len(kids)}", file=sys.stderr)

    records = []
    skipped = 0
    for i, cid in enumerate(kids):
        if i and i % 100 == 0:
            print(f"  ... {i}/{len(kids)} (kept={len(records)} skipped={skipped})", file=sys.stderr)
        try:
            c = get_json(HN_ITEM.format(id=cid))
            if not c or c.get("dead") or c.get("deleted"):
                skipped += 1
                continue
            rec = comment_to_record(c)
            if rec:
                records.append(rec)
            else:
                skipped += 1
        except Exception as e:
            print(f"  error fetching {cid}: {e}", file=sys.stderr)
            skipped += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(render(r) for r in records))
    print(f"kept={len(records)} skipped={skipped} → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
