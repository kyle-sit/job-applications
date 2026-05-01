# Job Pipeline

A daily, multi-source job-search pipeline that runs in Cowork. Pulls fresh
listings from Indeed, Dice, and LinkedIn email alerts, enriches them with full
descriptions and salary data, scores them against your background, and writes
a ranked digest you read each morning.

## What it does

Every day at 8 AM (configurable):

1. **Searches Indeed and Dice** in parallel for every (role × location) combination
   you care about.
2. **Reads your LinkedIn job-alert emails** from Gmail — no scraping, just the
   alerts LinkedIn already sends you.
3. **Opens each LinkedIn job in Chrome** to extract salary, applicant count, and
   the actual job description (using your own logged-in browser session).
4. **Dedupes, filters, and scores** every listing using rules you set in
   `config/scoring.json`.
5. **Writes a ranked digest** as Markdown — Strong Matches, Worth a Look, Lower
   Priority — with a one-paragraph summary inline for top matches.
6. **Tracks new listings** across runs so each digest highlights what's actually
   new today.

## What you need

- **Cowork** (Claude desktop app)
- **Indeed MCP connector** — required
- **Dice MCP connector** — required for tech roles, optional otherwise
- **Gmail MCP connector** — required if you want LinkedIn coverage
- **Claude in Chrome browser extension** — optional but strongly recommended (gets
  rich LinkedIn data)

## Quick start

Read `SETUP.md`. Plan on ~30 minutes the first time.

## Files

```
job-pipeline/
├── README.md                          ← you are here
├── SETUP.md                           ← step-by-step setup guide
├── INSTALL_PROMPT.md                  ← paste this to Claude after manual setup
├── scheduled-task-prompt-template.md  ← the daily run prompt template
├── config/
│   ├── profile.md                     ← your background / target roles
│   ├── search_queries.json            ← role × location matrix
│   └── scoring.json                   ← tunable scoring rules
├── pipeline/
│   ├── parse_and_score.py             ← scores and ranks
│   ├── dice_normalizer.py             ← Dice JSON → markdown
│   ├── linkedin_parser.py             ← LinkedIn email → markdown
│   ├── enrich_linkedin_md.py          ← apply Chrome enrichments to LinkedIn markdown
│   ├── splice_enrichments.py          ← splice descriptions into digest
│   ├── hn_fetcher.py                  ← Hacker News (dormant, see notes in SETUP.md)
│   └── PIPELINE_INSTRUCTIONS.md       ← reference doc on the pipeline architecture
├── data/                              ← daily raw fetches accumulate here (gitignore-able)
└── digest_archive/                    ← daily digests archived here
```

## How customizable is this?

- **Scoring**: every weight, threshold, keyword, and tier is in `config/scoring.json`.
  No code editing required.
- **Search queries**: `config/search_queries.json`.
- **Schedule**: change the cron expression in your scheduled task (default `0 8 * * *`).
- **Sources**: remove or add steps in your scheduled task prompt. Each source is
  independent — pipeline still works if any individual one is missing.

## License

Use it however you want.

## Credit

Built collaboratively in Cowork. The pipeline architecture is sound but the
scoring rules are opinionated — expect to tune `scoring.json` over the first
week as you see what lands in each tier.
