# `sync/` — upload Obsidian notes into the repo

**Direction: vault → repo only. One-way, notes only.** No script here reads the arXiv pipeline and no
script here ever writes to the vault. That is the whole design, and it is enforced by a test.

```
Obsidian vault  ──seed.py──►  knowledge-base/topics/*.md
   (source of truth)            (one file per category, read-only copy)
```

---

## 1. Commands

```bash
make seed-check                                   # read-only: what drifted since the last upload?
make seed-sync ARGS=--apply                       # upload clean drift, refresh hashes + attachments
make seed-sync ARGS="--apply --force"             # also overwrite local hand edits
make seed-add CAT=ocs SRC=OCS/Papers/MixNet_Analysis.md ARGS='--tags "OCS,MixNet"'
```

Directly:

```bash
python3 sync/seed.py                                       # report only (safe anywhere)
python3 sync/seed.py --apply                               # write
python3 sync/seed.py --add hcf "HCF/HCF Phy.md" --category HCF
python3 sync/seed.py --vault /path/to/other/vault          # --topics DIR also accepted
```

Exit codes: `0` in sync · `1` something needs a human · `2` usage/IO error. Read-only unless
`--apply`, so `make seed-check` is safe in a pre-commit hook or CI.

## 2. File layout it maintains

```markdown
---
topic: llm
category: LLM
vault_source: LLM/LLMBasics.md        # path relative to the vault root  ← the map lives here
vault_sha256: dff3707e…               # sha256 of the vault note (vault-side drift)
seed_sha256: dff3707e…                # sha256 of the body below (repo-side edits)
seeded: 2026-09-16                    # last upload date
status: seeded                        # seeded | incoming | hand-edited
sync: manual
tags: [LLM, basics, inference, kv-cache]
---

<!-- SEED:PROVENANCE:BEGIN -->
> Seeded verbatim from the vault note `LLM/LLMBasics.md` on 2026-09-16.
<!-- SEED:PROVENANCE:END -->

<verbatim vault body — never hand-edited>
```

There is no separate mapping config: **`vault_source` in the frontmatter is the map.** Adding a
category is `--add`, changing which note represents a category is `--add --force`, dropping one is
`rm <cat>.md`.

## 3. The logic (three-way compare, per seed file)

| vault note | repo copy | state | action |
|---|---|---|---|
| unchanged | unchanged | `OK` | nothing |
| **changed** | unchanged | `DRIFT` | `--apply` re-uploads and refreshes both hashes |
| **changed** | hand-edited | `CONFLICT` | writes `<cat>.seed-new.md`, **original untouched** |
| unchanged | hand-edited | `LOCAL-EDIT` | reported; only `--force` overwrites |
| missing | — | `MISSING` | reported |

Two hashes are what make this safe: `vault_sha256` detects vault-side edits, `seed_sha256` detects
edits made inside the repo copy — which is how a `CONFLICT` is distinguished from a clean `DRIFT`.

**Attachments:** every `attachments/<name>` reference in the body is resolved in the vault (next to
the note first, then anywhere in the vault) and copied into `knowledge-base/topics/attachments/`, so
relative image links render unchanged on GitHub. Copied on `--apply` and on `--add`. The vault copy is
only ever read.

**Write targets:** `<topics>/<cat>.md`, `<topics>/<cat>.seed-new.md` and `<topics>/attachments/*`.
Nothing else, anywhere. The vault is opened read-only.

## 4. Tests

```bash
python3 sync/test_seed.py        # 31 offline checks, also wired into `make test`
```

Covers add, steady state, drift, conflict, local edit, missing source, attachment copy, `--add`
refusal — plus the two invariants that matter here: **the vault is byte-identical after an upload**
and no stray files appear outside `topics/`.

## 5. Deliberately not built

The repo → vault direction is **out of scope by decision** (2026-09-16): publishing the arXiv daily
digest and auto-created paper stubs into the vault. It is a much harder problem — generated regions,
manifests, conflict sidecars inside the vault — and the notes you actually care about are the ones you
write yourself. `arxiv_digest/` keeps working exactly as before, writing `daily_digest.md` into the
repo; the vault is not involved.

Also not built: `vault_index` / `vault_refile` / `vault_promote` (all of which would write or move
things *inside* the vault) and `sync/taxonomy.yaml` (only needed to route digest papers into vault
subfolders — the four categories here are already the map).

## 6. If the upload direction ever needs more

Small, additive, same pattern (stdlib only, dry-run default, report first, never write to the vault):

- **Coverage scan** — `python3 sync/seed.py --scan`: list vault notes under `LLM/ OCS/ HCF/ Zotero/`
  that are *not* yet uploaded, with a suggested category. Answers "what else should I publish?"
  without moving anything.
- **More than one note per category** — already possible today via `--add` with a distinct slug
  (`make seed-add CAT=llm-structure SRC=LLM/Structure.md`); a `--scan` could propose the set.
- **Pre-commit hook** — run `make seed-check` so a commit warns when a published note has drifted.

## 7. Conventions

- Never hand-edit a seed body: edit the vault note, then `make seed-sync ARGS=--apply`. Local edits
  are detected, not silently lost.
- A conflict produces a `.seed-new.md` sidecar; merge by hand and delete the sidecar.
- Nothing in `sync/` commits or pushes. Publishing to the shared repo stays a manual, reviewed step.
- `sync/` is stdlib-only (`python3 sync/*.py`), matching the repo's single-dependency policy.
