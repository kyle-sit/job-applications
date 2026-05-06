# Scheduled Task Prompt — Template (multi-profile)

This is the prompt body for the daily Cowork scheduled task. It runs once a day
via cron, fetches all sources for every profile, scores, enriches, and writes
a digest per profile.

When setting up, fill in three placeholders:
- `{PROJECT_DIR}` — absolute path to your project folder
  (e.g. `/Users/jane/Documents/Claude/Projects/JobApps/job-pipeline`)
- `{INDEED_MCP_ID}` — the Indeed connector's MCP UUID for your Cowork install
- `{DICE_MCP_ID}` — the Dice connector's MCP UUID
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

# Step 3 — For EACH profile, run Steps 3a–3j sequentially

Process profiles one at a time. Between profiles, sleep 30 seconds before the
next profile's Dice calls to stay well under Dice's 200-req/min limit.

Define `PROFILE_DIR={PROJECT_DIR}/profiles/<profile>` and create needed
subdirs once per profile:
```bash
mkdir -p $PROFILE_DIR/data/raw_searches $PROFILE_DIR/data/dice_raw \
         $PROFILE_DIR/data/linkedin_raw $PROFILE_DIR/digest_archive
```

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

Concatenate every search response's "result" field (with blank lines between) and Write to:
  `$PROFILE_DIR/data/raw_searches/{TODAY}.txt`

## Step 3c — Dice searches (this profile)
Same role × location matrix. For each call to `mcp__{DICE_MCP_ID}__search_jobs`:
- For specific cities: pass `keyword`, `location`, `employment_types=["FULLTIME"]`, `posted_date="THREE"`, `jobs_per_page=15`
- For "remote" locations: pass `keyword`, `employment_types=["FULLTIME"]`, `workplace_types=["Remote"]`, `posted_date="THREE"`, `jobs_per_page=15` (omit location)

Stay under Dice's 200 req/min limit — split into two batches with `sleep 30` between if needed.

`posted_date="THREE"` bounds Dice to listings posted in the last 3 days.
`parse_and_score.py` also enforces a recency backstop (see `recency.max_days_by_source`
in scoring.json) — keep both in sync if you change the window.

Combine all Dice responses into one JSON: `{"data": [...all items...]}`. Write to:
  `$PROFILE_DIR/data/dice_raw/{TODAY}.json`

Then normalize:
  ```bash
  python3 {PROJECT_DIR}/pipeline/dice_normalizer.py \
    $PROFILE_DIR/data/dice_raw/{TODAY}.json \
    $PROFILE_DIR/data/dice_raw/{TODAY}.txt
  ```

If Dice errors, skip silently for this profile.

## Step 3d — LinkedIn email alerts (this profile, gated by linkedin.json)
Read `$PROFILE_DIR/linkedin.json` if it exists. If the file is missing, or
`enabled` is `false`, or `gmail_labels` is empty, skip Steps 3d and 3e for this profile.

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

If no threads found or Gmail errors, skip Step 3e and continue.

## Step 3e — LinkedIn Chrome enrichment (PRE-SCORE, this profile)
Critical step: pulls salary + description from each LinkedIn page so the scorer
in Step 3f can rank fairly.

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

## Step 3f — Run the parser/scorer with all available sources (this profile)
Build the input file list dynamically — only pass paths that exist:

  ```bash
  python3 {PROJECT_DIR}/pipeline/parse_and_score.py \
    $PROFILE_DIR/digest.md \
    $PROFILE_DIR/data/seen_jobs.json \
    $PROFILE_DIR/data/raw_searches/{TODAY}.txt \
    $PROFILE_DIR/data/dice_raw/{TODAY}.txt \
    $PROFILE_DIR/data/linkedin_raw/{TODAY}_normalized.txt
  ```

The parser auto-loads scoring rules from `$PROFILE_DIR/scoring.json` and detects source from each job_id prefix.

This step writes:
  - `$PROFILE_DIR/needs_enrichment.json` — Indeed strong matches needing get_job_details
  - `$PROFILE_DIR/needs_enrichment_linkedin.json` — LinkedIn jobs that didn't get a summary in Step 3e

## Step 3g — Enrich Indeed strong matches with full descriptions (this profile)
Read `$PROFILE_DIR/needs_enrichment.json`. For each entry (cap at 15):
  - Call `mcp__{INDEED_MCP_ID}__get_job_details` with the entry's `job_id`.
  - Write a 2-3 sentence factual summary capturing what the team does, key skills/seniority, distinctive scope. Avoid company boilerplate. Under 350 chars.
  - Build dict: `{ <hash>: <summary_text> }`

Write to `/tmp/<profile>_job_enrichments_indeed_{TODAY}.json`.

## Step 3h — LinkedIn fallback enrichment (this profile)
Read `$PROFILE_DIR/needs_enrichment_linkedin.json`. If empty or missing, skip.
Otherwise re-run the same Chrome flow as Step 3e for these stragglers, building summaries.
Write to `/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json`.

## Step 3i — Splice all post-score enrichments (this profile)
  ```bash
  python3 -c "import json,os; \
    a=json.load(open('/tmp/<profile>_job_enrichments_indeed_{TODAY}.json')) if os.path.exists('/tmp/<profile>_job_enrichments_indeed_{TODAY}.json') else {}; \
    b=json.load(open('/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json')) if os.path.exists('/tmp/<profile>_job_enrichments_linkedin_{TODAY}.json') else {}; \
    json.dump({**a, **b}, open('/tmp/<profile>_job_enrichments_{TODAY}.json', 'w'))"

  python3 {PROJECT_DIR}/pipeline/splice_enrichments.py \
    $PROFILE_DIR/digest.md \
    /tmp/<profile>_job_enrichments_{TODAY}.json
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

## Between profiles
After completing 3a–3j for one profile, sleep 30 seconds before starting the
next profile's Step 3c (Dice rate-limit safety):
  ```bash
  sleep 30
  ```

# Step 4 — Summary
After all profiles are done, reply with one line per profile plus a header:
```
Daily digest updated for N profiles:
  • <profile1>: <X> new today, <Y> strong matches across <sources>, <Ki> Indeed enriched, <Kl> LinkedIn enriched
  • <profile2>: ...
```

If any profile was skipped (missing config, placeholder text, etc.), include that as a separate line: `"  • <name>: skipped — <reason>"`.
