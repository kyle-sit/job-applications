# Indeed Search — sub-agent prompt (Step 3b)

Parent assistant: read this file, substitute every `{{PLACEHOLDER}}` with the
runtime value, and pass the substituted body as the `prompt` argument to the
`Agent` tool with `subagent_type="general-purpose"`. Then parse the
single-line stdout the sub-agent returns and update source-status accordingly.

Why this is a sub-agent: the Indeed search matrix is the single biggest
consumer of the parent's per-run TURN budget (one `search_jobs` call per
role×location pair, run serially so the rate limiter doesn't trip — often
20-40 calls per profile). Delegating the whole matrix to a sub-agent keeps
those calls (and their large markdown responses) out of the parent's turn
and context budgets, so the daily run finishes without hitting the
max-turns guardrail. The parent just spawns this agent and reads one line.

Placeholders to substitute:
- `{{PROFILE}}` — profile name (e.g. `kyle`)
- `{{PROFILE_DIR}}` — absolute path, e.g. `/Users/.../JobApps/profiles/kyle`
- `{{PROJECT_DIR}}` — absolute path to JobApps root
- `{{TODAY}}` — `YYYY-MM-DD`
- `{{INDEED_MCP_ID}}` — the Indeed search MCP UUID (the resolved value in the
  installed task, e.g. `350c9a8e-39e6-4738-94db-a452682e2fb2`)

---

You are running Step 3b (Indeed search matrix) for profile **{{PROFILE}}** as
a sub-agent. The parent assistant spawned you to keep the many Indeed
`search_jobs` calls out of its turn/context budget. Your job: run every
(role × location) search SERIALLY, concatenate the results, write them to
disk, and return **exactly one line of stdout**.

## Hard rules
1. **Do not echo any search results** — no job titles, no URLs, no markdown
   bodies. Your final reply is ONE line in the format below. No preamble,
   no explanation, no apologies.
2. **Do not call any MCP other than the Indeed search MCP**
   (`mcp__{{INDEED_MCP_ID}}__search_jobs`) plus
   `workspace__bash` / `Read` / `Write` for file work.
3. **Do not write any files** other than the one raw-searches output at the
   path in step 4.
4. **Never issue searches as a parallel burst.** Run them one at a time,
   waiting for each response before issuing the next. Indeed's per-account
   rate limiter trips on bursts and, once tripped, stays sticky for minutes.

## Steps

1. Read `{{PROFILE_DIR}}/search_queries.json` with the `Read` tool. Extract
   `role_queries` (list), `locations` (list), `country_code` (default
   `"US"`), and `job_type` (default `"fulltime"`). If the file is missing or
   still contains placeholder text (`REPLACE-WITH-...`), return
   `status=config_error pairs=0 jobs=0` and stop.

2. Build the full matrix = every `role_queries[i] × locations[j]` pair. Track
   `total_pairs`, `ok_pairs`, and `skipped_pairs`.

3. For each pair, in sequence (SERIAL — one call, await result, then next):
   - Call `mcp__{{INDEED_MCP_ID}}__search_jobs` with:
     - `search`: the role string
     - `location`: the location string
     - `country_code`: from config
     - `job_type`: from config
   - On success, append the response's `result` text to an in-memory buffer
     (separate entries with a blank line). Count the pair as `ok`.
     A response of "No job results found" is still a SUCCESS (count it ok);
     it just contributes no text.
   - On a `Rate limit exceeded` error, switch to exponential backoff for
     THAT pair: wait 5s (via `bash sleep 5`), retry; double each time
     (5 → 10 → 20 → 40), cap at 60s. After 3 consecutive failures for the
     same pair, skip it (count `skipped`) and continue with the rest.
   - To keep the call rate safely under the limiter between successful
     calls, you may run a short `bash sleep 1` every few pairs. The natural
     latency between sequential tool calls is usually enough on its own.

4. Write the concatenated buffer to:
   `{{PROFILE_DIR}}/data/raw_searches/{{TODAY}}.txt`
   using the `Write` tool. If the buffer is empty because EVERY pair errored
   out (none succeeded), do NOT write the file.

## Return value

Reply with **exactly one line** in one of these formats (no other text,
no Markdown, no quotes):

- All pairs succeeded: `status=ok pairs=<total_pairs> jobs=<job_count>`
- Some pairs failed, some succeeded: `status=rate_limited_partial pairs=<total_pairs> jobs=<job_count>`
- Every pair errored out (no file written): `status=rate_limited pairs=<total_pairs> jobs=0`
- Config missing/placeholder: `status=config_error pairs=0 jobs=0`

Where `<job_count>` is the number of `**Job Id:**` markers in the file you
wrote (run `grep -c 'Job Id' <path>` via bash to count). Use `jobs=0` when
no file was written.

## What success looks like

- `{{PROFILE_DIR}}/data/raw_searches/{{TODAY}}.txt` exists (unless every
  pair errored out).
- Your reply is one line, ≤80 chars, starting with `status=`.
