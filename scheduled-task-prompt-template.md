# Scheduled Task Prompt — Template (multi-profile)

This is the prompt body for the daily Cowork scheduled task. It runs once a day
via cron, fetches all configured sources for every profile, scores, enriches,
and writes a digest per profile.

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
         $PROFILE_DIR/data/linkedin_raw $PROFILE_DIR/digest_archive
```

**Source-status tracking.** Throughout this profile's run, maintain a JSON
object in memory that records each source's status, e.g. `{"indeed": "ok",
"linkedin": "ok"}`. If a source errors out (rate-limited, no connector, no
creds, parser failure, etc.), set its status to a short reason string like
`"rate_limited"`, `"chrome_unavailable"`, or `"gmail_error"` and STOP making
further calls for that source for this profile — but continue with the other
sources. At the end of the profile run (Step 3i below), write this object to
`$PROFILE_DIR/data/source_status_{TODAY}.json` so the email digest can render
a banner about which sources were missing.

## Step 3a — Read this profile's search config
Read `$PROFILE_DIR/search_queries.json` for `role_queries` and `locations`. If
the file is missing or still contains placeholder text (`REPLACE-WITH-...`),
skip this profile and log: `"Skipping profile <name>: search_queries.json not personalized."`

## Step 3b — Indeed searches (this profile)
For every (role × location) pair, call `mcp__{INDEED_MCP_ID}__search_jobs` in parallel:
  - `search`: the role string
  - `location`: the location string
  - `country_code`: from config (default "US")
  - `job_type`: from config (default "fulltime")

Indeed has historically tolerated parallel bursts; if you see rate-limit
errors here, fall back to a serial-with-`sleep 1` pattern.

Concatenate every search response's "result" field (with blank lines between) and Write to:
  `$PROFILE_DIR/data/raw_searches/{TODAY}.txt`

If every Indeed call errored out, skip writing the file and set
source-status `"indeed": "<short reason>"`. Otherwise set `"indeed": "ok"`.

## Step 3c — LinkedIn email alerts (this profile, gated by linkedin.json)
Read `$PROFILE_DIR/linkedin.json` if it exists. If the file is missing, or
`enabled` is `false`, or `gmail_labels` is empty, skip Steps 3c and 3d for this profile.

Otherwise: use `linkedin.json.gmail_labels` (an array of one or more Gmail
labels) and `linkedin.json.gmail_mcp_id` if set, else fall back to `{GMAIL_MCP_ID}`.
Build a query that OR-combines every label, e.g. for `["linkedin-jobs-software", "linkedin-jobs-ai"]`:
  `query="(label:linkedin-jobs-software OR label:linkedin-jobs-ai) newer_than:1d"`

For a single-label list, the parentheses are still safe:
  `query="(label:linkedin-jobs-software) newer_than:1d"`

Call the chosen Gmail MCP's `search_threads` with:
  - `query="<built query>"`
  - `pageSize=50`

For every thread returned, call the Gmail MCP's `get_thread` with `messageFormat="FULL_CONTENT"` and extract `messages[].plaintextBody`.

Concatenate all plaintextBody values, separated by blank lines. Write to:
  `$PROFILE_DIR/data/linkedin_raw/{TODAY}.txt`

Then run the parser:
  ```bash
  python3 {PROJECT_DIR}/pipeline/linkedin_parser.py \
    $PROFILE_DIR/data/linkedin_raw/{TODAY}.txt \
    $PROFILE_DIR/data/linkedin_raw/{TODAY}_normalized.txt \
    {TODAY}
  ```

This also emits a sidecar JSON list at `{TODAY}_normalized.jobs.json`.

If no threads found or Gmail errors, skip Step 3d and continue.

## Step 3d — LinkedIn Chrome enrichment (PRE-SCORE, this profile)
Critical step: pulls salary + description from each LinkedIn page so the scorer
in Step 3e can rank fairly.

a. Call `mcp__Claude_in_Chrome__list_connected_browsers`. Pick first with `isLocal=true`.
   If none connected, skip this step (write `{}` to enrichments file) — pipeline still works, just less precise.
b. Call `mcp__Claude_in_Chrome__select_browser` with that deviceId.
c. Call `mcp__Claude_in_Chrome__tabs_context_mcp` with `createIfEmpty=true`. Capture tabId.

For each entry in the sidecar JSON, run `mcp__Claude_in_Chrome__browser_batch`:
  ```json
  [
    {"name": "navigate", "input": {"tabId": <ID>, "url": <entry.url>}},
    {"name": "computer", "input": {"action": "wait", "duration": 3, "tabId": <ID>}},
    {"name": "get_page_text", "input": {"tabId": <ID>}}
  ]
  ```

From the page text, extract:
  - Salary range — look for "$X - $Y per year", "$X - $Y", "$XK - $YK", etc. Format as `"$X - $Y a year"` matching the parser format. Use "N/A" if absent.
  - Posted age (e.g. "3 days ago"), applicant count.
  - 2-3 sentence factual description of the role/team. Avoid company boilerplate.
  - Final summary format: `"**$X – $Y** · N applicants · posted X ago. <description>"` (under 350 chars).

Build:
  ```json
  { "<hash>": { "compensation": "$X - $Y a year", "summary": "<formatted summary>" } }
  ```

Write to `/tmp/<profile>_linkedin_chrome_enrichments_{TODAY}.json`.

After the loop, close the tab via `mcp__Claude_in_Chrome__tabs_close_mcp`.

Apply the enrichments to the LinkedIn markdown:
  ```bash
  python3 {PROJECT_DIR}/pipeline/enrich_linkedin_md.py \
    $PROFILE_DIR/data/linkedin_raw/{TODAY}_normalized.txt \
    /tmp/<profile>_linkedin_chrome_enrichments_{TODAY}.json
  ```

## Step 3e — Run the parser/scorer with all available sources (this profile)
Build the input file list dynamically — only pass paths that exist:

  ```bash
  python3 {PROJECT_DIR}/pipeline/parse_and_score.py \
    $PROFILE_DIR/digest.md \
    $PROFILE_DIR/data/seen_jobs.json \
    $PROFILE_DIR/data/raw_searches/{TODAY}.txt \
    $PROFILE_DIR/data/linkedin_raw/{TODAY}_normalized.txt
  ```

The parser auto-loads scoring rules from `$PROFILE_DIR/scoring.json` and detects source from each job_id prefix.

This step writes:
  - `$PROFILE_DIR/needs_enrichment.json` — Indeed strong matches needing get_job_details
  - `$PROFILE_DIR/needs_enrichment_linkedin.json` — LinkedIn jobs that didn't get a summary in Step 3d

## Step 3f — Enrich Indeed strong matches with full descriptions (this profile)
Read `$PROFILE_DIR/needs_enrichment.json`. For each entry (cap at 15):
  - Call `mcp__{INDEED_MCP_ID}__get_job_details` with the entry's `job_id`.
  - Write a 2-3 sentence factual summary capturing what the team does, key skills/seniority, distinctive scope. Avoid company boilerplate. Under 350 chars.
  - Build dict: `{ <hash>: <summary_text> }`

Write to `/tmp/<profile>_job_enrichments_indeed_{TODAY}.json`.

## Step 3g — LinkedIn fallback enrichment (this profile)
Read `$PROFILE_DIR/needs_enrichment_linkedin.json`. If empty or missing, skip.
Otherwise re-run the same Chrome flow as Step 3d for these stragglers, building summaries.
Write to `/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json`.

## Step 3h — Splice all post-score enrichments (this profile)
  ```bash
  python3 -c "import json,os; \
    a=json.load(open('/tmp/<profile>_job_enrichments_indeed_{TODAY}.json')) if os.path.exists('/tmp/<profile>_job_enrichments_indeed_{TODAY}.json') else {}; \
    b=json.load(open('/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json')) if os.path.exists('/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json') else {}; \
    json.dump({**a, **b}, open('/tmp/<profile>_job_enrichments_{TODAY}.json', 'w'))"

  python3 {PROJECT_DIR}/pipeline/splice_enrichments.py \
    $PROFILE_DIR/digest.md \
    /tmp/<profile>_job_enrichments_{TODAY}.json
  ```

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
  {"indeed": "ok", "linkedin": "ok"}
  EOF
  ```

## Step 3j — Archive (this profile)
  ```bash
  cp $PROFILE_DIR/digest.md \
     $PROFILE_DIR/digest_archive/{TODAY}.md
  ```

## Step 3k — Send digest email (this profile, gated by email.json)
  ```bash
  python3 {PROJECT_DIR}/pipeline/send_digest_email.py \
    $PROFILE_DIR \
    {PROJECT_DIR}
  ```

The script reads `$PROFILE_DIR/email.json` and `{PROJECT_DIR}/.env`. It silently
skips (exit 0) if email is disabled, the recipient is missing, or no matches
fall in the configured tiers. SMTP errors exit non-zero — log them and continue
to the next profile (don't abort the whole run).

# Step 4 — Summary
After all profiles are done, reply with one line per profile plus a header:
```
Daily digest updated for N profiles:
  • <profile1>: <X> new today, <Y> strong matches across <sources>, <Ki> Indeed enriched, <Kl> LinkedIn enriched
  • <profile2>: ...
```

If any profile was skipped (missing config, placeholder text, etc.), include that as a separate line: `"  • <name>: skipped — <reason>"`.
