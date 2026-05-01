# Two-Machine Sync Workflow

This pipeline is designed to live on multiple machines, with code shared via
git and personal configs kept local on each machine.

## What's tracked vs. local

| Tracked in git                          | Machine-local (gitignored)         |
| --------------------------------------- | ---------------------------------- |
| `pipeline/*.py` (pipeline scripts)      | `config/profile.md`                |
| `config/*.example.md` and `*.example.json` | `config/search_queries.json`    |
| `README.md`, `SETUP.md`, `INSTALL_PROMPT.md`, `SYNC.md` | `config/scoring.json` |
| `scheduled-task-prompt-template.md`     | `data/`                            |
| `.gitignore`                            | `digest_archive/`                  |
|                                         | `digest.md`                        |

The `.gitignore` is the source of truth — see it for the full list.

## Daily flow on a single machine

```bash
git pull --rebase            # before you start changes
# ... edit pipeline code, scoring, etc ...
git add -A && git commit -m "..."
git push
```

Personal configs are not touched — they stay local.

## Bringing personalized configs to a new machine

The configs are gitignored, so cloning the repo gives you only the `.example`
templates. To bootstrap a new machine:

```bash
git clone https://github.com/kyle-sit/job-applications.git
cd job-applications
cp config/profile.md.example          config/profile.md
cp config/search_queries.json.example config/search_queries.json
cp config/scoring.json.example        config/scoring.json
```

Then either edit them fresh, or copy them over from your other machine. Two
common ways to copy:

1. **iCloud / Dropbox / a cloud-synced folder** — keep a copy of your
   personalized configs in a synced folder, then copy them into `config/` on
   each machine.
2. **Manual paste** — open the personalized files on the source machine, paste
   them into Cowork chat here, and ask me to write them to `config/`.
3. **scp / AirDrop** — send the three files between machines directly.

If you change `config/scoring.json` often and want it synced too, the cleanest
upgrade is a separate **private** repo or gist for configs. Don't move it into
this public repo.

## When the pipeline grows

Anytime you change something tracked (pipeline scripts, `.example` configs,
docs), commit and push. The other machine pulls and gets it automatically. If
you change a `.example` template, also remember to re-apply that change to your
local `config/*.json` or `config/profile.md` (since those don't auto-update).

## Conflict cases to know

- **You edited a pipeline file on both machines:** standard git merge conflict.
  Resolve in your editor, commit, push.
- **You added a new file under `data/` or `digest_archive/`:** ignored — won't
  appear in `git status`. Each machine has its own digest history.
- **You changed the scoring keys in `scoring.json.example`:** you need to
  manually merge those changes into your local `config/scoring.json` on each
  machine. Check `git diff HEAD~1 -- config/scoring.json.example` after a pull.
