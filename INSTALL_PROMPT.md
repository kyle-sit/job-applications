# Install Prompt — Paste this to Claude in your Cowork

After you've done the manual setup steps in `SETUP.md` (connected MCPs, set up
LinkedIn alerts and per-profile labels, edited each profile's config files,
dropped this folder into your project directory), open Cowork and paste the
prompt below into a fresh chat. Claude will discover your connector UUIDs,
fill in the placeholders, and create the scheduled task for you.

---

## The prompt to paste:

```
I've installed the job pipeline at this path:
[PASTE THE ABSOLUTE PATH TO YOUR job-pipeline FOLDER, e.g. /Users/jane/Documents/Claude/Projects/JobApps/job-pipeline]

Please set up the daily scheduled task for me:

1. Read the template at <path>/scheduled-task-prompt-template.md
2. Discover my connector UUIDs by listing my installed MCP connectors and finding
   Indeed, Dice, and Gmail. The UUIDs look like a-b-c-d-e formatted strings.
3. List the profile dirs under <path>/profiles/ (skip _template and any name
   starting with `_` or `.`). For each profile:
   a. Read profile.md, search_queries.json, scoring.json, and linkedin.json
      (linkedin.json may be missing — that means LinkedIn is off for that profile).
   b. Confirm placeholders are gone. If search_queries.json still has
      REPLACE-WITH text or profile.md still has {{placeholder}} text, stop and
      tell me which profile + which file needs editing before continuing.
4. Replace {PROJECT_DIR}, {INDEED_MCP_ID}, {DICE_MCP_ID}, {GMAIL_MCP_ID} in
   the template with the discovered values.
5. Create a scheduled task named "daily-job-search" with the resolved prompt,
   running at 8:00 AM local time daily.
6. Tell me what to verify and recommend I click "Run now" once to pre-approve
   tool permissions.
```

---

## What Claude will check before installing

- **Have you connected the required MCPs?** Indeed and Dice are required.
  Gmail is required if any profile wants LinkedIn coverage. Without Gmail,
  LinkedIn steps are skipped silently for every profile.

- **Have you personalized each profile's config?** Claude will look for
  placeholder text like `REPLACE-WITH-YOUR-ROLE-1` in `search_queries.json`
  and `{{your current job title}}` in `profile.md`, in **every** profile dir.
  If any profile has unfinished templates, you'll be asked to fix them first.

- **Is Claude in Chrome installed and signed in?** Optional but strongly
  recommended for LinkedIn. Without it, LinkedIn jobs will appear in the
  digest but without descriptions or salary signal.

- **Per-profile LinkedIn labels:** if multiple profiles have LinkedIn enabled,
  Claude will sanity-check that each `linkedin.json` uses a distinct
  `gmail_label` so they don't pull each other's alerts.
