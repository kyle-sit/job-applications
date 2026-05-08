#!/usr/bin/env python3
"""
Prepare a job-digest email payload for Gmail-MCP draft creation.

Reads the profile's `digest.md`, extracts the configured tiers, renders an HTML
email and plaintext alternative, and prints a JSON payload to stdout that the
scheduled task hands to the Gmail MCP `create_draft` tool.

Replaces send_digest_email.py — does NOT send via SMTP. The send is performed
by the scheduled task: create_draft via Gmail MCP, then click Send via the
Claude-in-Chrome MCP.

Usage:
    prepare_digest_email.py <profile_dir> <project_root>

Output (stdout, JSON):
    On a real send-worthy run:
      {
        "skip": false,
        "to": ["recipient@example.com"],
        "subject": "Job Digest — Wed May 06 (12 strong · 8 worth)",
        "htmlBody": "<html>…</html>",
        "body": "plaintext fallback…",
        "total": 20,
        "subject_marker": "Job Digest — Wed May 06"
      }
    On skip (disabled / no recipient / no matches):
      {"skip": true, "reason": "..."}

Exits 0 in both cases. Exits non-zero only on parse / IO errors.

Profile email.json schema (unchanged):
    {
      "enabled": true,
      "recipient_email": "owner@example.com",
      "tiers": ["strong", "worth_a_look"]
    }
"""

import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path


# ---------- Digest parsing ----------
TIER_HEADER_RE = re.compile(r"^## (?:🟢|🟡|⚪) (.+)$", re.MULTILINE)
TIER_NAME_TO_KEY = {
    "Strong Matches": "strong",
    "Worth a Look": "worth_a_look",
    "Lower Priority": "lower",
}
JOB_BLOCK_RE = re.compile(
    r"^### \[(?P<title>[^\]]+)\]\((?P<url>[^\)]+)\)(?P<new>[^\n]*)\n"
    r"(?P<meta>\*\*[^\n]+)\n\n"
    r"(?P<score>`Score:[^\n]+`[^\n]*)",
    re.MULTILINE,
)
PROFILE_FIT_IN_BLOCK_RE = re.compile(r"_Profile fit:\s*([^\n]+?)_")
SUMMARY_BLOCKQUOTE_RE = re.compile(r"^> (.+?)$", re.MULTILINE)


def split_into_tiers(digest_text: str) -> dict:
    tiers = {}
    matches = list(TIER_HEADER_RE.finditer(digest_text))
    for i, m in enumerate(matches):
        tier_label = m.group(1).strip()
        tier_key = TIER_NAME_TO_KEY.get(tier_label)
        if not tier_key:
            continue
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(digest_text)
        body = digest_text[body_start:body_end]
        blocks = re.split(r"(?=^### )", body, flags=re.MULTILINE)
        blocks = [b.strip() for b in blocks if b.strip()]
        tiers[tier_key] = blocks
    return tiers


def parse_block(block_text: str) -> dict | None:
    m = JOB_BLOCK_RE.search(block_text)
    if not m:
        return None
    fit_match = PROFILE_FIT_IN_BLOCK_RE.search(block_text)
    profile_fit = fit_match.group(1).strip() if fit_match else ""
    summary_match = SUMMARY_BLOCKQUOTE_RE.search(block_text)
    summary = summary_match.group(1).strip() if summary_match else ""
    return {
        "title": m.group("title").strip(),
        "url": m.group("url").strip(),
        "new": m.group("new").strip(),
        "meta": m.group("meta").strip(),
        "score": m.group("score").strip(),
        "summary": summary,
        "profile_fit": profile_fit,
    }


def extract_header(digest_text: str) -> tuple[str, str]:
    lines = digest_text.splitlines()
    title = lines[0].lstrip("# ").strip() if lines else "Daily Job Digest"
    summary = ""
    for line in lines[1:6]:
        s = line.strip()
        if s.startswith("_") and s.endswith("_"):
            summary = s.strip("_").strip()
            break
    return title, summary


# ---------- Rendering ----------
TIER_RENDER = {
    "strong": ("🟢 Strong Matches", "#1a8f3c"),
    "worth_a_look": ("🟡 Worth a Look", "#b88800"),
    "lower": ("⚪ Lower Priority", "#666"),
}


def render_html(profile_name, header_title, header_summary, tier_blocks, total,
                source_notice_html=""):
    css = (
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
        "color:#222;line-height:1.45;max-width:760px;margin:24px auto;padding:0 16px;}"
        "h1{margin:0 0 4px;font-size:22px;}"
        "h2{margin-top:32px;font-size:18px;border-bottom:1px solid #eee;padding-bottom:6px;}"
        ".sub{color:#666;font-size:13px;margin-bottom:24px;}"
        ".job{margin:18px 0;padding:12px 14px;border:1px solid #e3e3e3;border-radius:8px;background:#fafafa;}"
        ".job h3{margin:0 0 4px;font-size:16px;}"
        ".job h3 a{color:#0a58ca;text-decoration:none;}"
        ".meta{color:#555;font-size:13px;margin-bottom:6px;}"
        ".score{color:#888;font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}"
        ".summary{margin:8px 0 0;padding:8px 12px;border-left:3px solid #ccc;background:#fff;color:#333;font-size:13px;}"
        ".profile-fit{margin:6px 0 0;padding:4px 10px;background:#eef6ff;color:#0a4480;font-size:12px;font-style:italic;border-radius:4px;}"
        ".new-badge{background:#e7f5ec;color:#1a8f3c;padding:1px 6px;border-radius:4px;font-size:11px;margin-left:6px;vertical-align:middle;}"
        ".footer{margin-top:36px;padding-top:14px;border-top:1px solid #eee;color:#888;font-size:12px;}"
    )
    parts = [
        "<html><head><style>", css, "</style></head><body>",
        f"<h1>{header_title}</h1>",
    ]
    if header_summary:
        parts.append(f'<div class="sub">{header_summary}</div>')
    if source_notice_html:
        parts.append(source_notice_html)
    for tier_key, blocks in tier_blocks.items():
        if not blocks:
            continue
        label, color = TIER_RENDER[tier_key]
        parts.append(f'<h2 style="color:{color}">{label} ({len(blocks)})</h2>')
        for block in blocks:
            j = parse_block(block)
            if not j:
                continue
            new_badge = '<span class="new-badge">new</span>' if "🆕" in j["new"] else ""
            meta_text = j["meta"].replace("**", "").replace("_", "")
            # HTML-escape every user-controlled string before splicing into the
            # template. Without this, characters like `&` (e.g. "Bath & Body
            # Works") render as malformed entities in stricter email clients
            # and can silently break rendering of the rest of the email.
            url_attr = html.escape(j["url"], quote=True)
            title_html = html.escape(j["title"])
            meta_safe = html.escape(meta_text)
            score_html = html.escape(j["score"])
            fit_safe = html.escape(j["profile_fit"]) if j.get("profile_fit") else ""
            summary_safe = html.escape(j["summary"]) if j["summary"] else ""
            fit_html = f'<div class="profile-fit">Profile fit: {fit_safe}</div>' if fit_safe else ""
            summary_html = f'<div class="summary">{summary_safe}</div>' if summary_safe else ""
            parts.append(
                f'<div class="job">'
                f'<h3><a href="{url_attr}">{title_html}</a>{new_badge}</h3>'
                f'<div class="meta">{meta_safe}</div>'
                f'<div class="score">{score_html}</div>'
                f'{fit_html}'
                f'{summary_html}'
                f'</div>'
            )
    parts.append(
        f'<div class="footer">{total} listings emailed for profile <code>{profile_name}</code>. '
        f'Full digest archive at <code>profiles/{profile_name}/digest_archive/</code>.</div>'
    )
    parts.append("</body></html>")
    return "".join(parts)


def render_plaintext(header_title, header_summary, tier_blocks, source_notice_text=""):
    out = [header_title, ""]
    if header_summary:
        out += [header_summary, ""]
    if source_notice_text:
        out += [source_notice_text, ""]
    for tier_key, blocks in tier_blocks.items():
        if not blocks:
            continue
        label, _ = TIER_RENDER[tier_key]
        out += ["", f"== {label} ({len(blocks)}) ==", ""]
        for block in blocks:
            j = parse_block(block)
            if not j:
                continue
            new_marker = " [NEW]" if "🆕" in j["new"] else ""
            out += [
                f"• {j['title']}{new_marker}",
                f"  {j['url']}",
                f"  {j['meta'].replace('**', '').replace('_', '')}",
            ]
            if j.get("profile_fit"):
                out += [f"  Profile fit: {j['profile_fit']}"]
            if j["summary"]:
                out += [f"  {j['summary']}"]
            out += [""]
    return "\n".join(out)


def emit_skip(reason):
    print(json.dumps({"skip": True, "reason": reason}))
    sys.exit(0)


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    profile_dir = Path(sys.argv[1])
    # project_root accepted for API compat with old script; not used.

    email_cfg_path = profile_dir / "email.json"
    if not email_cfg_path.exists():
        emit_skip(f"no email.json at {email_cfg_path}")
    email_cfg = json.loads(email_cfg_path.read_text())
    if not email_cfg.get("enabled", True):
        emit_skip("email disabled")
    recipient = email_cfg.get("recipient_email", "").strip()
    if not recipient:
        emit_skip("no recipient_email")
    tier_keys = email_cfg.get("tiers") or ["strong", "worth_a_look"]
    if not isinstance(tier_keys, list) or not tier_keys:
        tier_keys = ["strong"]

    digest_path = profile_dir / "digest.md"
    if not digest_path.exists():
        emit_skip(f"no digest at {digest_path}")
    digest_text = digest_path.read_text()
    header_title, header_summary = extract_header(digest_text)

    all_tiers = split_into_tiers(digest_text)
    selected = {k: all_tiers.get(k, []) for k in tier_keys}
    total = sum(len(b) for b in selected.values())

    # Optional source-status banner.
    source_notice_html = ""
    source_notice_text = ""
    today_iso = datetime.now().strftime("%Y-%m-%d")
    status_path = profile_dir / "data" / f"source_status_{today_iso}.json"
    if status_path.exists():
        try:
            statuses = json.loads(status_path.read_text())
            failures = [(s, r) for s, r in statuses.items() if r != "ok"]
            if failures:
                items = ", ".join(f"{s} ({r})" for s, r in failures)
                source_notice_html = (
                    '<div style="background:#fff5e6;border:1px solid #f0b97a;'
                    'border-radius:6px;padding:10px 14px;margin:12px 0 18px;'
                    'color:#7a4a00;font-size:13px;">'
                    f"⚠️ Some sources were unavailable today: <b>{items}</b>. "
                    "Listings from these sources are not in this digest."
                    "</div>"
                )
                source_notice_text = (
                    f"NOTE: Some sources were unavailable today: {items}.\n"
                    "Listings from these sources are not in this digest.\n"
                )
        except Exception as e:
            print(f"Could not read {status_path}: {e}", file=sys.stderr)

    if total == 0 and not source_notice_html:
        emit_skip(f"no matches in selected tiers ({tier_keys})")

    counts = " · ".join(
        f"{len(selected.get(k, []))} {TIER_RENDER[k][0].split()[1].lower()}"
        for k in tier_keys if selected.get(k)
    )
    today_str = datetime.now().strftime("%a %b %d")
    subject_marker = f"Job Digest — {today_str}"
    subject = f"{subject_marker} ({counts})" if counts else subject_marker

    html_body = render_html(
        profile_dir.name, header_title, header_summary, selected, total,
        source_notice_html=source_notice_html,
    )
    plain_body = render_plaintext(
        header_title, header_summary, selected,
        source_notice_text=source_notice_text,
    )

    print(json.dumps({
        "skip": False,
        "to": [recipient],
        "subject": subject,
        "subject_marker": subject_marker,
        "htmlBody": html_body,
        "body": plain_body,
        "total": total,
        "tier_counts": {k: len(v) for k, v in selected.items()},
    }))


if __name__ == "__main__":
    main()
