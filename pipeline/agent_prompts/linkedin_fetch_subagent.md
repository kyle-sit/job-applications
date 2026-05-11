# LinkedIn Gmail Fetch — sub-agent prompt (Step 3c)

Parent assistant: read this file, substitute every `{{PLACEHOLDER}}` with the
runtime value, and pass the substituted body as the `prompt` argument to the
`Agent` tool with `subagent_type="general-purpose"`. Then parse the
single-line stdout the sub-agent returns and update source-status accordingly.

Placeholders to substitute:
- `{{PROFILE}}` — profile name (e.g. `kyle`)
- `{{PROFILE_DIR}}` — absolute path, e.g. `/Users/.../JobApps/profiles/kyle`
- `{{PROJECT_DIR}}` — absolute path to JobApps root
- `{{TODAY}}` — `YYYY-MM-DD`
- `{{GMAIL_MCP_ID}}` — the Gmail MCP UUID for this profile (per-profile override
  from `linkedin.json.gmail_mcp_id` or the default)
- `{{GMAIL_QUERY}}` — pre-built query string from `gmail_labels`, e.g.
  `(label:linkedin-jobs-software OR label:linkedin-jobs-ai) newer_than:1d`

---

You are running Step 3c (LinkedIn Gmail fetch) for profile **{{PROFILE}}** as
a sub-agent. The parent assistant spawned you to keep email-body tokens out
of its 200K context window. Your job: fetch LinkedIn job-alert email bodies
via the Gmail MCP, write them to disk, run the existing parser, and return
**exactly one line of stdout**.

## Hard rules
1. **Do not echo any email content** — no thread previews, no quoted bodies,
   no URLs, no job titles. Your final reply is ONE line in the format below.
   No preamble, no explanation, no apologies.
2. **Do not call any MCP other than the Gmail MCP** below, and only the two
   methods listed.
3. **Do not write any files** other than the two outputs listed in step 4–5.
4. Cap at 50 threads even if more are returned (Gmail MCP `pageSize` max).

## Steps

1. Call `mcp__{{GMAIL_MCP_ID}}__search_threads` with:
   - `query`: `{{GMAIL_QUERY}}`
   - `pageSize`: `50`

   If the call errors, return `status=gmail_error threads=0 unique_jobs=0` and stop.
   If it returns zero threads, return `status=no_threads threads=0 unique_jobs=0`
   and stop.

2. For every thread in the response, call
   `mcp__{{GMAIL_MCP_ID}}__get_thread` with:
   - `threadId`: the thread's id
   - `messageFormat`: `"FULL_CONTENT"`

   Run these calls sequentially. If any individual `get_thread` errors,
   skip that thread and continue (count the skips internally).

3. From each `get_thread` response, walk `messages[]` and collect every
   non-empty `plaintextBody`. Concatenate every collected `plaintextBody`
   string across all threads, separated by two newlines (`\n\n`).

4. Write the concatenated text to:
   `{{PROFILE_DIR}}/data/linkedin_raw/{{TODAY}}.txt`
   using the `Write` tool.

5. Run the parser via bash (`mcp__workspace__bash`):
   ```bash
   python3 {{PROJECT_DIR}}/pipeline/linkedin_parser.py \
     {{PROFILE_DIR}}/data/linkedin_raw/{{TODAY}}.txt \
     {{PROFILE_DIR}}/data/linkedin_raw/{{TODAY}}_normalized.txt \
     {{TODAY}} 2>&1
   ```
   The parser prints to stderr a line like
   `parsed N unique LinkedIn jobs (skipped X non-job blocks) → ...`.
   Capture `N`. If the parser exits non-zero or you cannot find that line,
   treat as a parser error.

## Return value

Reply with **exactly one line** in one of these formats (no other text,
no Markdown, no quotes):

- Success: `status=ok threads=<thread_count> unique_jobs=<N>`
- No matching threads found: `status=no_threads threads=0 unique_jobs=0`
- Gmail MCP failed: `status=gmail_error threads=0 unique_jobs=0`
- Parser failed: `status=parser_error threads=<thread_count> unique_jobs=0`

Where `<thread_count>` is the number of threads `search_threads` returned
(NOT the number successfully fetched), and `<N>` is the parser's unique-jobs
count.

## What success looks like

- The two output files exist at the paths above.
- The parser printed a count line to stderr.
- Your reply is one line, ≤80 chars, starting with `status=`.
