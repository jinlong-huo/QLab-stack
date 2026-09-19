# QLab Stack

LLM inference / datacenter networking research group toolkit.

## Architecture

```
arxiv_digest/               # Pipeline (fetch → filter → select → digest)
  ├── Arxiv_filter.py       #   Main orchestrator
  ├── config.py             #   All settings: keywords, thresholds, paths
  ├── fetch.py              #   arXiv API: query builder, retry logic, error classification
  ├── filter.py             #   Text cleaning, keyword scoring (main + OCS, context-gated acronyms)
  ├── digest.py             #   State I/O, top-N selection, markdown generation
  ├── classify.py           #   Subfolder router: digest paper → LLM/moe, OCS/hardware, …
  ├── download_papers.py    #   PDF downloader (routes to topic subfolders via classify.py)
  ├── verify_downloads.py   #   Audit: digest papers vs on-disk PDFs; backfill missing (--days/--download)
  ├── rename_papers.py      #   PDF renamer v1 (legacy; has the arXiv-mirror fallbacks)
  ├── rename_papers_v2.py   #   PDF renamer v2 (canonical): metadata lookup + verification
  ├── test_catchup.py       #   Catch-up mechanism test (no network)
  ├── test_classify.py      #   Subfolder routing golden cases (no network)
  └── test_verify.py        #   Download-verify matching tests (no network)
members/<name>/           # Personal workspace — paper notes, projects, repros
paper-notes/              # Shared paper note template
knowledge-base/           # Glossary, reading roadmap, topic deep-dives
  └── topics/             #   One uploaded note per category: llm / ocs / hcf / zotero
sync/                     # Vault → repo upload (seed.py). One-way, notes only, never writes to the vault
templates/                # Reusable templates (LaTeX weekly report, meeting notes, reviews)
survival-guide/           # Career advice, how-to's, conference list
onboarding/ / offboarding/ # Join/leave procedures
```

## Key conventions

- **Paper notes**: copy `paper-notes/template.md` → `members/<name>/paper-notes/<year>/<paper-slug>.md`
- **Python**: dependencies are pinned in `requirements.txt` (`pip install -r requirements.txt`)
- **Git**: `main` is protected; work on `feature/*` branches; commit types per [CONTRIBUTING.md](CONTRIBUTING.md)
- **Quick commands**: `make run`, `make daily`, `make test`, `make note-new NAME=... FILE=...`
- **Vault notes → repo**: `make seed-check` (read-only drift), `make seed-sync ARGS=--apply`,
  `make seed-add CAT=ocs SRC=OCS/Papers/MixNet_Analysis.md`. One-way upload from the Obsidian vault
  (`QLAB_VAULT` env var, default `/Users/Vir-G/Documents/Obsidian_Vault`); `sync/seed.py` never writes
  to the vault. See [sync/README.md](sync/README.md).

## Pipeline

1. **fetch.py** — Pulls 8 categories from `export.arxiv.org/api/query`, 200 papers per page with auto-pagination. Default daily plan: last 3 days **plus an automatic lookback window** (8→4 days ago) that covers arXiv listing lag and forgotten days. `--from`/`--to` backfill a date range (chunked into 3-day windows, no lookback). Retry with exponential backoff; fatal-error detection for SSL/DNS failures.
2. **filter.py** — Scores each paper against two independent keyword filters: main (LLM/GPU/RDMA/scheduling) and OCS spotlight (optical switching + CPO ecosystem). Clash-prone acronyms (`cpo`/`lpo`/`npo`, `slo`) are context-gated: they only score when optical/serving context words are present.
3. **digest.py** — Selects top-15 main + top-10 OCS + top-5 carry-over, writes `daily_digest.md`. Manages `seen_papers.json` (write-only ledger) and `digest_papers.json` (two-tier gate) state.
4. **Arxiv_filter.py** — Orchestrates the pipeline. `--wait` auto-retries on 429; `--from YYYY-MM-DD --to YYYY-MM-DD` backfills a period; `--ignore-seen` re-scores regardless of digest history (use with `--from/--to`).
5. **download_papers.py** — Routes each digest paper to a topic subfolder via `classify.py`: `LLM/{moe,memory,agents,train,eval,inference,misc}`, `OCS/{hardware,topology,algorithms,applications}`, or top-level `Distributed/` (collectives / distributed-training infra). Most-specific topic wins (MoE-serving → `moe`); weak-signal papers fall back to `misc`/`applications`. Decisions are logged to `download_log.json` for review; tune rules in `config.SUBFOLDER_RULES`.
6. **verify_downloads.py** — Audits that digest papers actually exist on disk (match by arXiv ID or normalized title). Default: current digest; `--days N`: papers shown in digests within the last N days ("useful papers"); `--download`: backfills missing ones into their classified subfolders. `run_daily.sh` runs it report-only after renaming.
7. **rename_papers_v2.py** — Renames PDFs to `Author_Year_Title.pdf` (arXiv/Crossref/OpenAlex/S2 lookup, every match verified against the PDF's own page 1). The paper library lives **outside** this repo: root comes from `--root`, then `$QLAB_PAPER_ROOT`, then `/Users/Vir-G/Downloads/Paper`; the cache/report/undo log stay in `arxiv_digest/`. Needs poppler (`pdfinfo`/`pdftotext`) **and** `pdfminer.six` — it exits loudly if either is missing (a missing pdfminer would otherwise skip every file and look like a clean run). Report is read-only by default; `--apply` writes only high/medium confidence renames, `--revert` undoes the last run, `--acm` targets top-level ACM downloads, `--file` debugs one PDF.

### Two-tier digest gate + carry-over

Papers that match but get cut by top-N are stamped `shown: false` (pending) in `digest_papers.json` instead of being permanently skipped. Within `RESURFACE_DAYS` (7) days, a pending paper scoring ≥ `RESURFACE_MIN_SCORE` (12) resurfaces via the **High-Score Carry-Over** digest section (max `MAX_RESURFACED` = 5), labeled with its original first-seen date. Only papers actually shown in a section get `shown: true` (permanent skip). Legacy entries without a `shown` field migrate to `shown: true`.

All knobs live in `config.py`: `CATEGORIES`, `KEYWORDS`, `OCS_KEYWORDS`, `MIN_SCORE`, `MAX_PAPERS`, `LOOKBACK_*`, `RESURFACE_*`, `SUBFOLDER_RULES`.

## Dependencies

- Python 3.11+ with `feedparser` (see `requirements.txt`)
- For the PDF renamer: poppler (`pdfinfo`/`pdftotext`) + `pdfminer.six`, and run it with the
  interpreter that has them (`python3.12 arxiv_digest/rename_papers_v2.py`)
