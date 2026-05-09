# Job Pipeline

A daily, multi-source job-search pipeline that runs in Cowork. Pulls fresh
listings from Indeed and LinkedIn email alerts, enriches them with full
descriptions and salary data, scores them against each profile's background,
and writes a ranked digest per profile that you read each morning.

Supports **multiple profiles** — one Cowork install can run separate digests
for you, your spouse, friends, etc. Each profile has its own roles, scoring,
and (optionally) LinkedIn alerts.

## What it does

Every day at 8 AM (configurable), for each profile under `profiles/`:

1. **Searches Indeed** in parallel for every (role × location) combination
   that profile cares about.
2. **Reads that profile's LinkedIn job-alert emails** from Gmail using a
   per-profile label (e.g. `linkedin-jobs-alice`) — no scraping, just the
   alerts LinkedIn already sends.
3. **Opens each LinkedIn job in Chrome** to extract salary, applicant count, and
   the actual job description (using your own logged-in browser session).
4. **Dedupes, filters, and scores** every listing using rules in that profile's
   `scoring.json`. Hard recency cutoff (Indeed≤7d by default) drops stale
   listings before scoring.
5. **Writes a ranked digest** per profile as Markdown — Strong Matches, Worth
   a Look, Lower Priority — with a one-paragraph summary inline for top matches.
6. **Tracks new listings** across runs (per profile) so each digest highlights
   what's actually new today.

## What you need

- **Cowork** (Claude desktop app)
- **Indeed MCP connector** — required
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
├── SYNC.md                            ← two-machine git workflow
├── INSTALL_PROMPT.md                  ← paste this to Claude after manual setup
├── scheduled-task-prompt-template.md  ← the daily run prompt template (loops profiles)
├── profiles/
│   ├── _template/                     ← templates copied when bootstrapping a new profile
│   │   ├── profile.md.example
│   │   ├── search_queries.json.example
│   │   ├── scoring.json.example
│   │   └── linkedin.json.example
│   └── <name>/                        ← per-profile content (gitignored)
│       ├── profile.md                 ← that person's background / target roles
│       ├── search_queries.json        ← role × location matrix
│       ├── scoring.json               ← tunable scoring rules
│       ├── linkedin.json              ← LinkedIn enable + Gmail label
│       ├── data/                      ← raw fetches + seen_jobs.json
│       ├── digest.md                  ← latest digest
│       └── digest_archive/            ← daily digests archived
└── pipeline/
    ├── parse_and_score.py             ← scores and ranks
    ├── linkedin_parser.py             ← LinkedIn email → markdown
    ├── enrich_linkedin_md.py          ← apply Chrome enrichments to LinkedIn markdown
    ├── splice_enrichments.py          ← splice descriptions into digest
    ├── hn_fetcher.py                  ← Hacker News (dormant, see notes in SETUP.md)
    └── PIPELINE_INSTRUCTIONS.md       ← reference doc on the pipeline architecture
```

## How customizable is this?

- **Profiles**: add as many as you want. `mkdir profiles/<name>` and copy from
  `profiles/_template/`. The next scheduled run picks them up automatically.
- **Scoring**: every weight, threshold, keyword, tier, and recency cutoff is
  in each profile's `scoring.json`. No code editing required.
- **Search queries**: each profile's `search_queries.json`.
- **LinkedIn per profile**: each profile's `linkedin.json` controls whether
  LinkedIn runs for that profile and which Gmail label to filter on.
- **Schedule**: change the cron expression in your scheduled task (default `0 8 * * *`).
- **Sources**: remove or add steps in your scheduled task prompt. Each source
  is independent — pipeline still works if any individual one is missing.

## License

Use it however you want.

## Credit

Built collaboratively in Cowork. The pipeline architecture is sound but the
scoring rules are opinionated — expect to tune `scoring.json` over the first
week as you see what lands in each tier.
