#!/usr/bin/env python3
"""
Normalize Dice JSON responses into the markdown format the parser expects.

The Dice MCP returns JSON; the existing parse_and_score.py expects Indeed-style
markdown blocks. This script bridges them.

Input: a JSON file containing either:
  - a single Dice response object (with `data: [...]`), OR
  - a list of Dice response objects (one per search)

Output: a file with concatenated Indeed-style markdown blocks. Each Job Id
is prefixed with `dice-` so the parser tags the source correctly.

Usage:
    dice_normalizer.py <input_json> <output_md>
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path


def parse_dice_salary(salary: str) -> str:
    """Normalize Dice salary strings into the Indeed format the parser understands.

    Dice formats seen:
      - "$300000 - $400000 per annum"
      - "USD 160,000.00 - 200,000.00 per year"
      - "$120k - $180k"
      - "Depends on Experience"
      - ""
    """
    if not salary or not salary.strip():
        return "N/A"
    if "depends" in salary.lower() or "negotiable" in salary.lower():
        return "N/A"
    # Strip "USD" / "per annum" / "per year" / "annually"
    s = re.sub(r"\b(USD|per annum|per year|annually|a year)\b", "", salary, flags=re.IGNORECASE)
    # Find numeric values, supporting "150k", "150,000", "160,000.00"
    nums = []
    for m in re.finditer(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?", s):
        n = float(m.group(1).replace(",", ""))
        if m.group(2) or n < 1000:
            n *= 1000
        nums.append(n)
    nums = [n for n in nums if 30_000 <= n <= 2_000_000]
    if not nums:
        return "N/A"
    if len(nums) == 1:
        return f"${int(nums[0]):,} a year"
    return f"${int(min(nums)):,} - ${int(max(nums)):,} a year"


def parse_dice_date(d: str) -> str:
    """Convert Dice ISO timestamp to the 'Month DD, YYYY' format the parser expects."""
    if not d:
        return ""
    try:
        # Dice uses '2026-04-30T16:14:24Z' or with offsets
        d = d.replace("Z", "+00:00")
        dt = datetime.fromisoformat(d)
        return dt.strftime("%B %d, %Y")
    except ValueError:
        return ""


def clean_summary(s: str, max_chars: int = 350) -> str:
    """Tidy a Dice summary blob for inclusion in markdown."""
    if not s:
        return ""
    # Collapse repeated whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Truncate at sentence boundary if possible
    if len(s) > max_chars:
        cut = s[:max_chars]
        last_dot = cut.rfind(". ")
        if last_dot > max_chars * 0.6:  # only use sentence boundary if it's reasonably late
            cut = cut[: last_dot + 1]
        s = cut.rstrip() + "…"
    return s


def normalize_record(item: dict) -> dict:
    """Map a Dice job item to the parser's expected fields."""
    location = (item.get("jobLocation") or {}).get("displayName", "")
    if item.get("isRemote"):
        if "remote" not in location.lower():
            location = f"{location} (Remote)" if location else "Remote"
    title = item.get("title", "").strip()
    guid = item.get("guid") or item.get("id") or ""
    return {
        "title": title,
        "job_id": f"dice-{guid}",
        "company": (item.get("companyName") or "").strip() or "(unspecified)",
        "location": location,
        "posted_on": parse_dice_date(item.get("postedDate", "")),
        "job_type": item.get("employmentType") or "Full-time",
        "compensation": parse_dice_salary(item.get("salary", "")),
        "url": item.get("detailsPageUrl", ""),
        "summary": clean_summary(item.get("summary", "")),
    }


def render(r: dict) -> str:
    summary_line = f"            **Summary:** {r['summary']}\n" if r.get("summary") else ""
    return (
        f"**Job Title:** {r['title']}\n"
        f"            **Job Id:** {r['job_id']}\n"
        f"            **Company:** {r['company']}\n"
        f"            **Location:** {r['location']}\n"
        f"            **Posted on:** {r['posted_on']}\n"
        f"            **Job Type:** {r['job_type']}\n"
        f"            **Compensation:** {r['compensation']}\n"
        f"{summary_line}"
        f"            **View Job URL:** {r['url']}\n"
    )


def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    payload = json.loads(in_path.read_text())
    # Accept either a single response or a list of responses
    if isinstance(payload, dict):
        responses = [payload]
    else:
        responses = payload

    items = []
    for r in responses:
        for it in r.get("data") or []:
            items.append(it)

    records = [normalize_record(it) for it in items]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(render(r) for r in records))
    print(f"normalized {len(records)} Dice records → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
