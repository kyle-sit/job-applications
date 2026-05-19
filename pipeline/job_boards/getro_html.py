#!/usr/bin/env python3
"""
Getro-substrate job-board adapter.

Handles boards that share the Getro back-end but have different front-end
frameworks. All Getro-powered boards expose JSON-LD JobPosting on their
detail pages — that's the unified source of truth for descriptions, dates,
locations, salary, etc. Listing extraction varies by `listing_strategy`:

  - "next_data"  : Next.js sites that embed jobs in a __NEXT_DATA__ JSON
                   blob in the listing HTML.
                   Boards: Climate Draft, Elemental Impact.
  - "terra_html" : Qwik / custom-HTML sites. We pull detail-page URLs from
                   the listing HTML and rely on each detail page's
                   JSON-LD JobPosting for everything else.
                   Boards: Terra.do.

Public API: `fetch_board(board_config) -> list[JobRecord]`.
The board config dict comes from `profiles/<name>/job_boards.json` and is
shaped like:

    {
      "id": "climate_draft",
      "base_url": "https://jobs.climatedraft.org",
      "listing_path": "/jobs",
      "listing_strategy": "next_data"
    }

Stdlib only (urllib + re + json + html.parser) to match the rest of the
JobApps pipeline.

Ad-hoc verification (run from JobApps root):
    python3 -m pipeline.job_boards.getro_html              # all boards, enriched
    python3 -m pipeline.job_boards.getro_html --no-enrich  # listings only
    python3 -m pipeline.job_boards.getro_html --max 5      # cap per board
"""

from __future__ import annotations

import html as html_module
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.+?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_NEXTDATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class JobStub:
    """Minimal info pulled from a listing page (pre-enrichment)."""
    title: Optional[str]
    company: Optional[str]
    listing_url: str
    apply_url: Optional[str]
    date_posted: Optional[str]
    location_text: Optional[str]
    work_mode: Optional[str]


@dataclass
class JobRecord:
    """Full normalized record — stub merged with detail-page JSON-LD."""
    title: str
    company: Optional[str]
    description_html: Optional[str]
    description_text: Optional[str]
    date_posted: Optional[str]
    valid_through: Optional[str]
    employment_type: Optional[str]
    job_location_type: Optional[str]  # e.g. TELECOMMUTE
    location: Optional[str]
    company_url: Optional[str]
    company_description: Optional[str]
    salary_min: Optional[float]
    salary_max: Optional[float]
    salary_currency: Optional[str]
    apply_url: Optional[str]           # outbound ATS URL
    listing_url: str                   # canonical board URL
    board_id: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, errors="replace")


# ---------------------------------------------------------------------------
# Listing strategy: __NEXT_DATA__ (Climate Draft, Elemental Impact)
# ---------------------------------------------------------------------------

def _extract_next_data(html: str) -> Optional[dict]:
    m = _NEXTDATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _find_jobs_array(obj, depth: int = 0):
    """Walk a __NEXT_DATA__ tree for an array of job-shaped dicts."""
    if depth > 8:
        return None
    if (isinstance(obj, list) and obj and isinstance(obj[0], dict)
            and "title" in obj[0] and "organization" in obj[0]):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            got = _find_jobs_array(v, depth + 1)
            if got is not None:
                return got
    return None


def _unix_to_iso(ts) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _stubs_from_next_data(html: str, base_url: str) -> list[JobStub]:
    blob = _extract_next_data(html)
    if not blob:
        return []
    jobs = _find_jobs_array(blob) or []
    stubs = []
    for j in jobs:
        org = j.get("organization") or {}
        # organization may be a JSON-encoded string inside the blob
        if isinstance(org, str):
            try:
                org = json.loads(org)
            except json.JSONDecodeError:
                org = {}
        org_slug = (org or {}).get("slug") or ""
        job_slug = str(j.get("slug") or "")
        job_id = str(j.get("id") or "")
        # Detail-page URL on the board's own domain (NOT the outbound ATS).
        # On Climate Draft / Elemental Impact, the `slug` field already starts
        # with the job id, so don't double-prefix.
        slug_part = job_slug if job_slug.startswith(job_id) else f"{job_id}-{job_slug}".strip("-")
        listing_url = urljoin(
            base_url + "/", f"companies/{org_slug}/jobs/{slug_part}"
        )
        # locations may be a list of strings, or a JSON-encoded string
        locs = j.get("locations") or []
        if isinstance(locs, str):
            try:
                locs = json.loads(locs)
            except json.JSONDecodeError:
                locs = [locs]
        stubs.append(JobStub(
            title=j.get("title"),
            company=(org or {}).get("name") if isinstance(org, dict) else None,
            listing_url=listing_url,
            apply_url=j.get("url"),
            date_posted=_unix_to_iso(j.get("createdAt")),
            location_text=", ".join(locs) if locs else None,
            work_mode=j.get("workMode"),
        ))
    return stubs


# ---------------------------------------------------------------------------
# Listing strategy: Terra.do HTML
# ---------------------------------------------------------------------------

class _TerraListingParser(HTMLParser):
    """Extract detail-page URLs from Terra.do listing HTML.

    Looks for the pattern: <a id="jobtitlelink" href="..."><h4>title</h4></a>
    """

    def __init__(self):
        super().__init__()
        self._collecting_title = False
        self._current_href = None
        self._current_title_parts: list[str] = []
        self.jobs: list[tuple[str, str]] = []  # (href, title)

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "a" and attrs_d.get("id") == "jobtitlelink":
            self._current_href = attrs_d.get("href")
            self._current_title_parts = []
        elif tag == "h4" and self._current_href is not None:
            self._collecting_title = True

    def handle_endtag(self, tag):
        if tag == "h4" and self._collecting_title:
            self._collecting_title = False
        elif tag == "a" and self._current_href is not None:
            title = "".join(self._current_title_parts).strip()
            if title and self._current_href:
                self.jobs.append((self._current_href, title))
            self._current_href = None
            self._current_title_parts = []

    def handle_data(self, data):
        if self._collecting_title:
            self._current_title_parts.append(data)


def _stubs_from_terra_html(html: str, base_url: str) -> list[JobStub]:
    parser = _TerraListingParser()
    parser.feed(html)
    parser.close()
    seen = set()
    stubs = []
    for href, title in parser.jobs:
        url = urljoin(base_url + "/", href.lstrip("/").split("#")[0])
        if url in seen:
            continue
        seen.add(url)
        stubs.append(JobStub(
            title=title,
            company=None,            # detail page will populate
            listing_url=url,
            apply_url=None,
            date_posted=None,
            location_text=None,
            work_mode=None,
        ))
    return stubs


# ---------------------------------------------------------------------------
# Detail extraction: JSON-LD JobPosting (unified across all boards)
# ---------------------------------------------------------------------------

class _HTMLToText(HTMLParser):
    """Minimal HTML->text converter for description body."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        # collapse runs of blank lines, trim each line
        lines = [ln.strip() for ln in joined.splitlines()]
        out = []
        prev_blank = False
        for ln in lines:
            if ln:
                out.append(ln)
                prev_blank = False
            elif not prev_blank:
                out.append("")
                prev_blank = True
        return "\n".join(out).strip()


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    p = _HTMLToText()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return html
    return p.text()


def _parse_jsonld_jobposting(html: str) -> Optional[dict]:
    for m in _JSONLD_RE.finditer(html):
        body = m.group(1).strip()
        # Some pages HTML-escape the JSON-LD body
        if "&quot;" in body and '"' not in body:
            body = html_module.unescape(body)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for d in candidates:
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                return d
    return None


_WORK_MODE_MAP = {
    "remote": "TELECOMMUTE",
    "hybrid": "HYBRID",
    "onsite": "ONSITE",
    "on-site": "ONSITE",
    "on_site": "ONSITE",
    "in_office": "ONSITE",
    "in-office": "ONSITE",
}


def _work_mode_to_schema(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _WORK_MODE_MAP.get(value.lower().strip(), value)


def _normalize(stub: JobStub, detail: Optional[dict], board_id: str) -> JobRecord:
    detail = detail or {}
    desc_html = detail.get("description") or ""
    # Some boards (Terra.do) double-escape the description body
    if desc_html and "&lt;" in desc_html[:200] and "<" not in desc_html[:200]:
        desc_html = html_module.unescape(desc_html)
    desc_text = _html_to_text(desc_html) if desc_html else None

    org = detail.get("hiringOrganization") or {}
    if isinstance(org, list):
        org = org[0] if org else {}
    job_loc = detail.get("jobLocation") or {}
    if isinstance(job_loc, list):
        job_loc = job_loc[0] if job_loc else {}
    salary_outer = detail.get("baseSalary") or {}
    salary = salary_outer.get("value") or {}

    et = detail.get("employmentType")
    if isinstance(et, list):
        et_str = ", ".join(et) if et else None
    else:
        et_str = et

    return JobRecord(
        title=(detail.get("title") or stub.title) or "",
        company=(org.get("name") if isinstance(org, dict) else None) or stub.company,
        description_html=desc_html or None,
        description_text=desc_text,
        date_posted=detail.get("datePosted") or stub.date_posted,
        valid_through=detail.get("validThrough"),
        employment_type=et_str,
        job_location_type=detail.get("jobLocationType") or _work_mode_to_schema(stub.work_mode),
        location=(
            (job_loc.get("address") or {}).get("addressLocality")
            if isinstance(job_loc, dict) else None
        ) or stub.location_text,
        company_url=(org.get("url") if isinstance(org, dict) else None),
        company_description=(org.get("description") if isinstance(org, dict) else None),
        salary_min=salary.get("minValue") if isinstance(salary, dict) else None,
        salary_max=salary.get("maxValue") if isinstance(salary, dict) else None,
        salary_currency=salary_outer.get("currency"),
        # Fall back to the board's own detail page when no outbound ATS link
        # is available (Terra.do, some Climate Draft / EI rows).
        apply_url=stub.apply_url or stub.listing_url,
        listing_url=stub.listing_url,
        board_id=board_id,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_board(board: dict, *, enrich_details: bool = True,
                max_jobs: Optional[int] = None) -> list[JobRecord]:
    """
    Fetch jobs from a single Getro-substrate board.

    Args:
        board: dict, expected keys:
            id               required, e.g. "climate_draft"
            base_url         required, e.g. "https://jobs.climatedraft.org"
            listing_path     optional, default "/jobs"
            listing_strategy required: "next_data" | "terra_html"
        enrich_details: if True, fetch each detail page for JSON-LD JobPosting.
        max_jobs: cap on # of jobs returned (useful when testing).

    Returns:
        List of JobRecord. Always returns []; raises only on listing-fetch
        failure. Individual detail-fetch failures are silently skipped (the
        stub-level fields still appear in the record).
    """
    base = board["base_url"].rstrip("/")
    listing_path = board.get("listing_path", "/jobs")
    strategy = board["listing_strategy"]
    listing_url = urljoin(base + "/", listing_path.lstrip("/"))

    html = _http_get(listing_url)

    if strategy == "next_data":
        stubs = _stubs_from_next_data(html, base)
    elif strategy == "terra_html":
        stubs = _stubs_from_terra_html(html, base)
    else:
        raise ValueError(f"Unknown listing_strategy: {strategy!r}")

    if max_jobs is not None:
        stubs = stubs[:max_jobs]

    records = []
    for stub in stubs:
        detail = None
        if enrich_details:
            try:
                detail = _parse_jsonld_jobposting(_http_get(stub.listing_url))
            except urllib.error.URLError:
                detail = None
            except Exception:
                detail = None
        records.append(_normalize(stub, detail, board["id"]))
    return records


# ---------------------------------------------------------------------------
# CLI test runner
# ---------------------------------------------------------------------------

_TEST_BOARDS = [
    {"id": "climate_draft", "name": "Climate Draft",
     "base_url": "https://jobs.climatedraft.org", "listing_path": "/jobs",
     "listing_strategy": "next_data"},
    {"id": "elemental_impact", "name": "Elemental Impact",
     "base_url": "https://jobs.elementalimpact.com", "listing_path": "/jobs",
     "listing_strategy": "next_data"},
    {"id": "terra_do", "name": "Terra.do",
     "base_url": "https://www.terra.do",
     "listing_path": "/climate-jobs/job-board/",
     "listing_strategy": "terra_html"},
]


def _parse_args(argv):
    enrich = True
    max_jobs = 3
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--no-enrich":
            enrich = False
        elif a == "--max" and i + 1 < len(argv):
            max_jobs = int(argv[i + 1])
            i += 1
        i += 1
    return enrich, max_jobs


def _cli():
    enrich, max_jobs = _parse_args(sys.argv)
    for board in _TEST_BOARDS:
        print(f"\n=== {board['name']} ({board['id']}) ===")
        try:
            records = fetch_board(board, enrich_details=enrich, max_jobs=max_jobs)
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            continue
        print(f"  Fetched {len(records)} jobs (max {max_jobs}, enrich={enrich})")
        for r in records:
            print(f"\n  - {r.title}")
            print(f"      company       : {r.company}")
            print(f"      location      : {r.location}")
            print(f"      date_posted   : {r.date_posted}")
            print(f"      job_loc_type  : {r.job_location_type}")
            print(f"      employment    : {r.employment_type}")
            print(f"      salary        : {r.salary_min}-{r.salary_max} {r.salary_currency}")
            print(f"      apply_url     : {r.apply_url}")
            print(f"      listing_url   : {r.listing_url}")
            print(f"      desc length   : {len(r.description_text or '')} chars")
            if r.description_text:
                preview = r.description_text[:120].replace("\n", " ")
                print(f"      desc preview  : {preview!r}")


if __name__ == "__main__":
    _cli()
