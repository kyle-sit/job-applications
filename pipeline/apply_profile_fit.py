#!/usr/bin/env python3
"""
Apply per-job profile-fit scores to a digest, re-rank, and re-render.

The mechanical scorer (`parse_and_score.py`) ranks jobs on title + structured
fields (salary/recency/location). After enrichment splices descriptions into
the Strong tier's blockquotes, an agent reads `profile.md` + each Strong
match's title + description and emits a fit score 0–5 per job. This script:

  1. Parses digest.md into job blocks (keyed by `<!--HASH:abc-->` markers)
  2. Reads fit_scores.json: { "<hash>": {"fit_score": 0–5, "fit_notes": "..."} }
  3. Adds fit_score to each job's existing total → new total
  4. Optionally appends a "_Profile fit: N/5 — notes_" line under each block
  5. Re-tiers using thresholds from <digest_dir>/scoring.json
  6. Re-renders digest.md preserving the original block content (summaries,
     links, HASH markers) — just moves and regroups by new total

Idempotent: re-running with a fresh fit_scores.json strips the old fit lines
before applying new ones.

Usage:
    apply_profile_fit.py <digest_md> <fit_scores_json> [--no-notes]
"""

import copy
import json
import re
import sys
from pathlib import Path

DEFAULT_TIERS = {"strong_min": 17, "worth_a_look_min": 12}

TIER_HEADERS = {
    "strong": "## 🟢 Strong Matches\n",
    "worth_a_look": "\n## 🟡 Worth a Look\n",
    "lower": "\n## ⚪ Lower Priority\n",
}
TIER_HEADER_RE = re.compile(r"^## (?:🟢|🟡|⚪) [^\n]+$", re.MULTILINE)
HASH_RE = re.compile(r"<!--HASH:([\w-]+)-->")
SCORE_LINE_RE = re.compile(r"`Score:\s*(-?\d+)`")
PROFILE_FIT_LINE_RE = re.compile(r"\n_Profile fit:[^\n]*_\n")


def load_tiers(digest_dir: Path) -> dict:
    cfg_path = digest_dir / "scoring.json"
    if not cfg_path.exists():
        return dict(DEFAULT_TIERS)
    cfg = json.loads(cfg_path.read_text())
    tiers = cfg.get("tiers") or {}
    return {
        "strong_min": tiers.get("strong_min", DEFAULT_TIERS["strong_min"]),
        "worth_a_look_min": tiers.get("worth_a_look_min", DEFAULT_TIERS["worth_a_look_min"]),
    }


def split_digest(digest_text: str) -> tuple[str, list[dict]]:
    """Return (header_text_before_first_tier, [parsed_job_blocks])."""
    headers = list(TIER_HEADER_RE.finditer(digest_text))
    if not headers:
        return digest_text, []

    header_text = digest_text[: headers[0].start()].rstrip() + "\n\n"
    body = digest_text[headers[0].start():]

    # Split each job block — starts with <!--HASH:...-->\n### , runs until the
    # next HASH marker or end of string. Use \Z (not $) so MULTILINE doesn't
    # cut blocks short at line boundaries.
    block_re = re.compile(
        r"(<!--HASH:[\w-]+-->\n### [\s\S]+?)(?=<!--HASH:|\Z)",
    )
    job_blocks = []
    for m in block_re.finditer(body):
        block_text = m.group(1).rstrip()
        if not block_text:
            continue
        h_match = HASH_RE.search(block_text)
        s_match = SCORE_LINE_RE.search(block_text)
        if not h_match or not s_match:
            continue
        job_blocks.append({
            "hash": h_match.group(1),
            "total": int(s_match.group(1)),
            "body": block_text,
        })
    return header_text, job_blocks


def apply_fit_to_block(block: dict, fit_score: int, fit_notes: str,
                       show_notes: bool) -> dict:
    new_total = block["total"] + fit_score
    body = block["body"]

    # Update visible Score: number
    body = SCORE_LINE_RE.sub(f"`Score: {new_total}`", body, count=1)
    # Strip any pre-existing fit line (idempotent re-runs)
    body = PROFILE_FIT_LINE_RE.sub("\n", body)

    if show_notes:
        notes_clean = (fit_notes or "").strip()
        notes_suffix = f" — {notes_clean}" if notes_clean else ""
        # Insert right after the score line. Don't require a trailing \n
        # (blocks get rstripped during parsing, so the score line may be at
        # end-of-block).
        body = re.sub(
            r"(`Score:[^\n]+`[^\n]*)",
            rf"\1\n\n_Profile fit: {fit_score}/5{notes_suffix}_",
            body,
            count=1,
        )

    return {**block, "total": new_total, "body": body}


def render(header_text: str, blocks: list[dict], thresholds: dict) -> str:
    strong_min = thresholds["strong_min"]
    worth_min = thresholds["worth_a_look_min"]

    high = [b for b in blocks if b["total"] >= strong_min]
    med = [b for b in blocks if worth_min <= b["total"] < strong_min]
    low = [b for b in blocks if b["total"] < worth_min]

    parts = [header_text]
    if high:
        parts.append(TIER_HEADERS["strong"])
        parts.append("\n".join(b["body"] for b in high))
        parts.append("\n")
    if med:
        parts.append(TIER_HEADERS["worth_a_look"])
        parts.append("\n".join(b["body"] for b in med))
        parts.append("\n")
    if low:
        parts.append(TIER_HEADERS["lower"])
        parts.append("\n".join(b["body"] for b in low))
        parts.append("\n")
    return "".join(parts)


def main():
    args = sys.argv[1:]
    show_notes = True
    if "--no-notes" in args:
        show_notes = False
        args.remove("--no-notes")
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    digest_path = Path(args[0])
    fit_path = Path(args[1])

    if not digest_path.exists():
        print(f"No digest at {digest_path}; nothing to re-rank.", file=sys.stderr)
        return
    if not fit_path.exists():
        print(f"No fit_scores file at {fit_path}; leaving digest unchanged.", file=sys.stderr)
        return

    digest_text = digest_path.read_text()
    fit_scores = json.loads(fit_path.read_text())
    thresholds = load_tiers(digest_path.parent)

    header_text, blocks = split_digest(digest_text)
    if not blocks:
        print("No job blocks parsed (missing <!--HASH:--> markers?). Re-run "
              "parse_and_score.py first.", file=sys.stderr)
        return

    applied = 0
    for i, block in enumerate(blocks):
        fit_entry = fit_scores.get(block["hash"])
        if not fit_entry:
            continue
        try:
            fs = int(fit_entry.get("fit_score", 0))
        except (TypeError, ValueError):
            continue
        if fs == 0:
            continue
        notes = fit_entry.get("fit_notes", "")
        blocks[i] = apply_fit_to_block(block, fs, notes, show_notes)
        applied += 1

    blocks.sort(key=lambda b: -b["total"])
    new_digest = render(header_text, blocks, thresholds)
    digest_path.write_text(new_digest)

    print(f"applied fit scores to {applied}/{len(blocks)} jobs; re-tiered with "
          f"strong>={thresholds['strong_min']}, "
          f"worth_a_look>={thresholds['worth_a_look_min']}.",
          file=sys.stderr)


if __name__ == "__main__":
    main()
