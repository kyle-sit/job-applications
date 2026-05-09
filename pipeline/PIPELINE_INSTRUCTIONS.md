# Pipeline Architecture — Reference

This is a reference doc on what each script does and how the pieces fit. The
actual daily execution lives in `scheduled-task-prompt-template.md` at the
project root.

## Sources and parsers

| Source   | Fetched by               | Output format            | Salary in raw?    |
|----------|--------------------------|--------------------------|-------------------|
| Indeed   | Indeed MCP `search_jobs` | Markdown (native format) | Yes               |
| LinkedIn | Gmail MCP + `linkedin_parser.py` | Markdown                | No (added pre-score) |
| HN       | `hn_fetcher.py` (dormant — see below) | Markdown      | Sometimes (parsed from comments) |

## Pipeline scripts

### `linkedin_parser.py`
Reads concatenated plaintext bodies from LinkedIn job-alert emails, splits
each block by separator lines, and extracts `(title, company, location, url,
job_id)` per posting. Outputs both the markdown file and a sidecar JSON list
of jobs for the Chrome enrichment step.

### `enrich_linkedin_md.py`
Takes the LinkedIn markdown plus a `{hash: {compensation, summary}}` JSON
and updates the markdown in place — fills in `Compensation` and inserts a
`Summary` field. Runs **before** scoring so the new salaries influence ranking.

### `parse_and_score.py`
The heart of the pipeline. Accepts any number of input markdown files (one
per source), dedupes by job hash, applies the salary floor filter, scores
each remaining job, sorts into three tiers, and writes the digest as Markdown.

Loads tunable scoring rules from `config/scoring.json`. If missing, uses the
hardcoded defaults at the top of the file.

Also writes two sidecar JSONs:
- `needs_enrichment.json` — Indeed strong matches that need `get_job_details`
- `needs_enrichment_linkedin.json` — LinkedIn jobs missing a summary (Chrome failed)

### `splice_enrichments.py`
Replaces `<!--ENRICH_INDEED:hash-->` and `<!--ENRICH_LINKEDIN:hash-->` markers
in a digest with blockquote summaries from a `{hash: text}` JSON. Markers
without a matching entry are removed silently.

### `hn_fetcher.py` (dormant)
Fetches the latest "Ask HN: Who is hiring?" thread and parses comments. Built
but currently blocked by Cowork's network egress allowlist. Will activate
automatically if `hn.algolia.com` and `hacker-news.firebaseio.com` are
allowlisted in your Cowork settings AND the egress sandbox respects the
allowlist (some plans don't apply user allowlist to runtime).

## Execution order

```
1. Fetch  ────► Indeed/LinkedIn raw data
2. Parse  ────► Each source → unified markdown blocks
3. Enrich (PRE-SCORE) ──► LinkedIn pages via Chrome → salary + summary written into LinkedIn markdown
4. Score  ────► parse_and_score.py reads all markdown, ranks, writes digest with markers
5. Enrich (POST-SCORE) ──► Indeed get_job_details for top markers; LinkedIn fallback if Chrome missed any
6. Splice ────► splice_enrichments.py replaces markers with descriptions
7. Archive ──► copy digest.md to digest_archive/{date}.md
```

## Why pre-score LinkedIn enrichment matters

LinkedIn alert emails don't include salary. If you score them with no comp
data, every LinkedIn match gets 0 on the salary axis — which is half the
points away from "Strong Match" tier. By Chrome-fetching salary BEFORE
scoring, LinkedIn jobs compete fairly with Indeed.

## Why post-score Indeed enrichment is fine

Indeed's `search_jobs` already returns the salary. `get_job_details` only
adds the description. Fetching descriptions doesn't change rankings, so we
do it post-score, only for the top 15-ish strong matches, to save API calls.

## Tuning

All scoring knobs are in `config/scoring.json`. See the comments in that file
for what each section does. Common tuning patterns:

- **Too many results in Strong tier** — raise `tiers.strong_min` (e.g. from 17 to 19)
- **Wrong jobs ranking high** — adjust `title.tech_keywords` or `senior_tokens`
- **Salary filter too tight** — lower `salary_floor`
- **Want to ignore stale jobs** — lower `recency.stale_after_days` and increase `stale_penalty`

The pipeline reads config on every run, so changes take effect on the next
scheduled run with no restart.
