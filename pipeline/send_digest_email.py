#!/usr/bin/env python3
"""
Send a job-digest email via Gmail SMTP.

Reads the profile's `digest.md`, extracts the configured tiers, renders an HTML
email, and sends from the dedicated Gmail (configured via project-root `.env`)
to the profile's recipient (configured via `<profile>/email.json`).

Usage:
    send_digest_email.py <profile_dir> <project_root>

  profile_dir   path to profiles/<name>/
  project_root  path to the project root (where `.env` lives)

Env file format (project_root/.env):
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=465
    SMTP_USER=dedicated.account@gmail.com
    SMTP_PASSWORD=xxxxxxxxxxxxxxxx       # 16-char Gmail app password
    SMTP_FROM_NAME=Job Pipeline          # optional display name

Profile email.json schema:
    {
      "recipient_email": "owner@example.com",
      "tiers": ["strong", "worth_a_look"],   // any subset of strong, worth_a_look, lower
      "enabled": true
    }

Exits 0 if the email was sent (or skipped because disabled / no matches).
Exits non-zero on a real error so the pipeline run knows.
"""

import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


# ---------- .env loader (no external dep) ----------
def load_env(env_path: Path) -> dict:
    if not env_path.exists():
        return {}
    out = {}
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        # Strip matching quotes
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        out[k.strip()] = v
    return out


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
    r"(?P<score>`Score:[^\n]+`[^\n]*)\n"
    r"(?P<rest>(?:\n> [^\n]+\n)?)",
    re.MULTILINE,
)


def split_into_tiers(digest_text: str) -> dict:
    """Return {tier_key: [block_text, ...]} for whichever tiers appear in the digest."""
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
        # Split on '### ' headers (each is one job)
        blocks = re.split(r"(?=^### )", body, flags=re.MULTILINE)
        blocks = [b.strip() for b in blocks if b.strip()]
        tiers[tier_key] = blocks
    return tiers


def parse_block(block_text: str) -> dict | None:
    """Pull out title/url/meta/score/summary from a single job block."""
    m = JOB_BLOCK_RE.search(block_text)
    if not m:
        return None
    rest = m.group("rest").strip()
    summary = ""
    if rest.startswith(">"):
        summary = rest[1:].strip()
    return {
        "title": m.group("title").strip(),
        "url": m.group("url").strip(),
        "new": m.group("new").strip(),
        "meta": m.group("meta").strip(),
        "score": m.group("score").strip(),
        "summary": summary,
    }


def extract_header(digest_text: str) -> tuple[str, str]:
    """Return (h1_title, italic_summary_line) from the top of the digest."""
    lines = digest_text.splitlines()
    title = lines[0].lstrip("# ").strip() if lines else "Daily Job Digest"
    summary = ""
    for line in lines[1:6]:
        s = line.strip()
        if s.startswith("_") and s.endswith("_"):
            summary = s.strip("_").strip()
            break
    return title, summary


# ---------- HTML rendering ----------
TIER_RENDER = {
    "strong": ("🟢 Strong Matches", "#1a8f3c"),
    "worth_a_look": ("🟡 Worth a Look", "#b88800"),
    "lower": ("⚪ Lower Priority", "#666"),
}


def render_html(profile_name: str, header_title: str, header_summary: str,
                tier_blocks: dict, total: int, source_notice_html: str = "") -> str:
    css = (
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
        "color:#222;line-height:1.45;max-width:760px;margin:24px auto;padding:0 16px;}"
        "h1{margin:0 0 4px;font-size:22px;}"
        "h2{margin-top:32px;font-size:18px;border-bottom:1px solid #eee;padding-bottom:6px;}"
        ".sub{color:#666;font-size:13px;margin-bottom:24px;}"
        ".job{margin:18px 0;padding:12px 14px;border:1px solid #e3e3e3;border-radius:8px;background:#fafafa;}"
        ".job h3{margin:0 0 4px;font-size:16px;}"
        ".job h3 a{color:#0a58ca;text-decoration:none;}"
        ".job h3 a:hover{text-decoration:underline;}"
        ".meta{color:#555;font-size:13px;margin-bottom:6px;}"
        ".score{color:#888;font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}"
        ".summary{margin:8px 0 0;padding:8px 12px;border-left:3px solid #ccc;background:#fff;color:#333;font-size:13px;}"
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
            # j["meta"] looks like: **Company** · Loc · $X-$Y · _Source · posted X ago_
            # Render the bold/italic conventions to HTML by simple substitution
            meta_html = (
                j["meta"]
                .replace("**", "")
                .replace("_", "")
            )
            summary_html = ""
            if j["summary"]:
                summary_html = f'<div class="summary">{j["summary"]}</div>'
            parts.append(
                f'<div class="job">'
                f'<h3><a href="{j["url"]}">{j["title"]}</a>{new_badge}</h3>'
                f'<div class="meta">{meta_html}</div>'
                f'<div class="score">{j["score"]}</div>'
                f'{summary_html}'
                f'</div>'
            )

    parts.append(
        f'<div class="footer">{total} listings emailed for profile <code>{profile_name}</code>. '
        f'Full digest archive at <code>profiles/{profile_name}/digest_archive/</code>.</div>'
    )
    parts.append("</body></html>")
    return "".join(parts)


def render_plaintext(header_title: str, header_summary: str,
                     tier_blocks: dict, source_notice_text: str = "") -> str:
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
            if j["summary"]:
                out += [f"  {j['summary']}"]
            out += [""]
    return "\n".join(out)


# ---------- Main ----------
def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    profile_dir = Path(sys.argv[1])
    project_root = Path(sys.argv[2])

    email_cfg_path = profile_dir / "email.json"
    if not email_cfg_path.exists():
        print(f"No email.json at {email_cfg_path}; skipping email send.", file=sys.stderr)
        return
    email_cfg = json.loads(email_cfg_path.read_text())
    if not email_cfg.get("enabled", True):
        print(f"Email disabled for profile {profile_dir.name}; skipping.", file=sys.stderr)
        return

    recipient = email_cfg.get("recipient_email", "").strip()
    if not recipient:
        print(f"No recipient_email in {email_cfg_path}; skipping.", file=sys.stderr)
        return

    tier_keys = email_cfg.get("tiers") or ["strong", "worth_a_look"]
    if not isinstance(tier_keys, list) or not tier_keys:
        tier_keys = ["strong"]

    digest_path = profile_dir / "digest.md"
    if not digest_path.exists():
        print(f"No digest at {digest_path}; nothing to send.", file=sys.stderr)
        return

    digest_text = digest_path.read_text()
    header_title, header_summary = extract_header(digest_text)

    all_tiers = split_into_tiers(digest_text)
    selected = {k: all_tiers.get(k, []) for k in tier_keys}
    total = sum(len(b) for b in selected.values())

    # Optional: read source-status notice. Orchestrator writes
    # data/source_status_<TODAY>.json with {"<source>": "ok"|"<failure_reason>"}.
    # Any non-"ok" entry is rendered as a banner in the email.
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
        print(f"No matches in selected tiers ({tier_keys}); skipping send.", file=sys.stderr)
        return

    env = load_env(project_root / ".env")
    smtp_host = env.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(env.get("SMTP_PORT", "465"))
    smtp_user = env.get("SMTP_USER", "").strip()
    smtp_password = env.get("SMTP_PASSWORD", "").strip()
    from_name = env.get("SMTP_FROM_NAME", "Job Pipeline").strip()
    if not smtp_user or not smtp_password:
        print("SMTP_USER or SMTP_PASSWORD missing in .env; cannot send.", file=sys.stderr)
        sys.exit(1)

    counts = " · ".join(
        f"{len(selected.get(k, []))} {TIER_RENDER[k][0].split()[1].lower()}"
        for k in tier_keys if selected.get(k)
    )
    today_str = datetime.now().strftime("%a %b %d")
    subject = f"Job Digest — {today_str} ({counts})"

    html_body = render_html(
        profile_dir.name, header_title, header_summary, selected, total,
        source_notice_html=source_notice_html,
    )
    plain_body = render_plaintext(
        header_title, header_summary, selected,
        source_notice_text=source_notice_text,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{smtp_user}>" if from_name else smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=30) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

    print(f"Sent digest to {recipient} ({total} listings: {counts}).", file=sys.stderr)


if __name__ == "__main__":
    main()
