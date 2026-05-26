# Scheduled Task Prompt — Template (multi-profile)

This is the prompt body for the daily Cowork scheduled task. It runs once a day
via cron, fetches all configured sources for every profile, scores, enriches,
and writes a digest per profile.

## How this file is used

**This file is the canonical source of truth.** It is rendered into the live
Cowork skill at:
  `~/Documents/Claude/Scheduled/daily-job-search/SKILL.md`

The rendering happens once at install time (driven by `INSTALL_PROMPT.md`,
which substitutes the placeholders below and adds the required YAML
frontmatter). After install, the live SKILL.md is what Cowork actually
executes each morning.

**Editing workflow when you need to change the daily prompt:**
1. Edit this file (the template) — the change is captured in git.
2. Open a fresh chat in Cowork (NOT inside a scheduled-task run) and ask
   Claude to mirror the change into `~/Documents/Claude/Scheduled/daily-job-search/SKILL.md`,
   preserving the YAML frontmatter at the top of the live file.
3. The next scheduled run picks up the change. No re-install required.

There used to be a third file (`_new_scheduled_task_prompt.md`) that
mirrored the rendered runtime version in this repo for git history. It was
removed because it tended to drift out of sync with this template, which is
how the Step 3k Chrome-auto-send patch silently went missing.

## Placeholders to substitute at install time

When setting up, fill in three placeholders:
- `{PROJECT_DIR}` — absolute path to your project folder
  (e.g. `/Users/jane/Documents/Claude/Projects/JobApps/job-pipeline`)
- `{INDEED_MCP_ID}` — the Indeed connector's MCP UUID for your Cowork install
- `{GMAIL_MCP_ID}` — the default Gmail connector's MCP UUID (per-profile
  overrides are supported via `linkedin.json.gmail_mcp_id`)

(The companion `INSTALL_PROMPT.md` tells Claude how to discover these for you.)

Profiles live under `{PROJECT_DIR}/profiles/<name>/`. Each profile has its own
`profile.md`, `search_queries.json`, `scoring.json`, optional `linkedin.json`,
and its own `data/`, `digest.md`, and `digest_archive/` directories. The
`profiles/_template/` directory contains `*.example` files for bootstrapping
new profiles and is skipped at runtime.

---

Run the daily multi-source job search pipeline across all profiles.

# Step 1 — Get today's date
Run `date +%Y-%m-%d` via bash. Use the result as TODAY (e.g. "2026-05-01").

# Step 2 — List profiles
Run via bash:
```bash
ls -1 {PROJECT_DIR}/profiles | grep -vE '^(_|\.)'
```

This yields the list of active profile names (e.g. `kyle`, `alice`). Anything
starting with `_` or `.` is skipped (so `_template` is never run).

If the list is empty, exit with: `"No active profiles in {PROJECT_DIR}/profiles."`

# Step 3 — For EACH profile, run Steps 3a–3k sequentially

Process profiles one at a time.

Define `PROFILE_DIR={PROJECT_DIR}/profiles/<profile>` and create needed
subdirs once per profile:
```bash
mkdir -p $PROFILE_DIR/data/raw_searches \
         $PROFILE_DIR/data/linkedin_raw \
         $PROFILE_DIR/data/job_boards_raw \
         $PROFILE_DIR/digest_archive
```

**Source-status tracking.** Throughout this profile's run, maintain a JSON
object in memory that records each source's status, e.g. `{"indeed": "ok",
"linkedin": "ok", "job_boards": "ok"}`. If a source errors out (rate-limited,
no connector, no creds, parser failure, etc.), set its status to a short
reason string like `"rate_limited"`, `"chrome_unavailable"`, or
`"gmail_error"` and STOP making further calls for that source for this
profile — but continue with the other sources. At the end of the profile run
(Step 3i below), write this object to
`$PROFILE_DIR/data/source_status_{TODAY}.json` so the email digest can render
a banner about which sources were missing.

## Step 3a — Read this profile's search config
Read `$PROFILE_DIR/search_queries.json` for `role_queries` and `locations`. If
the file is missing or still contains placeholder text (`REPLACE-WITH-...`),
skip this profile and log: `"Skipping profile <name>: search_queries.json not personalized."`

## Step 3b — Indeed searches via sub-agent (this profile)

**Do NOT call the Indeed `search_jobs` MCP from the main assistant context.**
The search matrix (one call per role × location pair, run serially so the
rate limiter doesn't trip — often 20-40 calls per profile) is the single
biggest consumer of the parent's per-run TURN budget and has caused runs to
hit the max-turns guardrail. Spawn a sub-agent that runs the whole matrix in
its own turn/context window and returns one status line — same pattern as the
LinkedIn steps below.

Read the sub-agent prompt template via the `Read` tool:
  `{PROJECT_DIR}/pipeline/agent_prompts/indeed_search_subagent.md`

In the body of that file (everything after the `---` separator that ends
the placeholder list), substitute:
- `{{PROFILE}}` → the profile name
- `{{PROFILE_DIR}}` → `$PROFILE_DIR`
- `{{PROJECT_DIR}}` → `{PROJECT_DIR}`
- `{{TODAY}}` → the TODAY date string
- `{{INDEED_MCP_ID}}` → `{INDEED_MCP_ID}`

The sub-agent reads `$PROFILE_DIR/search_queries.json` itself, runs every
(role × location) pair SERIALLY (with exponential backoff on any
`Rate limit exceeded`, capping at 60s and skipping a pair after 3 failures),
concatenates the successful results, and writes them to
`$PROFILE_DIR/data/raw_searches/{TODAY}.txt`.

Call the `Agent` tool with:
- `subagent_type`: `"general-purpose"`
- `description`: `"Indeed search (<profile>)"`
- `prompt`: the substituted body

The sub-agent returns one line. Parse it with best-effort regex: extract the
first `status=(\S+)`. Map status to source-status:
- `status=ok` — set `"indeed": "ok"`.
- `status=rate_limited_partial` — set `"indeed": "rate_limited_partial"`.
- `status=rate_limited` — set `"indeed": "rate_limited"` (no raw file was
  written; Step 3e simply won't receive an Indeed input path).
- `status=config_error` — this profile's `search_queries.json` is missing or
  still a placeholder; skip this profile entirely and log
  `"Skipping profile <name>: search_queries.json not personalized."`
- Anything else / unparseable — treat as `"indeed": "protocol_error"` and
  continue to the LinkedIn steps (Step 3e will skip the Indeed input if the
  raw file is absent).

## Step 3c — LinkedIn email alerts via sub-agent (this profile, gated by linkedin.json)
Read `$PROFILE_DIR/linkedin.json` if it exists. If the file is missing, or
`enabled` is `false`, or `gmail_labels` is empty, skip Steps 3c and 3d for
this profile and set source-status `"linkedin": "disabled"`.

**Do NOT call the Gmail MCP from the main assistant context.** Spawn a
sub-agent that handles all Gmail traffic in its own context window — thread
bodies never enter the parent's 200K budget. This is the single biggest
context-window saving in the LinkedIn flow.

Build the Gmail query from `gmail_labels`:
- One label `X`: `(label:X) newer_than:1d`
- Multiple labels: `(label:X OR label:Y OR ...) newer_than:1d`

Pick the Gmail MCP UUID: `linkedin.json.gmail_mcp_id` if set, else `{GMAIL_MCP_ID}`.

Read the sub-agent prompt template via the `Read` tool:
  `{PROJECT_DIR}/pipeline/agent_prompts/linkedin_fetch_subagent.md`

In the body of that file (everything after the `---` separator that ends
the placeholder list), substitute:
- `{{PROFILE}}` → the profile name
- `{{PROFILE_DIR}}` → `$PROFILE_DIR`
- `{{PROJECT_DIR}}` → `{PROJECT_DIR}`
- `{{TODAY}}` → the TODAY date string
- `{{GMAIL_MCP_ID}}` → the chosen Gmail MCP UUID for this profile
- `{{GMAIL_QUERY}}` → the built query

Call the `Agent` tool with:
- `subagent_type`: `"general-purpose"`
- `description`: `"LinkedIn Gmail fetch (<profile>)"`
- `prompt`: the substituted body

The sub-agent returns one line. Parse it with best-effort regex: extract the
first `status=(\S+)` and the first `unique_jobs=(\d+)`. Valid statuses:
- `status=ok` — proceed to Step 3d.
- `status=no_threads` — nothing today; set `"linkedin": "no_threads"` and
  skip Step 3d.
- `status=gmail_error` — set `"linkedin": "gmail_error"` and skip Step 3d.
- `status=parser_error` — set `"linkedin": "parser_error"` and skip Step 3d.
- Anything else / unparseable — treat as `protocol_error`, set
  `"linkedin": "protocol_error"`, skip Step 3d, continue to the next source.

On `status=ok`, set `"linkedin": "ok"`. If `unique_jobs=0`, skip Step 3d
(nothing to enrich).

## Step 3d — LinkedIn Chrome enrichment via sub-agent (PRE-SCORE, this profile)
Skip if Step 3c set `"linkedin"` to anything other than `ok`, or if Step 3c
returned `unique_jobs=0`.

**Do NOT call the `Claude_in_Chrome` MCP from the main assistant context.**
Spawn a sub-agent that drives Chrome in its own context window — LinkedIn
page text (which dominates the LinkedIn token cost) never enters the
parent's 200K budget.

Read the sub-agent prompt template via the `Read` tool:
  `{PROJECT_DIR}/pipeline/agent_prompts/linkedin_enrich_subagent.md`

In the body of that file (everything after the `---` separator), substitute:
- `{{PROFILE}}` → the profile name
- `{{PROFILE_DIR}}` → `$PROFILE_DIR`
- `{{PROJECT_DIR}}` → `{PROJECT_DIR}`
- `{{TODAY}}` → the TODAY date string
- `{{SIDECAR_PATH}}` → `$PROFILE_DIR/data/linkedin_raw/{TODAY}_normalized.jobs.json`

Call the `Agent` tool with:
- `subagent_type`: `"general-purpose"`
- `description`: `"LinkedIn Chrome enrichment (<profile>)"`
- `prompt`: the substituted body

The sub-agent returns one line. Parse with best-effort regex: extract the
first `status=(\S+)`, `enriched=(\d+)`, `skipped=(\d+)`. Valid statuses:
- `status=ok` — keep `"linkedin": "ok"`.
- `status=no_jobs` — keep `"linkedin"` as-is (3c already set it).
- `status=chrome_unavailable` — downgrade source-status to
  `"linkedin": "chrome_unavailable"`. The pipeline still works; Step 3g
  will retry these as the post-score fallback.
- `status=login_wall` — downgrade to `"linkedin": "login_wall"`. Same
  fallback behavior.
- `status=splicer_error` — set `"linkedin": "splicer_error"`. Inspect
  manually after the run.
- Unparseable — set `"linkedin": "protocol_error"`, continue.

The sub-agent has already written
`/tmp/<profile>_linkedin_chrome_enrichments_{TODAY}.json` and run
`enrich_linkedin_md.py`, so no further action is needed for this step in
the parent — proceed to Step 3d.5.

## Step 3d.5 — Job boards fetch (this profile, gated by job_boards.json)
Skip and set source-status `"job_boards": "disabled"` if
`$PROFILE_DIR/job_boards.json` is missing or has no boards with
`enabled: true`. Otherwise run the dispatcher:

```bash
python3 -m pipeline.job_boards $PROFILE_DIR {TODAY}
```

Run from `{PROJECT_DIR}` (the working directory must be the project root so
the `pipeline.job_boards` module is importable). The dispatcher prints a
single JSON line on stdout summarizing the run, e.g.:

```json
{"total": 47, "per_board": {"climate_draft": 20, "elemental_impact": 20, "terra_do": 7},
 "wrote_raw": "$PROFILE_DIR/data/job_boards_raw/{TODAY}.txt",
 "wrote_enrichments": "/tmp/<profile>_job_enrichments_jobboards_{TODAY}.json",
 "status": "ok"}
```

The dispatcher writes two files for downstream consumption:
- `$PROFILE_DIR/data/job_boards_raw/{TODAY}.txt` — markdown stubs in the
  same format `hn_fetcher.py` produces. Step 3e adds this to the
  parse_and_score input list.
- `/tmp/<profile>_job_enrichments_jobboards_{TODAY}.json` — `{hash: short_summary}`
  enrichments. The board adapters fetched full descriptions in one pass, so
  no per-job enrichment is needed in Step 3f — Step 3h's merge picks up
  this file alongside Indeed and LinkedIn enrichments.

Source status from the JSON line's `status` field:
- `ok` — set `"job_boards": "ok"`.
- `no_config` / `no_enabled` — set `"job_boards": "disabled"`.
- Any non-zero exit / parse error — set `"job_boards": "fetcher_error"` and
  continue. Step 3e will still run with whatever other sources succeeded.

If the dispatcher exits non-zero or the JSON is unparseable, set
`"job_boards": "fetcher_error"` and continue to Step 3e.

## Step 3e — Run the parser/scorer with all available sources (this profile)
Build the input file list dynamically — only pass paths that exist:

  ```bash
  python3 {PROJECT_DIR}/pipeline/parse_and_score.py \
    $PROFILE_DIR/digest.md \
    $PROFILE_DIR/data/seen_jobs.json \
    $PROFILE_DIR/data/raw_searches/{TODAY}.txt \
    $PROFILE_DIR/data/linkedin_raw/{TODAY}_normalized.txt \
    $PROFILE_DIR/data/job_boards_raw/{TODAY}.txt
  ```

The parser auto-loads scoring rules from `$PROFILE_DIR/scoring.json` and detects source from each job_id prefix (`hn-`, `linkedin-`, `jb-`, or no prefix for Indeed).

This step writes:
  - `$PROFILE_DIR/needs_enrichment.json` — Indeed strong matches needing get_job_details
  - `$PROFILE_DIR/needs_enrichment_linkedin.json` — LinkedIn jobs that didn't get a summary in Step 3d
  - `$PROFILE_DIR/data/cached_enrichments_{TODAY}.json` — pre-seeded summaries
    for any Strong-tier hashes already in the enrichment cache. Step 3h
    merges this with the fresh-fetch enrichments before splicing.

The parser auto-loads the enrichment cache from
`$PROFILE_DIR/data/enrichment_cache.json` and filters cached hashes OUT of
the two `needs_enrichment*.json` files — so Steps 3f and 3g only fetch the
delta. This is the per-job rate-limit relief: if we enriched `JOB_3870`
yesterday, we don't re-spend an Indeed `get_job_details` call on it today.

## Step 3f — Enrich Indeed strong matches with full descriptions (this profile)
Read `$PROFILE_DIR/needs_enrichment.json`. **Note:** parse_and_score has
already removed hashes that exist in the enrichment cache, so this list
contains only NEW Strong-tier Indeed jobs that need a fresh fetch. The
cached summaries for previously-enriched jobs are in
`$PROFILE_DIR/data/cached_enrichments_{TODAY}.json` and will be merged in
at Step 3h.

For each entry (cap at 15):
  - Call `mcp__{INDEED_MCP_ID}__get_job_details` with the entry's `job_id`.
  - Run these calls SERIALLY with a 1-second sleep between each — same
    rate-limit avoidance as the Step 3b sub-agent.
  - Write a 2-3 sentence factual summary capturing what the team does, key
    skills/seniority, distinctive scope. Avoid company boilerplate. Under
    350 chars.
  - Build dict: `{ <hash>: <summary_text> }`

Write to `/tmp/<profile>_job_enrichments_indeed_{TODAY}.json`. If the
needs_enrichment list is empty (everything was cached), still write `{}` so
Step 3h's merge logic is uniform.

## Step 3g — LinkedIn fallback enrichment (this profile)
Read `$PROFILE_DIR/needs_enrichment_linkedin.json`. If empty or missing, skip.
Otherwise re-run the same Chrome flow as Step 3d for these stragglers, building summaries.
Write to `/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json`.

## Step 3h — Splice all post-score enrichments (this profile)
Merge three sources before splicing: the cached summaries (from Step 3e),
this run's fresh Indeed enrichments (3f), and this run's fresh LinkedIn
fallback enrichments (3g). Cache takes lowest precedence so a fresh fetch
overrides a stale cached entry if both somehow exist for the same hash.

  ```bash
  python3 -c "import json,os; \
    c=json.load(open('$PROFILE_DIR/data/cached_enrichments_{TODAY}.json')) if os.path.exists('$PROFILE_DIR/data/cached_enrichments_{TODAY}.json') else {}; \
    a=json.load(open('/tmp/<profile>_job_enrichments_indeed_{TODAY}.json')) if os.path.exists('/tmp/<profile>_job_enrichments_indeed_{TODAY}.json') else {}; \
    b=json.load(open('/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json')) if os.path.exists('/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json') else {}; \
    d=json.load(open('/tmp/<profile>_job_enrichments_jobboards_{TODAY}.json')) if os.path.exists('/tmp/<profile>_job_enrichments_jobboards_{TODAY}.json') else {}; \
    json.dump({**c, **a, **b, **d}, open('/tmp/<profile>_job_enrichments_{TODAY}.json', 'w'))"

  python3 {PROJECT_DIR}/pipeline/splice_enrichments.py \
    $PROFILE_DIR/digest.md \
    /tmp/<profile>_job_enrichments_{TODAY}.json
  ```

After splicing succeeds, write this run's fresh enrichments back into the
cache so the next run can skip them:

  ```bash
  python3 {PROJECT_DIR}/pipeline/update_enrichment_cache.py \
    $PROFILE_DIR/data/enrichment_cache.json \
    /tmp/<profile>_job_enrichments_indeed_{TODAY}.json \
    /tmp/<profile>_job_enrichments_linkedin_{TODAY}.json \
    /tmp/<profile>_job_enrichments_jobboards_{TODAY}.json
  ```

`update_enrichment_cache.py` skips missing input files silently, so this
works even if 3f or 3g was skipped earlier. It also infers the source label
(`Indeed` / `LinkedIn`) from the filename.

## Step 3h.5 — Profile-fit re-rank for the Strong tier (this profile)
At this point, every Strong-tier job in `$PROFILE_DIR/digest.md` has its full
description spliced in as a `> blockquote` summary. Use that signal plus the
profile narrative to re-rank within the Strong tier.

Read `$PROFILE_DIR/profile.md`. For each job in the **Strong Matches tier
only** (do NOT process Worth a Look or Lower Priority):
  - Read the title, the meta line, and the description in the blockquote
    summary directly below the score line.
  - Assign a `fit_score` 0–5 based on how well the role aligns with the
    profile narrative — sector, level, type of work, scope, stack:
      5 = excellent fit — directly matches a key strength + sector alignment
      4 = strong fit — most of the role overlaps with the profile
      3 = decent fit — relevant but missing one or two important elements
      2 = marginal — adjacent to profile but not a clean match
      1 = weak — mostly mismatched
      0 = skip / unable to assess
  - Write a one-line `fit_notes` (under 80 chars) capturing why.

Extract each Strong-tier block's hash from its `<!--HASH:abc-->` marker.
Build a dict:
  ```json
  { "<hash>": {"fit_score": 4, "fit_notes": "climate ecosystem alignment, cross-sector ops"} }
  ```

Write to `/tmp/<profile>_fit_scores_{TODAY}.json`. Then re-rank:
  ```bash
  python3 {PROJECT_DIR}/pipeline/apply_profile_fit.py \
    $PROFILE_DIR/digest.md \
    /tmp/<profile>_fit_scores_{TODAY}.json
  ```

This adds the fit score to each scored job's total, re-tiers using the
profile's existing thresholds, re-renders the digest preserving spliced
summaries, and surfaces a `_Profile fit: N/5 — notes_` line under each
re-scored job. The fit pass can demote a Strong match into Worth a Look if
its narrative fit is poor; it can also keep ordering tighter at the top of
Strong tier.

## Step 3i — Write source-status snapshot (this profile)
Persist the source-status object (built throughout Steps 3b–3g) to:
  `$PROFILE_DIR/data/source_status_{TODAY}.json`

Each key is a source name (`indeed`, `linkedin`, etc.) and each value is the
string `"ok"` or a short reason if that source wasn't fully successful (e.g.
`"rate_limited"`, `"chrome_unavailable"`, `"gmail_error"`, `"label_not_set"`).
The email-send script reads this file and prepends a banner if any value is
non-`"ok"`.

Example:
  ```bash
  cat > $PROFILE_DIR/data/source_status_{TODAY}.json << 'EOF'
  {"indeed": "ok", "linkedin": "ok", "job_boards": "ok"}
  EOF
  ```

## Step 3j — Archive (this profile)
  ```bash
  cp $PROFILE_DIR/digest.md \
     $PROFILE_DIR/digest_archive/{TODAY}.md
  ```

## Step 3k — Prepare digest payload, create draft, AND send via Chrome (this profile, gated by email.json)

**End-state required:** the digest email must be *sent* (not merely drafted) by
the end of this step whenever the user's environment supports it. `create_draft`
alone is NEVER the success state — it is one half of a two-half flow. The
"drafted only" outcome is reserved for documented failure modes; if Chrome is
connected and you reach the draft, you MUST continue into the Chrome Send flow.

The Cowork sandbox blocks SMTP, so this step stages the message via the Gmail
MCP `create_draft` tool and then sends it by driving the user's logged-in Gmail
tab via the `mcp__Claude_in_Chrome__*` tools.

### 3k.1 — Build the payload
Run via bash:
  ```bash
  python3 {PROJECT_DIR}/pipeline/prepare_digest_email.py \
    $PROFILE_DIR \
    {PROJECT_DIR}
  ```

The script reads `$PROFILE_DIR/email.json` and prints a single JSON object
on stdout. Parse it.

If `payload.skip == true`, log `payload.reason`, set this profile's send
status to `"skipped"`, and skip the rest of Step 3k for this profile.
Continue to the next profile.

The payload has: `to` (array), `subject`, `subject_marker`, `htmlBody`,
`body`, `total`.

### 3k.2 — Create the Gmail draft
Call `mcp__{GMAIL_MCP_ID}__create_draft` with:
  - `to`: `payload.to`
  - `subject`: `payload.subject`
  - `htmlBody`: `payload.htmlBody`
  - `body`: `payload.body`

If `linkedin.json.gmail_mcp_id` overrides the Gmail MCP for this profile,
use that override here too.

Capture the returned draft id for logging.

If `create_draft` errors, log the failure, mark this profile's send status as
`"drafted_failed"`, and continue to the next profile. Do NOT proceed to 3k.3
in that case.

### 3k.3 — Connect Chrome and open Drafts
This step is REQUIRED whenever `create_draft` succeeded. Do not skip it as a
"safer" default — the full send flow is the success path.

a. Call `mcp__Claude_in_Chrome__list_connected_browsers`. Pick first with
   `isLocal=true`. If none connected, mark this profile's send status as
   `"drafted_chrome_unavailable"` and continue to the next profile — this is
   the ONLY acceptable reason to stop at "drafted".
b. Call `mcp__Claude_in_Chrome__select_browser` with that deviceId.
c. Call `mcp__Claude_in_Chrome__tabs_context_mcp` with `createIfEmpty=true`.
   Capture tabId.
d. Run a `mcp__Claude_in_Chrome__browser_batch` with:
   ```json
   [
     {"name": "navigate", "input": {"tabId": <ID>, "url": "https://mail.google.com/mail/u/0/#drafts"}},
     {"name": "computer", "input": {"action": "wait", "duration": 4, "tabId": <ID>}},
     {"name": "javascript_tool", "input": {"action": "javascript_exec", "tabId": <ID>, "text": "(()=>{const t=document.title; const u=location.href; const has=document.body.innerText.includes(\"You don't have any saved drafts\"); return JSON.stringify({title:t, url:u, empty:has});})()"}}
   ]
   ```

If `title` does not include the email address that owns the Gmail MCP (i.e.
the browser is logged into a different Google account than the MCP wrote the
draft to), mark this profile `"drafted_wrong_account"` and continue.

If `empty` is true, retry the batch once after another 4-second wait. If still
empty, mark this profile `"drafted_not_visible"` and continue.

### 3k.4 — Open the draft by subject_marker and click Send

Run a `mcp__Claude_in_Chrome__browser_batch` to locate the draft row, click
into it, then click Send:
```json
[
  {"name": "javascript_tool", "input": {"action": "javascript_exec", "tabId": <ID>, "text": "(()=>{const m=<JSON_STRING(payload.subject_marker)>; const rows=document.querySelectorAll('tr.zA'); for(const r of rows){if((r.innerText||'').includes(m)){r.querySelector('span.bog, span.bqe, .y6')?.click() || r.click(); return 'opened';}} return 'not_found';})()"}},
  {"name": "computer", "input": {"action": "wait", "duration": 3, "tabId": <ID>}},
  {"name": "javascript_tool", "input": {"action": "javascript_exec", "tabId": <ID>, "text": "(()=>{const btns=document.querySelectorAll('[role=\"button\"],div'); for(const b of btns){const dt=b.getAttribute('data-tooltip')||''; const al=b.getAttribute('aria-label')||''; const tx=(b.innerText||'').trim(); if(/^Send\\b/.test(dt)||/^Send\\b/.test(al)||tx==='Send'){b.click(); return 'sent_clicked';}} return 'no_send_btn';})()"}},
  {"name": "computer", "input": {"action": "wait", "duration": 3, "tabId": <ID>}},
  {"name": "javascript_tool", "input": {"action": "javascript_exec", "tabId": <ID>, "text": "JSON.stringify({url:location.href, sentToast: /Message sent|Your message has been sent/i.test(document.body.innerText)})"}}
]
```

`<JSON_STRING(payload.subject_marker)>` means a JSON-encoded string literal
of `payload.subject_marker` (so quotes/em-dashes survive).

The send is successful when EITHER:
- the final URL no longer contains `?compose=`, OR
- the `sentToast` regex matched.

If neither: re-run the second JS in the batch once after a 2-second wait. If
still not sent, mark this profile `"drafted_send_failed"` and continue.

On success, set this profile's send status to `"sent"`.

### 3k.5 — Close the tab
Call `mcp__Claude_in_Chrome__tabs_close_mcp` with the tabId from 3k.3.

# Step 4 — Summary
After all profiles are done, reply with one line per profile plus a header:
```
Daily digest updated for N profiles:
  • <profile1>: <X> new today, <Y> strong matches across <sources>, <Ki> Indeed enriched, <Kl> LinkedIn enriched, <Kj> JobBoard enriched — send: <send_status>
  • <profile2>: ...
```

Allowed `<send_status>` values from Step 3k:
- `sent` — expected default. The digest email was successfully sent via the
  Chrome auto-send flow.
- `skipped` — Step 3k chose to skip (email disabled, no recipient, or no
  matches in the configured tiers). Omit the `— send: skipped` suffix in
  this case if you prefer; or include it for visibility.
- `drafted_failed` — `create_draft` itself failed.
- `drafted_chrome_unavailable` — Chrome wasn't connected at run time.
- `drafted_wrong_account` — Chrome was logged into a different Google
  account than the Gmail MCP wrote the draft to.
- `drafted_not_visible` — The draft didn't appear in the Drafts list after
  navigation + retry.
- `drafted_send_failed` — Found the draft and clicked Send, but neither URL
  change nor "Message sent" toast confirmed the send.

Anything starting with `drafted_` is a signal that something needs attention.

If any profile was skipped (missing config, placeholder text, etc.), include that as a separate line: `"  • <name>: skipped — <reason>"`.
