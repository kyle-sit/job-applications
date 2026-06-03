#!/usr/bin/env python3
"""
Job Pipeline — Parse, dedupe, score, and rank job listings from multiple sources.

Input files: concatenated markdown blocks in the format Indeed/LinkedIn
parsers produce. Source is auto-detected from each `job_id` prefix:
  hn-       → Hacker News
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
from datetime import datetime, timedelta
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
        ],
        # Fallback salary score for sources that routinely don't publish a
        # salary (e.g. LinkedIn job-alert listings), so a missing salary scores
        # a neutral value instead of 0 and doesn't bury otherwise-good jobs.
        # Sources not listed keep the default 0 for an unknown salary.
        "unknown_fallback_by_source": {"LinkedIn": 4},
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
    # Match each dollar amount plus an optional magnitude suffix (K=thousand,
    # M=million). LinkedIn enrichment emits salaries like "$194.4K"; without
    # suffix handling those parse as 194.4 and silently fail the salary floor.
    # The (?![A-Za-z]) guard stops a following word (e.g. "$120,000 Kaiser")
    # from being misread as a "K" multiplier.
    matches = re.findall(r"\$?([\d,]+(?:\.\d+)?)\s*([KkMm])?(?![A-Za-z])", comp_str)
    vals = []
    for num, suffix in matches:
        cleaned = num.replace(",", "")
        if not cleaned or not any(c.isdigit() for c in cleaned):
            continue
        v = float(cleaned)
        if suffix in ("K", "k"):
            v *= 1_000
        elif suffix in ("M", "m"):
            v *= 1_000_000
        vals.append(v)
    if not vals:
        return None, None
    if "hour" in comp_str.lower():
        vals = [v * HOURS_PER_YEAR for v in vals]
    if len(vals) == 1:
        return vals[0], vals[0]
    return min(vals), max(vals)


def parse_date(date_str: str):
    if not date_str:
        return None
    s = date_str.strip()
    # Accept the long-form Indeed format and the ISO form LinkedIn writes into
    # "Posted on" (the run date, e.g. "2026-06-02"). Without the ISO format the
    # LinkedIn run date was unparseable, leaving recency at 0 for every job.
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, AttributeError):
            continue
    return None


# LinkedIn job-alert pages expose no machine-readable post date — the only
# signal is a relative phrase like "posted 2 days ago" / "reposted 3 weeks ago"
# that the Chrome enrichment captures into the Summary line. Convert it to an
# absolute date (relative to the run date) so recency scoring reflects the real
# posting age. Returns None when no phrase is present, so the caller can fall
# back to the run date.
RELATIVE_AGE_RE = re.compile(
    r"(?:re)?posted\s+(\d+)\s*\+?\s*(minute|min|hour|day|week|month)s?\s+ago",
    re.IGNORECASE,
)


def relative_age_to_date(text: str, ref_date):
    if not text or ref_date is None:
        return None
    m = RELATIVE_AGE_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("minute", "min", "hour"):
        days = 0
    elif unit == "day":
        days = n
    elif unit == "week":
        days = n * 7
    else:  # month
        days = n * 30
    return ref_date - timedelta(days=days)


def get_job_hash(job_id: str) -> str:
    """The unique hash sits at the end of job_id, before any '---' suffix."""
    clean = job_id.split("---")[0]
    parts = clean.rsplit("-", 1)
    return parts[-1].strip() if parts else clean.strip()


JOB_BLOCK_RE = re.compile(r"\*\*Job Title:\*\*[\s\S]+?(?=\*\*Job Title:\*\*|\Z)")
# Match the value of a single-line field. `[ \t]*` after the label only consumes
# space/tab (never newlines), so an empty field returns "" instead of greedily
# slurping the next field's content via DOTALL.
FIELD_RE = lambda name: re.compile(rf"\*\*{name}:\*\*[ \t]*([^\n]*)")


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
    # When the source didn't publish a salary (comp_high is None → score 0),
    # apply a per-source neutral fallback so jobs from salary-sparse sources
    # like LinkedIn aren't buried purely for lacking a figure.
    if job["comp_high"] is None:
        fallback = (CONFIG.get("salary", {}).get("unknown_fallback_by_source") or {})
        sal_score = fallback.get(job.get("source", ""), sal_score)
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
        # Recency for LinkedIn: prefer the real posting age the enrichment
        # captured in the summary ("posted N days ago"); otherwise fall back to
        # the run date already parsed from "Posted on". This guarantees every
        # LinkedIn job earns a recency score instead of a silent 0.
        if j["source"] == "LinkedIn":
            real_dt = relative_age_to_date(j.get("summary", ""), j.get("posted_dt") or today)
            if real_dt is not None:
                j["posted_dt"] = real_dt
            elif j.get("posted_dt") is None:
                j["posted_dt"] = today
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

    # Pretty-print the per-source recency cutoff for the header (e.g. "Indeed≤7d").
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

    # ---- Enrichment cache: skip API calls for hashes we've already enriched.
    # Cache lives at $PROFILE_DIR/data/enrichment_cache.json alongside seen_jobs.json.
    # Schema: { "<hash>": {"summary": "...", "source": "Indeed"|"LinkedIn",
    #                       "date_cached": "YYYY-MM-DD"} }
    # If seen_path was passed, use its parent (data/) as the cache directory.
    cache_path = (seen_path.parent / "enrichment_cache.json") if seen_path else None
    cache_data = {}
    if cache_path and cache_path.exists():
        try:
            cache_data = json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            cache_data = {}  # corrupt cache → start fresh; not fatal

    def cached_summary(h: str) -> str:
        entry = cache_data.get(h)
        if isinstance(entry, dict):
            return (entry.get("summary") or "").strip()
        return ""

    # Indeed strong matches that need a get_job_details enrichment — but skip
    # hashes already in the cache.
    needs_indeed = [
        {"hash": j["hash"], "job_id": j["job_id"], "title": j["title"],
         "company": j["company"], "url": j["url"]}
        for j in high
        if j.get("source", "Indeed") == "Indeed"
           and not (j.get("summary") or "").strip()
           and not cached_summary(j["hash"])
    ]
    (out_path.parent / "needs_enrichment.json").write_text(json.dumps(needs_indeed, indent=2))

    # LinkedIn jobs that didn't get a summary in pre-score Chrome enrichment —
    # also skip hashes already in the cache.
    needs_linkedin = [
        {"hash": j["hash"], "job_id": j["job_id"], "title": j["title"],
         "company": j["company"], "url": j["url"]}
        for j in filtered
        if j.get("source") == "LinkedIn"
           and not (j.get("summary") or "").strip()
           and not cached_summary(j["hash"])
    ]
    (out_path.parent / "needs_enrichment_linkedin.json").write_text(
        json.dumps(needs_linkedin, indent=2)
    )

    # Pre-seed today's enrichments file with cached summaries for any Strong-tier
    # Indeed jobs (or LinkedIn fallback jobs) whose hashes ARE cached. Step 3h
    # merges this with the fresh-fetch enrichments before splicing.
    cached_today = {}
    for j in high:
        if j.get("source", "Indeed") == "Indeed" and not (j.get("summary") or "").strip():
            s = cached_summary(j["hash"])
            if s:
                cached_today[j["hash"]] = s
    for j in filtered:
        if j.get("source") == "LinkedIn" and not (j.get("summary") or "").strip():
            s = cached_summary(j["hash"])
            if s:
                cached_today[j["hash"]] = s
    # Sidecar lives next to the cache itself (in data/) so consumers can find
    # it predictably alongside seen_jobs.json and enrichment_cache.json.
    if seen_path:
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        (seen_path.parent / f"cached_enrichments_{today_str}.json").write_text(
            json.dumps(cached_today, indent=2)
        )
        print(f"cached_summaries_reused={len(cached_today)}", file=sys.stderr)

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
