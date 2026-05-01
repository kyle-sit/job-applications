# Two-Machine Sync Workflow

This pipeline is designed to live on multiple machines, with code shared via
git and per-profile content kept local on each machine.

## What's tracked vs. local

| Tracked in git                          | Machine-local (gitignored)         |
| --------------------------------------- | ---------------------------------- |
| `pipeline/*.py` (pipeline scripts)      | `profiles/<name>/*` (everything inside an active profile) |
| `profiles/_template/*.example`          | `profiles/<name>/data/`            |
| `README.md`, `SETUP.md`, `INSTALL_PROMPT.md`, `SYNC.md` | `profiles/<name>/digest.md` |
| `scheduled-task-prompt-template.md`     | `profiles/<name>/digest_archive/`  |
| `.gitignore`                            |                                    |

The `.gitignore` is the source of truth — see it for the full pattern.

## Daily flow on a single machine

```bash
git pull --rebase            # before you start changes
# ... edit pipeline code, scoring, etc ...
git add -A && git commit -m "..."
git push
```

Active profile content (`profiles/<name>/`) is not touched — it stays local.

## Bringing personalized content to a new machine

Profiles are gitignored, so cloning the repo gives you only the `_template/`
files. To bootstrap a profile on a new machine:

```bash
git clone https://github.com/kyle-sit/job-applications.git
cd job-applications
mkdir -p profiles/kyle
cp profiles/_template/profile.md.example          profiles/kyle/profile.md
cp profiles/_template/search_queries.json.example profiles/kyle/search_queries.json
cp profiles/_template/scoring.json.example        profiles/kyle/scoring.json
cp profiles/_template/linkedin.json.example       profiles/kyle/linkedin.json
```

Then either edit them fresh, or copy the personalized versions over from your
other machine. Two common ways to copy:

1. **iCloud / Dropbox / a cloud-synced folder** — keep a copy of your
   personalized profile content in a synced folder, then copy them into
   `profiles/<name>/` on each machine.
2. **Manual paste** — open the personalized files on the source machine, paste
   them into Cowork chat here, and ask Claude to write them to
   `profiles/<name>/`.
3. **scp / AirDrop** — send the four files between machines directly.

If you change configs often and want them synced, the cleanest upgrade is a
separate **private** repo or gist for profile content. Don't move it into
this public repo.

## Migrating from the legacy single-profile layout

If you have an older clone with `config/`, `data/`, and `digest_archive/` at
the project root (single-profile layout), migrate to the multi-profile layout:

```bash
mkdir -p profiles/kyle/data
# Move personalized configs (these were gitignored in the old layout too):
[ -f config/profile.md          ] && mv config/profile.md          profiles/kyle/profile.md
[ -f config/search_queries.json ] && mv config/search_queries.json profiles/kyle/search_queries.json
[ -f config/scoring.json        ] && mv config/scoring.json        profiles/kyle/scoring.json
# Bootstrap a linkedin.json from the template (none existed in legacy layout):
cp profiles/_template/linkedin.json.example profiles/kyle/linkedin.json
# Edit linkedin.json: set "enabled": true and "gmail_label": "linkedin-jobs" if
# you want the legacy LinkedIn behavior preserved.

# Move runtime data:
[ -d data            ] && mv data/*            profiles/kyle/data/   2>/dev/null
[ -d digest_archive  ] && mv digest_archive    profiles/kyle/
[ -f digest.md       ] && mv digest.md         profiles/kyle/

# Clean up the now-empty legacy dirs:
rmdir data config 2>/dev/null
```

Then update your scheduled task: it will now run for every profile under
`profiles/` automatically. No re-install needed unless your MCP UUIDs changed.

## When the pipeline grows

Anytime you change something tracked (pipeline scripts, `_template/*.example`,
docs), commit and push. The other machine pulls and gets it automatically. If
you change a `_template/*.example`, also remember to re-apply that change to
your local `profiles/<name>/*` files (since those don't auto-update).

## Conflict cases to know

- **You edited a pipeline file on both machines:** standard git merge conflict.
  Resolve in your editor, commit, push.
- **You added a new profile or runtime data:** ignored — won't appear in
  `git status`. Each machine has its own profiles and digest history.
- **You changed scoring keys in `scoring.json.example`:** you need to manually
  merge those changes into each profile's `scoring.json` on each machine.
  Check `git diff HEAD~1 -- profiles/_template/scoring.json.example` after a pull.
