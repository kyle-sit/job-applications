# Setup Guide

Follow these steps in order. Most are one-time clicks. Total setup time:
**~30 minutes** if you don't already have the connectors installed.

---

## Step 1 — Drop the folder into your Cowork project directory

Move this entire `job-pipeline/` folder into your Cowork project area. The
default location for Cowork projects is `~/Documents/Claude/Projects/`.

So you should end up with something like:
```
~/Documents/Claude/Projects/job-pipeline/
├── README.md
├── SETUP.md  ← you are here
├── config/
├── pipeline/
└── ...
```

Note the absolute path — you'll need it in Step 8.

---

## Step 2 — Create your local config files from the templates

The personal config files (`profile.md`, `search_queries.json`, `scoring.json`)
are gitignored so each machine keeps its own copy. After cloning the repo, run:

```bash
cd config
cp profile.md.example          profile.md
cp search_queries.json.example search_queries.json
cp scoring.json.example        scoring.json
```

Then open `config/profile.md` and replace the `{{placeholder}}` text with your
own background. Be specific — the pipeline uses this to make smarter rankings
and write better summaries.

---

## Step 3 — Edit `config/search_queries.json`

(You created this from the `.example` template in Step 2.) Open the file and
replace the `REPLACE-WITH-YOUR-ROLE-N` and `REPLACE-WITH-CITY-N` entries with
your actual target roles and locations.

The file has examples for software engineering, marketing, product management,
and data science under `_role_queries_examples`. Copy whichever fits closest
into `role_queries`.

Tip: 5-7 role variants and 2-4 locations is the sweet spot. More locations =
more searches per day = slower run.

---

## Step 4 — Edit `config/scoring.json`

Open the file and review each section. The defaults work for software
engineering — for other fields, pay attention to:

- **`salary_floor`**: minimum acceptable upper-bound annual comp
- **`title.primary_role_substrings`**: exact role names you target
- **`title.tech_keywords`**: skills/tools you bring (Hubspot, Figma, etc.)
- **`title.specialty_groups`**: sub-specialties within your field
- **`location.preferred`**: replace `REPLACE-WITH-CITY-1/2` with your target cities

Each section has a `_about` comment explaining what it does.

---

## Step 5 — Connect Indeed and Dice MCP connectors

In Cowork: open Settings → Connectors. Search for and Connect:
- **Indeed** (required)
- **Dice** (required for tech roles; less useful for other fields — skip if not relevant)

If a connector doesn't apply to your field (e.g. Dice for non-tech), edit
`scheduled-task-prompt-template.md` later to remove that step.

---

## Step 6 — Connect Gmail MCP and set up LinkedIn job alerts

This unlocks LinkedIn coverage. **Skip if you don't want LinkedIn.**

1. **Connect Gmail MCP** in Cowork Settings → Connectors. Authorize inbox-read access.

2. **Create LinkedIn job alerts** for each search you care about:
   - Go to https://www.linkedin.com/jobs
   - Search for a target role + location
   - Apply filters (Date posted: past week, Job type: Full-time)
   - Toggle the **Job alert** switch ON at the top of results
   - Set frequency: **Daily**, delivery: **Email**, click Save
   - Repeat for 3-7 of your most important searches

3. **Create a Gmail filter** to label these emails:
   - In Gmail, click the search-bar dropdown → "Create filter"
   - From: `jobalerts-noreply@linkedin.com`
   - Click "Create filter"
   - Check **Apply the label**, click "New label", name it `linkedin-jobs`
   - Click "Create filter"

The pipeline searches for `label:linkedin-jobs newer_than:1d` to find each day's alerts.

---

## Step 7 — Install Claude in Chrome (recommended for LinkedIn)

Without Chrome, LinkedIn jobs appear in your digest but with no salary or
description. With Chrome, they're enriched alongside Indeed/Dice.

1. Install the **Claude in Chrome** extension from the Chrome Web Store
   (search "Claude" or visit https://www.anthropic.com/claude-in-chrome)
2. Click the Claude extension icon in your Chrome toolbar
3. Sign in with the **same Anthropic account** you use for Cowork
4. Make sure Chrome is running before each daily pipeline run

---

## Step 8 — Run the install prompt

Open `INSTALL_PROMPT.md` in this folder. Copy the prompt block, paste it into
Cowork, and replace the `[PASTE THE ABSOLUTE PATH...]` line with the actual
path to your `job-pipeline/` folder.

Claude will:
- Verify your config files are personalized (no placeholder text remaining)
- Discover your connector UUIDs
- Create a scheduled task running at 8 AM local daily
- Tell you to click "Run now" once to pre-approve tool permissions

---

## Step 9 — First manual run

In Cowork's Scheduled section, find `daily-job-search` and click **Run now**.
Approve any tool permission prompts. Once it finishes, you'll have your first
digest at:
```
job-pipeline/digest.md
```

---

## Tuning after the first few digests

After 2-3 days of runs, look at what's landing in each tier and tune
`config/scoring.json`:
- Too many results? Raise `salary_floor` or tier thresholds.
- Wrong jobs in Strong tier? Adjust `title.tech_keywords` or `senior_tokens`.
- Missing your favorite cities? Add them to `location.preferred`.

The pipeline picks up config changes on the next run — no restart needed.

---

## Troubleshooting

- **"Daily digest updated: 0 new today"** — pipeline ran but found nothing
  new. Check the digest header for raw → unique → above-floor counts. If raw
  is 0, your search queries probably don't match anything; adjust them.

- **LinkedIn descriptions empty** — Chrome wasn't running at run time, or the
  extension isn't signed in. Check Chrome status and re-run.

- **Indeed/Dice rate-limited** — drop a `sleep 30` between batches in the
  scheduled task prompt, or reduce role/location counts.

- **Scoring feels off** — check `config/scoring.json`. Each scoring axis has
  its own section. Tweak one knob at a time and re-run to see the effect.
