#!/usr/bin/env python3
"""
Job Pipeline — Parse, dedupe, score, and rank job listings from multiple sources.

Input files: concatenated markdown blocks in the format Indeed/Dice/LinkedIn
parsers produce. Source is auto-detected from each `job_id` prefix:
  hn-       → Hacker News
  dice-     → Dice
  linkedin- → LinkedIn
  (no prefix) → Indeed

Output: a ranked digest as Markdown.

Scoring rules are tunable via a JSON config — looked up by default at
`<digest_dir>/config/scoring.json`. If missing, sensible defaults are used.
See DEFAULT_CONFIG below for the schema and ship a `config/scoring.json` to
override any subset.

Usage:
    parse_and_score.py <output_digest> <seen_jobs_json> <input_file_1> [input_file_2 ...]
"""

import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HOURS_PER_YEAR = 2080  # for hourly → annual conversion

# ---------- Default scoring config ----------
DEFAULT_CONFIG = {
    "salary_floor": 150_000,
    "tiers": {"strong_min": 17, "worth_a_look_min": 12},
    "title": {
        "senior_tokens": [],
        "senior_bonus": 3,
        "primary_role_substrings": ["software engineer", "software developer", " sde", " swe"],
        "primary_role_bonus": 3,
        "secondary_role_substrings": ["engineer", "developer"],
        "secondary_role_bonus": 1,
        # Merged former specialty_groups + tech_keywords. Each entry is matched
        # with word-boundary regex; +1 per match, no cap. Multi-word phrases
        # like "full stack" still match because the space inside is a word
        # boundary on each side.
        "keywords": [],
    },
    "salary": {
        "tiers": [
            {"min": 300_000, "score": 5},
            {"min": 250_000, "score": 4},
            {"min": 200_000, "score": 3},
            {"min": 175_000, "score": 2},
            {"min": 150_000, "score": 1},
        ]
    },
    "recency": {
        "tiers_days": [
            {"max_days": 7, "score": 5},
            {"max_days": 14, "score": 4},
            {"max_days": 30, "score": 3},
            {"max_days": 60, "score": 2},
            {"max_days": 90, "score": 1},
        ],
        "stale_after_days": 365,
        "stale_penalty": -2,
        # Hard recency cutoff per source (days). Jobs older than the listed value
        # for their source are dropped before scoring. Sources NOT listed have no
        # cutoff (e.g. LinkedIn, where posted_on is always the run date).
        "max_days_by_source": {
            "Indeed": 7,
            "Dice": 3,
        },
        # If a job has no parseable posted_on date, drop it (True) or keep it (False).
        "drop_if_no_date": False,
    },
    "location": {
        "preferred": [
            {"name": "Remote", "substrings": ["remote"], "score": 3},
            {"name": "SF Bay Area", "substrings": ["san francisco", "sf bay", "san mateo",
                "south san francisco", "foster city", "palo alto", "san carlos", "alameda"], "score": 3},
            {"name": "San Diego", "substrings": ["san diego", "poway"], "score": 2},
        ]
    },
}

# Module-level config — populated from JSON in main(), replaces hardcoded constants
CONFIG = copy.deepcopy(DEFAULT_CONFIG)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base recursively. Lists are replaced wholesale."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path: Path) -> dict:
    """Load scoring config from JSON, falling back to defaults for missing keys."""
    if not config_path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    user = json.loads(config_path.read_text())
    return _deep_merge(copy.deepcopy(DEFAULT_CONFIG), user)


# ---------- Parsing ----------
def parse_compensation(comp_str: str):
    """Return (low, high) annualized salary, or (None, None) if unparseable."""
    if not comp_str or comp_str.strip().upper() == "N/A":
        return None, None
    if comp_str.strip() in ("$0 a year", "$0"):
        return None, None
    nums = re.findall(r"\$?([\d,]+(?:\.\d+)?)", comp_str)
    if not nums:
        return None, None
    vals = [float(n.replace(",", "")) for n in nums]
    if "hour" in comp_str.lower():
        vals = [v * HOURS_PER_YEAR for v in vals]
    if len(vals) == 1:
        return vals[0], vals[0]
    return min(vals), max(vals)


def parse_date(date_str: str):
    try:
        return datetime.strptime(date_str.strip(), "%B %d, %Y")
    except (ValueError, AttributeError):
        return None


def get_job_hash(job_id: str) -> str:
    """The unique hash sits at the end of job_id, before any '---' suffix."""
    clean = job_id.split("---")[0]
    parts = clean.rsplit("-", 1)
    return parts[-1].strip() if parts else clean.strip()


JOB_BLOCK_RE = re.compile(r"\*\*Job Title:\*\*[\s\S]+?(?=\*\*Job Title:\*\*|\Z)")
FIELD_RE = lambda name: re.compile(rf"\*\*{name}:\*\*\s*(.+?)(?=\n\s*\*\*|\Z)", re.DOTALL)


def parse_jobs(text: str):
    jobs = []
    for block in JOB_BLOCK_RE.findall(text):
        def f(name):
            m = FIELD_RE(name).search(block)
            return m.group(1).strip() if m else ""
        title = f("Job Title")
        jid = f("Job Id")
        if not title or not jid:
            continue
        comp_str = f("Compensation")
        low, high = parse_compensation(comp_str)
        jobs.append({
            "title": title,
            "job_id": jid,
            "hash": get_job_hash(jid),
            "company": f("Company"),
            "location": f("Location"),
            "posted_on": f("Posted on"),
            "posted_dt": parse_date(f("Posted on")),
            "job_type": f("Job Type"),
            "compensation": comp_str,
            "comp_low": low,
            "comp_high": high,
            "url": f("View Job URL"),
            "summary": f("Summary"),
        })
    return jobs


def dedupe(jobs):
    """Dedupe by job hash. Prefer the entry with the richest comp string."""
    by_hash = {}
    for j in jobs:
        h = j["hash"]
        if h not in by_hash or len(j["compensation"]) > len(by_hash[h]["compensation"]):
            by_hash[h] = j
    return list(by_hash.values())


# ---------- Filtering ----------
def passes_salary_floor(job):
    """Pass if upper-bound is unknown OR upper-bound >= floor."""
    return job["comp_high"] is None or job["comp_high"] >= CONFIG["salary_floor"]


def passes_recency(job, today):
    """Pass if the job is within its source's max_days window. Sources without
    a configured cutoff always pass. Behavior on missing posted_dt is governed
    by recency.drop_if_no_date.
    """
    cfg = CONFIG.get("recency", {}) or {}
    max_by_source = cfg.get("max_days_by_source") or {}
    source = job.get("source", "Indeed")
    max_days = max_by_source.get(source)
    if max_days is None:
        return True  # no cutoff configured for this source
    if job.get("posted_dt") is None:
        return not cfg.get("drop_if_no_date", False)
    days = (today - job["posted_dt"]).days
    return days <= max_days


# ---------- Scoring ----------
def has_token(text: str, token: str) -> bool:
    return bool(re.search(rf"\b{token}\b", text, re.IGNORECASE))


def score_title(title: str):
    cfg = CONFIG["title"]
    notes = []
    score = 0
    t = title.lower()

    # Senior-level signal (e.g. Senior, Staff, Head of, II, III). Optional —
    # if a profile leaves senior_tokens empty, no senior bonus is applied.
    senior_tokens = cfg.get("senior_tokens", [])
    if senior_tokens and any(has_token(title, tok) for tok in senior_tokens):
        bonus = cfg.get("senior_bonus", 3)
        score += bonus
        notes.append(f"senior+{bonus}")

    # Role match — primary preferred, falls back to secondary.
    if any(s in t for s in cfg["primary_role_substrings"]):
        score += cfg["primary_role_bonus"]
        notes.append(f"role+{cfg['primary_role_bonus']}")
    elif any(s in t for s in cfg.get("secondary_role_substrings", [])):
        score += cfg.get("secondary_role_bonus", 0)
        notes.append(f"role+{cfg.get('secondary_role_bonus', 0)}")

    # Merged keywords (formerly tech_keywords + specialty_groups). Each entry
    # is word-boundary matched; +1 per match; no cap.
    keywords = cfg.get("keywords", [])
    matched = [kw for kw in keywords if has_token(title, kw)]
    if matched:
        score += len(matched)
        notes.append(f"kw+{len(matched)}")
    return score, notes


def score_salary(high):
    if high is None:
        return 0
    for tier in CONFIG["salary"]["tiers"]:
        if high >= tier["min"]:
            return tier["score"]
    return 0


def score_recency(posted_dt, today):
    if posted_dt is None:
        return 0
    days = (today - posted_dt).days
    if days < 0:
        # future-dated — treat as freshest
        return CONFIG["recency"]["tiers_days"][0]["score"]
    cfg = CONFIG["recency"]
    for tier in cfg["tiers_days"]:
        if days <= tier["max_days"]:
            return tier["score"]
    if days > cfg.get("stale_after_days", 365):
        return cfg.get("stale_penalty", -2)
    return 0


def score_location(location: str):
    l = location.lower()
    for pref in CONFIG["location"]["preferred"]:
        if any(s in l for s in pref["substrings"]):
            return pref["score"]
    return 0


def score_job(job, today):
    title_score, title_notes = score_title(job["title"])
    sal_score = score_salary(job["comp_high"])
    rec_score = score_recency(job["posted_dt"], today)
    loc_score = score_location(job["location"])
    return {
        "total": title_score + sal_score + rec_score + loc_score,
        "title": title_score,
        "title_notes": title_notes,
        "salary": sal_score,
        "recency": rec_score,
        "location": loc_score,
    }


# ---------- Rendering ----------
def fmt_comp(job):
    if job["comp_high"] is None:
        return "_comp N/A_"
    if job["comp_low"] == job["comp_high"]:
        return f"${int(job['comp_low']):,}"
    return f"${int(job['comp_low']):,} – ${int(job['comp_high']):,}"


def fmt_age(posted_dt, today):
    if not posted_dt:
        return "unknown date"
    days = (today - posted_dt).days
    if days < 0:
        return "future-dated"
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < 30:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{days // 30} months ago"
    return f"{days // 365} year(s) ago"


def render_job(j, new_hashes, today, include_summary=False):
    new_tag = " 🆕" if j["hash"] in new_hashes else ""
    s = j["score"]
    notes = ", ".join(s["title_notes"]) if s["title_notes"] else ""
    source = j.get("source", "Indeed")
    # Hidden hash marker — invisible in rendered Markdown but lets the
    # apply_profile_fit re-rank pass key each block back to its job hash.
    out = (
        f"<!--HASH:{j['hash']}-->\n"
        f"### [{j['title']}]({j['url']}){new_tag}\n"
        f"**{j['company']}** · {j['location']} · {fmt_comp(j)} · "
        f"_{source} · posted {fmt_age(j['posted_dt'], today)}_\n\n"
        f"`Score: {s['total']}` "
        f"(title {s['title']}{f' [{notes}]' if notes else ''}, "
        f"salary {s['salary']}, recency {s['recency']}, location {s['location']})\n"
    )

    summary = (j.get("summary") or "").strip()
    show_summary = include_summary or source == "LinkedIn"

    if show_summary:
        if summary:
            out += f"\n> {summary}\n"
        elif source == "Indeed":
            out += f"\n<!--ENRICH_INDEED:{j['hash']}-->\n"
        elif source == "LinkedIn":
            out += f"\n<!--ENRICH_LINKEDIN:{j['hash']}-->\n"
    return out


SOURCE_BY_PREFIX = [
    ("hn-", "HN"),
    ("dice-", "Dice"),
    ("linkedin-", "LinkedIn"),
]


def detect_source(job_id: str) -> str:
    for prefix, name in SOURCE_BY_PREFIX:
        if job_id.startswith(prefix):
            return name
    return "Indeed"


def main():
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    out_path = Path(sys.argv[1])
    seen_path = Path(sys.argv[2])
    input_paths = [Path(p) for p in sys.argv[3:]]

    # Load tunable scoring config (auto-detect at <digest_dir>/scoring.json,
    # which in the multi-profile layout is profiles/<name>/scoring.json next to
    # the profile's digest.md). For backward compat, also check the legacy
    # <digest_dir>/config/scoring.json location.
    global CONFIG
    candidates = [
        out_path.parent / "scoring.json",
        out_path.parent / "config" / "scoring.json",  # legacy single-profile layout
    ]
    config_path = next((p for p in candidates if p.exists()), candidates[0])
    CONFIG = load_config(config_path)

    raw_text = "\n\n".join(p.read_text() for p in input_paths if p.exists())
    today = datetime.now()

    all_jobs = parse_jobs(raw_text)
    for j in all_jobs:
        j["source"] = detect_source(j["job_id"])
    deduped = dedupe(all_jobs)
    after_salary = [j for j in deduped if passes_salary_floor(j)]
    filtered = [j for j in after_salary if passes_recency(j, today)]

    # Track seen jobs across runs
    seen_data = {}
    if seen_path and seen_path.exists():
        seen_data = json.loads(seen_path.read_text())
    new_hashes = set()
    today_str = today.strftime("%Y-%m-%d")
    for j in filtered:
        if j["hash"] not in seen_data:
            seen_data[j["hash"]] = today_str
            new_hashes.add(j["hash"])
    if seen_path:
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text(json.dumps(seen_data, indent=2))

    for j in filtered:
        j["score"] = score_job(j, today)

    filtered.sort(key=lambda j: (
        -j["score"]["total"],
        -(j["posted_dt"].timestamp() if j["posted_dt"] else 0),
    ))

    strong_min = CONFIG["tiers"]["strong_min"]
    worth_min = CONFIG["tiers"]["worth_a_look_min"]
    high = [j for j in filtered if j["score"]["total"] >= strong_min]
    med = [j for j in filtered if worth_min <= j["score"]["total"] < strong_min]
    low = [j for j in filtered if j["score"]["total"] < worth_min]

    src_counts = {}
    for j in filtered:
        src_counts[j.get("source", "Indeed")] = src_counts.get(j.get("source", "Indeed"), 0) + 1
    src_summary = ", ".join(f"{n} {s}" for s, n in sorted(src_counts.items()))

    # Pretty-print the per-source recency cutoff for the header (e.g. "Indeed≤7d, Dice≤3d").
    rec_caps = (CONFIG.get("recency", {}) or {}).get("max_days_by_source") or {}
    rec_summary = ", ".join(f"{src}≤{d}d" for src, d in sorted(rec_caps.items())) if rec_caps else "none"

    lines = [
        f"# Daily Job Digest — {today.strftime('%A, %B %d, %Y')}",
        "",
        f"_Sources: {src_summary or 'none'} · salary floor ${CONFIG['salary_floor']:,} · "
        f"recency: {rec_summary} · "
        f"{len(all_jobs)} raw → {len(deduped)} unique → {len(after_salary)} above salary floor "
        f"→ {len(filtered)} within recency · "
        f"**{len(new_hashes)} new since last run**_",
        "",
        "---",
        "",
    ]
    if high:
        lines.append("## 🟢 Strong Matches\n")
        lines += [render_job(j, new_hashes, today, include_summary=True) for j in high]
    if med:
        lines.append("\n## 🟡 Worth a Look\n")
        lines += [render_job(j, new_hashes, today) for j in med]
    if low:
        lines.append("\n## ⚪ Lower Priority\n")
        lines += [render_job(j, new_hashes, today) for j in low]
    if not filtered:
        lines.append("_No matches passed the filter today._\n")

    # Indeed strong matches that need a get_job_details enrichment
    needs_indeed = [
        {"hash": j["hash"], "job_id": j["job_id"], "title": j["title"],
         "company": j["company"], "url": j["url"]}
        for j in high
        if j.get("source", "Indeed") == "Indeed" and not (j.get("summary") or "").strip()
    ]
    (out_path.parent / "needs_enrichment.json").write_text(json.dumps(needs_indeed, indent=2))

    # LinkedIn jobs that didn't get a summary in pre-score Chrome enrichment
    needs_linkedin = [
        {"hash": j["hash"], "job_id": j["job_id"], "title": j["title"],
         "company": j["company"], "url": j["url"]}
        for j in filtered
        if j.get("source") == "LinkedIn" and not (j.get("summary") or "").strip()
    ]
    (out_path.parent / "needs_enrichment_linkedin.json").write_text(
        json.dumps(needs_linkedin, indent=2)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    print(f"raw={len(all_jobs)} unique={len(deduped)} after_salary={len(after_salary)} "
          f"kept={len(filtered)} new={len(new_hashes)}",
          file=sys.stderr)
    print(f"wrote: {out_path}", file=sys.stderr)
    if config_path.exists():
        print(f"using scoring config: {config_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
