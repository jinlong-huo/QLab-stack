# Topics — one file per category

Simplest possible version of the vault ⇄ QLab merge: **four categories, one file each.**

| File | Category | Vault source | Lines |
|---|---|---|---|
| `llm.md` | LLM | `LLM/LLMBasics.md` | 245 |
| `ocs.md` | OCS | `OCS/Papers/MixNet_Analysis.md` | 151 |
| `hcf.md` | HCF | `HCF/HCF Phy.md` | 261 |
| `zotero.md` | Zotero | `Zotero/Harvest.md` | 252 |

Each file is a **verbatim** copy of one chosen vault note, with a small provenance frontmatter
prepended. Nothing else was changed, nothing in the vault was touched.

## Frontmatter contract

```yaml
---
topic: llm                 # category slug — the join key, and the map itself
category: LLM              # display name / folder name
vault_source: LLM/LLMBasics.md
vault_sha256: dff3707e…    # sha256 of the vault note (source drift)
seed_sha256: dff3707e…     # sha256 of the body in this repo (local hand edits)
seeded: 2026-09-16
status: seeded             # seeded | incoming | hand-edited
sync: manual               # manual until the publish pipeline exists
tags: [LLM, basics, inference, kv-cache]
---
```

The body follows a `<!-- SEED:PROVENANCE:BEGIN -->` marker, so the script can find the verbatim region
deterministically.

`vault_sha256` is the drift check for the vault note; `seed_sha256` is the same hash taken over the
body in this repo, which is what detects local hand edits. Both are maintained by the script — never
edit them by hand.

## Updating (one command)

```bash
make seed-check                    # which vault notes changed since the last upload?
make seed-sync ARGS=--apply        # re-upload clean drift, refresh hashes + attachments
make seed-add CAT=ocs SRC=OCS/Papers/MixNet_Analysis.md   # add or swap a category
```

Workflow: edit the vault note → `make seed-check` → `make seed-sync ARGS=--apply` → commit. If the
repo copy was also edited, you get a `*.seed-new.md` sidecar instead of a silent overwrite.

Full logic, states and exit codes: [`sync/README.md`](../../sync/README.md). Never hand-edit a seed
body: edit the vault note.

## What comes later (upload direction only)

The arXiv digest is **not** part of this: it keeps writing `daily_digest.md` into the repo as before,
and nothing is pushed into the vault. Optional additions, all in the same vault → repo direction:

- `seed-scan` — list vault notes that are not uploaded yet, with a suggested category.
- More than one note per category — already works: `make seed-add CAT=llm-structure SRC=LLM/Structure.md`.
- A pre-commit hook running `make seed-check`.

Details and the full rationale: [`sync/README.md`](../../sync/README.md).

## Assets

`attachments/` holds the Zotero images referenced by `zotero.md` (`attachments/<KEY>.png`), so the
relative image links render on GitHub unchanged.
