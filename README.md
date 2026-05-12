# Job Pipeline

A daily, multi-source job-search pipeline that runs in Cowork. Pulls fresh
listings from Indeed and LinkedIn email alerts, enriches them with full
descriptions and salary data, scores them against each profile's background,
and emails a ranked digest per profile that lands in your inbox each morning.

Supports **multiple profiles** — one Cowork install can run separate digests
for you, your spouse, friends, etc. Each profile has its own roles, scoring,
LinkedIn alerts, and email delivery settings.

## What it does

Every day at 8 AM (configurable), for each profile under `profiles/`:

1. **Searches Indeed** for every (role × location) combination that profile
   cares about. Calls are serialized with a short sleep to stay under
   Indeed's per-account rate limiter, and any individual call that 429s
   gets retried with exponential backoff before being skipped.
2. **Reads that profile's LinkedIn job-alert emails** from Gmail using
   per-profile labels (e.g. `linkedin-jobs-alice`) — no scraping, just the
   alerts LinkedIn already sends.
3. **Opens each LinkedIn job in Chrome** to extract salary, applicant count,
   and the actual job description (using your own logged-in browser session).
4. **Dedupes, filters, and scores** every listing using rules in that
   profile's `scoring.json`. Hard recency cutoff (Indeed≤7d by default)
   drops stale listings before scoring.
5. **Writes a ranked digest** per profile as Markdown — Strong Matches,
   Worth a Look, Lower Priority — with a one-paragraph summary inline for
   top matches. Then **emails it** via Gmail draft + Chrome click-Send
   (per-profile, gated by `email.json`).
6. **Caches each Strong-tier description** in `enrichment_cache.json` so
   repeat listings across days don't re-spend Indeed API quota or Chrome
   page fetches.
7. **Tracks new listings** across runs (per profile) so each digest
   highlights what's actually new today.

Steps 2 and 3 run as sub-agents so the bulky Gmail thread bodies and
LinkedIn page text never enter the parent assistant's context window —
that's what makes a multi-profile run with full enrichment fit comfortably
under the 200K-token ceiling.

## What you need

- **Cowork** (Claude desktop app)
- **Indeed MCP connector** — required
- **Gmail MCP connector** — required for LinkedIn coverage and for the
  daily-digest email (used both to fetch alert emails and to stage the
  outgoing digest as a Gmail draft)
- **Claude in Chrome browser extension** — strongly recommended. Without
  it, LinkedIn listings land in the digest with no salary or description,
  and the daily email gets staged as a draft you'd have to click Send on
  manually. With it, both happen automatically.

## Quick start

Read `SETUP.md`. Plan on ~30 minutes the first time.

## Files

```
job-pipeline/
├── README.md                          ← you are here
├── SETUP.md                           ← step-by-step setup guide
├── SYNC.md                            ← two-machine git workflow
├── INSTALL_PROMPT.md                  ← paste this to Claude after manual setup
├── scheduled-task-prompt-template.md  ← daily run prompt template (loops profiles)
├── profiles/
│   ├── _template/                     ← templates copied when bootstrapping a new profile
│   │   ├── profile.md.example
│   │   ├── search_queries.json.example
│   │   ├── scoring.json.example
│   │   ├── linkedin.json.example
│   │   └── email.json.example
│   └── <name>/                        ← per-profile content (gitignored)
│       ├── profile.md                 ← that person's background / target roles
│       ├── search_queries.json        ← role × location matrix
│       ├── scoring.json               ← tunable scoring rules
│       ├── linkedin.json              ← LinkedIn enable + Gmail labels
│       ├── email.json                 ← digest delivery (recipient + tiers)
│       ├── data/                      ← raw fetches, seen_jobs.json, enrichment_cache.json
│       ├── digest.md                  ← latest digest
│       └── digest_archive/            ← daily digests archived
└── pipeline/
    ├── parse_and_score.py             ← scores and ranks; manages enrichment cache
    ├── linkedin_parser.py             ← LinkedIn email → markdown
    ├── enrich_linkedin_md.py          ← apply Chrome enrichments to LinkedIn markdown
    ├── splice_enrichments.py          ← splice descriptions into digest
    ├── apply_profile_fit.py           ← Strong-tier re-rank by profile narrative fit
    ├── prepare_digest_email.py        ← render digest as a Gmail-MCP draft payload
    ├── update_enrichment_cache.py     ← merge fresh enrichments into per-profile cache
    ├── agent_prompts/                 ← sub-agent prompts for Gmail + Chrome (context isolation)
    │   ├── linkedin_fetch_subagent.md
    │   └── linkedin_enrich_subagent.md
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
  LinkedIn runs for that profile and which Gmail label(s) to filter on.
- **Email per profile**: each profile's `email.json` controls recipient,
  which tiers to include, and whether the email goes out at all. Set
  `"enabled": false` to keep the digest local-only.
- **Schedule**: change the cron expression in your scheduled task (default `0 8 * * *`).
- **Sources**: remove or add steps in your scheduled task prompt. Each source
  is independent — the pipeline still works if any individual one is missing.

## License

Use it however you want.
