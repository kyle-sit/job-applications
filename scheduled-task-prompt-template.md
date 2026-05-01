# Scheduled Task Prompt — Template

This is the prompt body for the daily Cowork scheduled task. It runs once a day
via cron, fetches all sources, scores, enriches, and writes the digest.

When setting up, fill in three placeholders:
- `{PROJECT_DIR}` — absolute path to your project folder
  (e.g. `/Users/jane/Documents/Claude/Projects/JobApps`)
- `{INDEED_MCP_ID}` — the Indeed connector's MCP UUID for your Cowork install
- `{DICE_MCP_ID}` — the Dice connector's MCP UUID
- `{GMAIL_MCP_ID}` — the Gmail connector's MCP UUID

(The companion `INSTALL_PROMPT.md` tells Claude how to discover these for you.)

---

Run the daily multi-source job search pipeline.

# Step 1 — Get today's date
Run `date +%Y-%m-%d` via bash. Use the result as TODAY (e.g. "2026-05-01").

# Step 2 — Indeed searches
Read `{PROJECT_DIR}/config/search_queries.json` for `role_queries` and `locations`.

For every (role × location) pair, call `mcp__{INDEED_MCP_ID}__search_jobs` in parallel:
  - `search`: the role string
  - `location`: the location string
  - `country_code`: from config (default "US")
  - `job_type`: from config (default "fulltime")

Concatenate every search response's "result" field (with blank lines between) and Write to:
  `{PROJECT_DIR}/data/raw_searches/{TODAY}.txt`

# Step 3 — Dice searches
Same role × location matrix. For each call to `mcp__{DICE_MCP_ID}__search_jobs`:
- For specific cities: pass `keyword`, `location`, `employment_types=["FULLTIME"]`, `jobs_per_page=15`
- For "remote" locations: pass `keyword`, `employment_types=["FULLTIME"]`, `workplace_types=["Remote"]`, `jobs_per_page=15` (omit location)

Stay under Dice's 200 req/min limit — split into two batches with `sleep 30` between if needed.

Combine all Dice responses into one JSON: `{"data": [...all items...]}`. Write to:
  `{PROJECT_DIR}/data/dice_raw/{TODAY}.json`

Then normalize:
  ```bash
  python3 {PROJECT_DIR}/pipeline/dice_normalizer.py \
    {PROJECT_DIR}/data/dice_raw/{TODAY}.json \
    {PROJECT_DIR}/data/dice_raw/{TODAY}.txt
  ```

If Dice errors, skip silently.

# Step 4 — LinkedIn email alerts (parse the digests into our markdown structure)
Call `mcp__{GMAIL_MCP_ID}__search_threads` with:
  - `query="label:linkedin-jobs newer_than:1d"`
  - `pageSize=50`

For every thread returned, call `mcp__{GMAIL_MCP_ID}__get_thread` with `messageFormat="FULL_CONTENT"` and extract `messages[].plaintextBody`.

Concatenate all plaintextBody values, separated by blank lines. Write to:
  `{PROJECT_DIR}/data/linkedin_raw/{TODAY}.txt`

Then run the parser:
  ```bash
  python3 {PROJECT_DIR}/pipeline/linkedin_parser.py \
    {PROJECT_DIR}/data/linkedin_raw/{TODAY}.txt \
    {PROJECT_DIR}/data/linkedin_raw/{TODAY}_normalized.txt \
    {TODAY}
  ```

This also emits a sidecar JSON list at `{TODAY}_normalized.jobs.json`.

If no threads found or Gmail errors, skip the rest of LinkedIn and continue.

# Step 5 — LinkedIn Chrome enrichment (PRE-SCORE)
Critical step: pulls salary + description from each LinkedIn page so the scorer
in Step 6 can rank fairly.

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

Write to `/tmp/linkedin_chrome_enrichments_{TODAY}.json`.

After the loop, close the tab via `mcp__Claude_in_Chrome__tabs_close_mcp`.

Apply the enrichments to the LinkedIn markdown:
  ```bash
  python3 {PROJECT_DIR}/pipeline/enrich_linkedin_md.py \
    {PROJECT_DIR}/data/linkedin_raw/{TODAY}_normalized.txt \
    /tmp/linkedin_chrome_enrichments_{TODAY}.json
  ```

# Step 6 — Run the parser/scorer with all available sources
Build the input file list dynamically — only pass paths that exist:

  ```bash
  python3 {PROJECT_DIR}/pipeline/parse_and_score.py \
    {PROJECT_DIR}/digest.md \
    {PROJECT_DIR}/data/seen_jobs.json \
    {PROJECT_DIR}/data/raw_searches/{TODAY}.txt \
    {PROJECT_DIR}/data/dice_raw/{TODAY}.txt \
    {PROJECT_DIR}/data/linkedin_raw/{TODAY}_normalized.txt
  ```

The parser auto-loads scoring rules from `config/scoring.json` and detects source from each job_id prefix.

This step writes:
  - `{PROJECT_DIR}/needs_enrichment.json` — Indeed strong matches needing get_job_details
  - `{PROJECT_DIR}/needs_enrichment_linkedin.json` — LinkedIn jobs that didn't get a summary in Step 5

# Step 7 — Enrich Indeed strong matches with full descriptions
Read `needs_enrichment.json`. For each entry (cap at 15):
  - Call `mcp__{INDEED_MCP_ID}__get_job_details` with the entry's `job_id`.
  - Write a 2-3 sentence factual summary capturing what the team does, key skills/seniority, distinctive scope. Avoid company boilerplate. Under 350 chars.
  - Build dict: `{ <hash>: <summary_text> }`

Write to `/tmp/job_enrichments_indeed_{TODAY}.json`.

# Step 8 — LinkedIn fallback enrichment
Read `needs_enrichment_linkedin.json`. If empty or missing, skip.
Otherwise re-run the same Chrome flow as Step 5 for these stragglers, building summaries.
Write to `/tmp/job_enrichments_linkedin_{TODAY}.json`.

# Step 9 — Splice all post-score enrichments
  ```bash
  python3 -c "import json,os; a=json.load(open('/tmp/job_enrichments_indeed_{TODAY}.json')) if os.path.exists('/tmp/job_enrichments_indeed_{TODAY}.json') else {}; b=json.load(open('/tmp/job_enrichments_linkedin_{TODAY}.json')) if os.path.exists('/tmp/job_enrichments_linkedin_{TODAY}.json') else {}; json.dump({**a, **b}, open('/tmp/job_enrichments_{TODAY}.json', 'w'))"

  python3 {PROJECT_DIR}/pipeline/splice_enrichments.py \
    {PROJECT_DIR}/digest.md \
    /tmp/job_enrichments_{TODAY}.json
  ```

# Step 10 — Archive
  ```bash
  cp {PROJECT_DIR}/digest.md \
     {PROJECT_DIR}/digest_archive/{TODAY}.md
  ```

# Step 11 — Summary
Reply with one line: `"Daily digest updated: <N> new today, <M> strong matches across <sources>, <Ki> Indeed enriched, <Kl> LinkedIn enriched"`.
