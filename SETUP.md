# Setup Guide

Follow these steps in order. Most are one-time clicks. Total setup time:
**~30 minutes** if you don't already have the connectors installed.

The pipeline runs a digest **per profile**. Profiles live under
`profiles/<name>/` and are independent — different roles, different scoring,
optionally different LinkedIn alerts. Set up your first profile here, then
repeat Steps 2–4 for each additional person.

---

## Step 1 — Drop the folder into your Cowork project directory

Move this entire `job-pipeline/` folder into your Cowork project area. The
default location for Cowork projects is `~/Documents/Claude/Projects/`.

So you should end up with something like:
```
~/Documents/Claude/Projects/JobApps/job-pipeline/
├── README.md
├── SETUP.md  ← you are here
├── pipeline/
├── profiles/
│   └── _template/    ← templates you'll copy from
└── ...
```

Note the absolute path — you'll need it in Step 8.

---

## Step 2 — Create your first profile from the template

Profile content is gitignored so each machine keeps its own copy. Bootstrap
your first profile (replace `kyle` with whatever name you want):

```bash
cd profiles
mkdir kyle
cp _template/profile.md.example          kyle/profile.md
cp _template/search_queries.json.example kyle/search_queries.json
cp _template/scoring.json.example        kyle/scoring.json
cp _template/linkedin.json.example       kyle/linkedin.json
```

Then open `profiles/kyle/profile.md` and replace the `{{placeholder}}` text
with your own background. Be specific — the pipeline uses this to make smarter
rankings and write better summaries.

---

## Step 3 — Edit `profiles/kyle/search_queries.json`

(You created this from the `.example` template in Step 2.) Open the file and
replace the `REPLACE-WITH-YOUR-ROLE-N` and `REPLACE-WITH-CITY-N` entries with
your actual target roles and locations.

The file has examples for software engineering, marketing, product management,
and data science under `_role_queries_examples`. Copy whichever fits closest
into `role_queries`.

Tip: 5-7 role variants and 2-4 locations is the sweet spot. More locations =
more searches per day = slower run.

---

## Step 4 — Edit `profiles/kyle/scoring.json`

Open the file and review each section. The defaults work for software
engineering — for other fields, pay attention to:

- **`salary_floor`**: minimum acceptable upper-bound annual comp
- **`title.primary_role_substrings`**: exact role names you target
- **`title.tech_keywords`**: skills/tools you bring (Hubspot, Figma, etc.)
- **`title.specialty_groups`**: sub-specialties within your field
- **`location.preferred`**: replace `REPLACE-WITH-CITY-1/2` with your target cities
- **`recency.max_days_by_source`**: hard cutoffs (default Indeed≤7d)

Each section has a `_about` comment explaining what it does.

---

## Step 5 — Connect the Indeed MCP connector

In Cowork: open Settings → Connectors. Search for and Connect:
- **Indeed** (required)

The same Indeed MCP is shared by every profile — no per-profile connector
setup is needed for it.

---

## Step 6 — Connect Gmail MCP and configure LinkedIn alerts (per-profile)

This unlocks LinkedIn coverage. Skip if you don't want LinkedIn — leave
`profiles/kyle/linkedin.json`'s `enabled` set to `false`.

1. **Connect Gmail MCP** in Cowork Settings → Connectors. Authorize inbox-read access.

2. **Create LinkedIn job alerts** for each search you care about:
   - Go to https://www.linkedin.com/jobs
   - Search for a target role + location
   - Apply filters (Date posted: past week, Job type: Full-time)
   - Toggle the **Job alert** switch ON at the top of results
   - Set frequency: **Daily**, delivery: **Email**, click Save
   - Repeat for 3-7 of your most important searches

3. **Create a Gmail filter** to label these emails with a per-profile label:
   - In Gmail, click the search-bar dropdown → "Create filter"
   - From: `jobalerts-noreply@linkedin.com`
   - Click "Create filter"
   - Check **Apply the label**, click "New label", name it something like
     `linkedin-jobs-kyle` (use a per-profile label so multiple profiles can
     coexist in one Gmail account)
   - Click "Create filter"

4. **Edit `profiles/kyle/linkedin.json`**:
   ```json
   {
     "enabled": true,
     "gmail_label": "linkedin-jobs-kyle",
     "gmail_mcp_id": null
   }
   ```
   The pipeline will search for `label:linkedin-jobs-kyle newer_than:1d`.

For multiple profiles sharing one Gmail account, give each profile its own
label. If you've installed multiple Gmail MCP connectors (each authorized to
a different Google account), set `gmail_mcp_id` per profile to the right UUID.

---

## Step 7 — Install Claude in Chrome (recommended for LinkedIn)

Without Chrome, LinkedIn jobs appear in your digest but with no salary or
description. With Chrome, they're enriched alongside Indeed listings.

1. Install the **Claude in Chrome** extension from the Chrome Web Store
   (search "Claude" or visit https://www.anthropic.com/claude-in-chrome)
2. Click the Claude extension icon in your Chrome toolbar
3. Sign in with the **same Anthropic account** you use for Cowork
4. Make sure Chrome is running before each daily pipeline run

The Chrome session is shared across profiles — only one sign-in needed.

### Note: LinkedIn fetch + enrichment run as sub-agents

Steps 3c (Gmail) and 3d (Chrome enrichment) of the daily run are dispatched
to a sub-agent so the bulky thread bodies and page text never enter the
main assistant's 200K context window. The sub-agent prompts live at:
- `pipeline/agent_prompts/linkedin_fetch_subagent.md`
- `pipeline/agent_prompts/linkedin_enrich_subagent.md`

This is purely a context-window optimization — no extra setup, no new
credentials, no behavior difference visible from the digest. If you ever
need to debug the LinkedIn flow, run those prompts manually against a
profile by reading the file, doing the placeholder substitution shown at
the top, and invoking the sub-agent.

---

## Step 8 — Run the install prompt

Open `INSTALL_PROMPT.md` in this folder. Copy the prompt block, paste it into
Cowork, and replace the `[PASTE THE ABSOLUTE PATH...]` line with the actual
path to your `job-pipeline/` folder.

Claude will:
- Verify each profile's configs are personalized (no placeholder text remaining)
- Discover your connector UUIDs
- Create a scheduled task running at 8 AM local daily
- Tell you to click "Run now" once to pre-approve tool permissions

---

## Step 9 — First manual run

In Cowork's Scheduled section, find `daily-job-search` and click **Run now**.
Approve any tool permission prompts. Once it finishes, each profile gets its
own digest at:
```
profiles/<name>/digest.md
```

---

## Adding more profiles later

For each new person:

```bash
cd profiles
mkdir <name>
cp _template/profile.md.example          <name>/profile.md
cp _template/search_queries.json.example <name>/search_queries.json
cp _template/scoring.json.example        <name>/scoring.json
cp _template/linkedin.json.example       <name>/linkedin.json
```

Personalize the four files (Steps 2–4 again, plus Step 6 if they want LinkedIn).
The next scheduled run will pick up the new profile automatically — no code or
task changes needed.

---

## Tuning after the first few digests

After 2-3 days of runs, look at what's landing in each tier and tune that
profile's `scoring.json`:
- Too many results? Raise `salary_floor` or tier thresholds.
- Wrong jobs in Strong tier? Adjust `title.tech_keywords` or `senior_tokens`.
- Missing your favorite cities? Add them to `location.preferred`.
- Stale jobs leaking through? Tighten `recency.max_days_by_source`.

The pipeline picks up config changes on the next run — no restart needed.

---

## Troubleshooting

- **"Daily digest updated: 0 new today"** — pipeline ran but found nothing
  new for that profile. Check the digest header for raw → unique → above-floor
  → within-recency counts. If raw is 0, the search queries probably don't
  match anything; adjust them.

- **LinkedIn descriptions empty** — Chrome wasn't running at run time, or the
  extension isn't signed in. Check Chrome status and re-run.

- **Indeed rate-limited** — Indeed historically tolerates parallel bursts.
  If you start seeing rate-limit errors, fall back to a serial-with-`sleep 1`
  pattern in Step 3b of the template, or reduce role/location counts.

- **Scoring feels off** — check `profiles/<name>/scoring.json`. Each scoring
  axis has its own section. Tweak one knob at a time and re-run to see the effect.

- **Profile being skipped** — the daily task skips profiles whose
  `search_queries.json` still contains `REPLACE-WITH-` placeholder text.
  Personalize it to enable.
