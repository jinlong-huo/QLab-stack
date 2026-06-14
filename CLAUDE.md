# QLab Stack

LLM inference / datacenter networking research group toolkit.

## Architecture

```
Arxiv_filter.py          # Daily arXiv digest engine (the daily driver)
setup.zsh / setup.ps1    # Install launchd (macOS) / schtasks (Windows) daily 09:00
members/<name>/           # Personal workspace — paper notes, projects, repros
paper-notes/              # Shared paper note template
knowledge-base/           # Glossary, reading roadmap, topic deep-dives
templates/                # Reusable templates (LaTeX weekly report, meeting notes, reviews)
survival-guide/           # Career advice, how-to's, conference list
onboarding/ / offboarding/ # Join/leave procedures
```

## Key conventions

- **Paper notes**: copy `paper-notes/template.md` → `members/<name>/paper-notes/<year>/<paper-slug>.md`
- **Python**: single dependency (`feedparser`), install with `pip install -r requirements.txt`
- **Git**: `main` is protected; work on `feature/*` branches; commit types per [CONTRIBUTING.md](CONTRIBUTING.md)
- **Quick commands**: `make run`, `make send`, `make setup-mac`, `make note-new NAME=... FILE=...`

## Arxiv_filter.py

- Fetches 8 CS categories from `export.arxiv.org/api/query`, 200 papers/category
- Scores papers against two keyword filters: main (LLM/GPU/RDMA/scheduling) + OCS spotlight (optical switching)
- Top-15 main + top-10 OCS → `daily_digest.md`
- `--send` emails the digest (skips on Sat/Sun)
- State tracked in `seen_papers.json`
- Configured via variables at top of script: `CATEGORIES`, `KEYWORDS`, `OCS_KEYWORDS`, `MIN_SCORE`, `MAX_PAPERS`

## Dependencies

- Python 3.11+ with `feedparser` (see `requirements.txt`)
- macOS: `launchd` (built-in); Windows: `schtasks` (built-in)
- Gmail app password for email (stored in `.email_password`, gitignored)
