# LinkedIn Chrome Enrichment — sub-agent prompt (Step 3d)

Parent assistant: read this file, substitute every `{{PLACEHOLDER}}` with the
runtime value, and pass the substituted body as the `prompt` argument to the
`Agent` tool with `subagent_type="general-purpose"`. Then parse the
single-line stdout the sub-agent returns and update source-status accordingly.

Placeholders to substitute:
- `{{PROFILE}}` — profile name (e.g. `kyle`)
- `{{PROFILE_DIR}}` — absolute path, e.g. `/Users/.../JobApps/profiles/kyle`
- `{{PROJECT_DIR}}` — absolute path to JobApps root
- `{{TODAY}}` — `YYYY-MM-DD`
- `{{SIDECAR_PATH}}` — full path to the sidecar JSON produced by Step 3c, i.e.
  `{{PROFILE_DIR}}/data/linkedin_raw/{{TODAY}}_normalized.jobs.json`

---

You are running Step 3d (LinkedIn Chrome enrichment) for profile
**{{PROFILE}}** as a sub-agent. The parent assistant spawned you to keep
LinkedIn page text out of its 200K context window. Your job: open each
LinkedIn job page in Chrome, extract salary + description, write an
enrichments JSON, run the existing splicer, and return **exactly one line of
stdout**.

## Hard rules
1. **Do not echo any page content** — no quoted descriptions, no URLs, no
   job titles, no company names. Your final reply is ONE line in the format
   below. No preamble.
2. **Do not call any MCP other than `Claude_in_Chrome`** for the navigation
   work, and `workspace__bash` to run the splicer.
3. **Do not write any files** other than the one enrichments JSON at the
   path listed in step 6.
4. Process at most 25 jobs (cap from the front of the sidecar). If the
   sidecar has more entries, count the remainder under `skipped`.

## Steps

1. Read the sidecar JSON at `{{SIDECAR_PATH}}` using the `Read` tool. It is
   a list of `{hash, job_id, title, company, url}` objects.

   If the file does not exist or is empty (`[]`), write `{}` to
   `/tmp/{{PROFILE}}_linkedin_chrome_enrichments_{{TODAY}}.json` and
   return `status=no_jobs enriched=0 skipped=0`.

2. Call `mcp__Claude_in_Chrome__list_connected_browsers`. Pick the first
   entry where `isLocal == true`. If no entry, write `{}` to
   `/tmp/{{PROFILE}}_linkedin_chrome_enrichments_{{TODAY}}.json` and
   return `status=chrome_unavailable enriched=0 skipped=<total>`.

3. Call `mcp__Claude_in_Chrome__select_browser` with the chosen `deviceId`.

4. Call `mcp__Claude_in_Chrome__tabs_context_mcp` with
   `createIfEmpty=true`. Capture the returned `tabId`.

5. Take the first 25 entries of the sidecar. For each entry, in sequence:

   a. Call `mcp__Claude_in_Chrome__browser_batch` with:
      ```json
      [
        {"name": "navigate", "input": {"tabId": <tabId>, "url": "<entry.url>"}},
        {"name": "computer", "input": {"action": "wait", "duration": 3, "tabId": <tabId>}},
        {"name": "get_page_text", "input": {"tabId": <tabId>}}
      ]
      ```

   b. From the returned page text, extract:
      - **Salary range** — look for `$X - $Y per year`, `$X - $Y a year`,
        `$XK - $YK`, etc. Normalize to `"$X - $Y a year"` matching the
        existing parser format. If absent, use `"N/A"`.
      - **Applicant count** — e.g. `"56 applicants"`, `"100+ applicants"`.
      - **Posted age** — e.g. `"posted 2 days ago"`, `"reposted 14 min ago"`.
      - **Description** — 2–3 factual sentences about the role/team/scope.
        Avoid company boilerplate. Total summary stays under 350 chars.
      - **Final summary string**, formatted exactly as:
        `**$X - $Y** · N applicants · posted X ago. <description>`
        (Use `**N/A**` if salary absent; `<unknown> applicants` if absent;
        omit the `posted X ago` segment if absent.)

   c. If the page returns a LinkedIn login wall (e.g. the page text mentions
      "Join LinkedIn" / "Sign in" prominently and no job content), stop
      processing further entries and treat this and all remaining entries
      as skipped. Set `<halt_reason>` to `login_wall`.

   d. If `browser_batch` errors out for a single entry, count it as skipped
      and continue to the next entry.

6. Build an enrichments dict:
   ```json
   {
     "<hash>": {
       "compensation": "$X - $Y a year",
       "summary": "**$X - $Y** · N applicants · posted X ago. <description>"
     },
     ...
   }
   ```

   Use the `hash` field from the sidecar (NOT `job_id` — drop the
   `linkedin-` prefix; `hash` is already prefix-free).

   Write to `/tmp/{{PROFILE}}_linkedin_chrome_enrichments_{{TODAY}}.json`
   via the `Write` tool.

7. Close the tab: `mcp__Claude_in_Chrome__tabs_close_mcp` with the `tabId`.

8. Run the enrich-md splicer via bash:
   ```bash
   python3 {{PROJECT_DIR}}/pipeline/enrich_linkedin_md.py \
     {{PROFILE_DIR}}/data/linkedin_raw/{{TODAY}}_normalized.txt \
     /tmp/{{PROFILE}}_linkedin_chrome_enrichments_{{TODAY}}.json 2>&1
   ```
   The splicer prints `Applied N enrichments to ...` to stderr. Capture N.

## Return value

Reply with **exactly one line** in one of these formats (no other text,
no Markdown, no quotes):

- Success: `status=ok enriched=<N> skipped=<M>`
- No jobs to enrich: `status=no_jobs enriched=0 skipped=0`
- Chrome not connected: `status=chrome_unavailable enriched=0 skipped=<total>`
- LinkedIn login wall hit mid-loop: `status=login_wall enriched=<N> skipped=<M>`
- Splicer failed after enrichment: `status=splicer_error enriched=<N> skipped=<M>`

Where:
- `<N>` is the count `enrich_linkedin_md.py` reports as Applied (NOT the
  count of dict entries you built — they should match, but trust the
  splicer's count).
- `<M>` is total_sidecar_entries − N (jobs that were skipped or failed).
- `<total>` is the full sidecar length when Chrome wasn't reachable.

## What success looks like

- `/tmp/{{PROFILE}}_linkedin_chrome_enrichments_{{TODAY}}.json` exists and
  is valid JSON.
- The normalized markdown at
  `{{PROFILE_DIR}}/data/linkedin_raw/{{TODAY}}_normalized.txt` now has
  `Compensation` and `Summary` fields filled in for the enriched jobs.
- Your reply is one line, ≤80 chars, starting with `status=`.
