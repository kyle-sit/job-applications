# Install Prompt — Paste this to Claude in your Cowork

After you've done the manual setup steps in `SETUP.md` (connected MCPs, set up
LinkedIn alerts, edited config files, dropped this folder into your project
directory), open Cowork and paste the prompt below into a fresh chat. Claude
will discover your connector UUIDs, fill in the placeholders, and create the
scheduled task for you.

---

## The prompt to paste:

```
I've installed the job pipeline at this path:
[PASTE THE ABSOLUTE PATH TO YOUR job-pipeline FOLDER, e.g. /Users/jane/Documents/Claude/Projects/JobApps]

Please set up the daily scheduled task for me:

1. Read the template at <path>/scheduled-task-prompt-template.md
2. Discover my connector UUIDs by listing my installed MCP connectors and finding
   Indeed, Dice, and Gmail. The UUIDs look like a-b-c-d-e formatted strings.
3. Read my config files (config/profile.md, config/search_queries.json,
   config/scoring.json) to confirm I've personalized them — if they still
   have REPLACE-WITH or {{placeholder}} text, stop and tell me which file
   needs editing before continuing.
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
  Gmail is required if you want LinkedIn coverage. Without Gmail, LinkedIn
  steps will be skipped silently.

- **Have you personalized your config?** Claude will look for placeholder text
  like `REPLACE-WITH-YOUR-ROLE-1` in `search_queries.json`, `{{your current
  job title}}` in `profile.md`, and `REPLACE-WITH-CITY-1` in `scoring.json`.
  If any are still there, you'll be asked to fix them first.

- **Is Claude in Chrome installed and signed in?** Optional but strongly
  recommended for LinkedIn. Without it, LinkedIn jobs will appear in the
  digest but without descriptions or salary signal.
