#!/usr/bin/env python3
"""seed.py — one-way upload: vault notes → knowledge-base/topics/<cat>.md

The cheap half of the vault ⇄ QLab bridge: no vault writes, no commits, no network.
Reads the vault, writes only inside knowledge-base/topics/.

Layout of every seed file (the markers are load-bearing — do not delete them):

    ---
    topic: llm
    category: LLM
    vault_source: LLM/LLMBasics.md      # path relative to the vault root
    vault_sha256: <sha256 of the vault note body>   # source drift check
    seed_sha256: <sha256 of the body below>         # local hand-edit check
    seeded: 2026-09-16                  # last upload date
    status: seeded
    sync: manual
    tags: [LLM, basics]
    ---

    <!-- SEED:PROVENANCE:BEGIN -->
    > Seeded verbatim from the vault note `LLM/LLMBasics.md` on 2026-09-16.
    <!-- SEED:PROVENANCE:END -->

    <verbatim vault body>

Logic — three-way compare per file:

    vault note unchanged, repo copy unchanged                -> OK
    vault note changed, repo copy untouched                  -> DRIFT       re-upload
    vault note changed, repo copy hand-edited                -> CONFLICT    sidecar, never overwrite
    repo copy hand-edited, vault note unchanged              -> LOCAL-EDIT  report only
    vault_source does not exist                              -> MISSING     report only

Attachments: every `attachments/<name>` reference in the body is resolved in the vault
(next to the note, then anywhere in the vault) and copied into topics/attachments/.

Commands:
    python3 sync/seed.py                             # report only (default)
    python3 sync/seed.py --apply                     # upload clean drift, sidecar conflicts
    python3 sync/seed.py --apply --force             # also overwrite local hand edits
    python3 sync/seed.py --add ocs OCS/Papers/MixNet_Analysis.md --tags "OCS,MixNet"
    python3 sync/seed.py --add hcf HCF/HCF Phy.md --category HCF

    make seed-check
    make seed-sync ARGS=--apply
    make seed-add CAT=llm SRC=LLM/LLMBasics.md

Exit codes: 0 = in sync · 1 = drift/conflict/missing found · 2 = error
"""

import argparse
import datetime
import hashlib
import os
import pathlib
import re
import shutil
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_TOPICS = REPO_ROOT / "knowledge-base" / "topics"
DEFAULT_VAULT = pathlib.Path(
    os.environ.get("QLAB_VAULT", "/Users/Vir-G/Documents/Obsidian_Vault")
).expanduser()

PROV_BEGIN = "<!-- SEED:PROVENANCE:BEGIN -->"
PROV_END = "<!-- SEED:PROVENANCE:END -->"
FM_ORDER = [
    "topic", "category", "vault_source", "vault_sha256", "seed_sha256",
    "seeded", "status", "sync", "tags",
]
SKIP_NAMES = {"README.md"}


# ---------------------------------------------------------------- primitives

def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_front(text):
    """Split '---' frontmatter from the rest. Values stay raw strings."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = {}
            for line in lines[1:i]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
            return fm, "\n".join(lines[i + 1:])
    return {}, text


def split_provenance(rest):
    """Return (provenance_lines, body). Body is verbatim, only trailing blanks trimmed."""
    stripped = rest.lstrip("\n")
    if stripped.startswith(PROV_BEGIN):
        end = stripped.find(PROV_END)
        if end != -1:
            prov = stripped[len(PROV_BEGIN):end].strip("\n")
            body = stripped[end + len(PROV_END):].lstrip("\n")
            return prov, body
    return None, stripped


def provenance_block(src_rel, date):
    return (
        f"{PROV_BEGIN}\n"
        f"> Seeded verbatim from the vault note `{src_rel}` on {date}.\n"
        f"> Do not hand-edit the body below — edit the vault note, then run `make seed-sync ARGS=--apply`.\n"
        f"{PROV_END}"
    )


def render(fm, body):
    ordered = [(k, fm[k]) for k in FM_ORDER if k in fm]
    ordered += [(k, v) for k, v in fm.items() if k not in FM_ORDER]
    head = "\n".join(f"{k}: {v}" for k, v in ordered)
    return f"---\n{head}\n---\n\n{provenance_block(fm['vault_source'], fm['seeded'])}\n\n{body.rstrip()}\n"


def looks_hand_edited(fm):
    return fm.get("status", "").strip() in {"hand-edited", "edited"}


def rel_display(path):
    """Repo-relative when possible, absolute otherwise (tests use temp dirs)."""
    try:
        return str(pathlib.Path(path).relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- discovery

def attachment_refs(body):
    return sorted(set(re.findall(r"attachments/([A-Za-z0-9_.\-]+)", body)))


def find_attachment(name, src_file, vault):
    for cand in (src_file.parent / "attachments" / name, src_file.parent / name):
        if cand.is_file():
            return cand
    hits = sorted(vault.glob(f"**/{name}"))
    return hits[0] if hits else None


def load_seeds(topics_dir):
    seeds = []
    for path in sorted(topics_dir.glob("*.md")):
        if path.name in SKIP_NAMES or path.name.endswith(".seed-new.md"):
            continue
        fm, rest = read_front(path.read_text(encoding="utf-8"))
        if "vault_source" not in fm:
            continue
        _, body = split_provenance(rest)
        seeds.append({"path": path, "fm": fm, "body": body})
    return seeds


def classify_seed(seed, vault):
    """Return (state, detail, src_text, src_file)."""
    src_rel = seed["fm"]["vault_source"]
    src_file = vault / src_rel
    if not src_file.is_file():
        return "MISSING", f"no such vault file: {src_rel}", None, src_file
    src_text = src_file.read_text(encoding="utf-8")
    src_hash = sha(src_text)
    local_hash = sha(seed["body"])
    stored_src = seed["fm"].get("vault_sha256", "")
    stored_seed = seed["fm"].get("seed_sha256", "")
    src_changed = src_hash != stored_src
    local_changed = local_hash != stored_seed

    if not src_changed and not local_changed:
        return "OK", "", src_text, src_file
    if src_changed and not local_changed:
        return "DRIFT", "vault note changed since the upload", src_text, src_file
    if src_changed and local_changed:
        return "CONFLICT", "vault note changed AND repo copy was edited", src_text, src_file
    return "LOCAL-EDIT", "repo copy edited, vault note unchanged", src_text, src_file


# ---------------------------------------------------------------- commands

def copy_attachments(body, src_file, vault, topics_dir, apply_changes):
    actions = []
    for name in attachment_refs(body):
        found = find_attachment(name, src_file, vault)
        if found is None:
            actions.append((name, "referenced but not found in vault"))
            continue
        dest = topics_dir / "attachments" / name
        if dest.is_file() and dest.read_bytes() == found.read_bytes():
            continue
        actions.append((name, f"copy <- {found.relative_to(vault)}"))
        if apply_changes:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, dest)
    return actions


def cmd_report(args):
    vault, topics = args.vault, args.topics
    seeds = load_seeds(topics)
    if not seeds:
        print(f"no seed files found in {topics}")
        return 2

    def say(*parts):
        if not args.quiet:
            print(*parts)

    today = datetime.date.today().isoformat()
    unresolved, applied = [], 0
    say(f"vault : {vault}")
    say(f"topics: {topics}")
    say(f"mode  : {'APPLY' if args.apply else 'report only'}{' (force)' if args.force else ''}\n")
    say(f"{'file':<12} {'state':<11} detail")
    say("-" * 72)

    for seed in seeds:
        state, detail, src_text, src_file = classify_seed(seed, vault)
        say(f"{seed['path'].name:<12} {state:<11} {detail}")
        if state == "OK":
            continue

        if state in {"DRIFT", "CONFLICT"} and src_file.is_file():
            for name, act in copy_attachments(src_text, src_file, vault, topics, args.apply):
                say(f"{'':<12} {'':<11}   attachment {name}: {act}")

        if state == "DRIFT":
            if args.apply:
                fm = dict(seed["fm"])
                fm["vault_sha256"] = sha(src_text)
                fm["seed_sha256"] = sha(src_text)
                fm["seeded"] = today
                fm["status"] = "seeded"
                seed["path"].write_text(render(fm, src_text), encoding="utf-8")
                applied += 1
                say(f"{'':<12} {'':<11}   -> re-uploaded, hashes refreshed")
            else:
                unresolved.append(seed["path"].name)
        elif state == "CONFLICT":
            if args.force and args.apply:
                fm = dict(seed["fm"])
                fm["vault_sha256"] = sha(src_text)
                fm["seed_sha256"] = sha(src_text)
                fm["seeded"] = today
                fm["status"] = "seeded"
                seed["path"].write_text(render(fm, src_text), encoding="utf-8")
                applied += 1
                say(f"{'':<12} {'':<11}   -> forced overwrite (local edits dropped)")
            else:
                side = seed["path"].with_suffix(".seed-new.md")
                fm = dict(seed["fm"])
                fm["vault_sha256"] = sha(src_text)
                fm["seed_sha256"] = sha(src_text)
                fm["seeded"] = today
                fm["status"] = "incoming"
                fm["note"] = "conflict copy: original kept, review and merge by hand"
                side.write_text(render(fm, src_text), encoding="utf-8")
                unresolved.append(f"{seed['path'].name} (sidecar written)")
                say(f"{'':<12} {'':<11}   -> wrote {side.name}, original untouched")
        elif state == "LOCAL-EDIT":
            unresolved.append(seed["path"].name)
            say(f"{'':<12} {'':<11}   -> repo copy diverged from the vault; --force re-uploads")
        elif state == "MISSING":
            unresolved.append(seed["path"].name)

    say("-" * 72)
    if unresolved:
        say(f"{len(unresolved)} file(s) need a human: {', '.join(unresolved)}")
        say(f"{applied} re-uploaded automatically; {len(seeds) - len(unresolved) - applied} already in sync.")
        if not args.apply:
            say("hint: `make seed-sync ARGS=--apply` uploads clean drift; conflicts are left alone.")
        return 1
    if applied:
        say(f"{applied} file(s) re-uploaded; {len(seeds) - applied} already in sync.")
    else:
        say(f"{len(seeds)} seed(s): everything in sync with the vault.")
    return 0


def cmd_add(args):
    vault, topics = args.vault, args.topics
    src_rel = args.src
    src_file = (vault / src_rel) if not pathlib.Path(src_rel).is_absolute() else pathlib.Path(src_rel)
    if not src_file.is_file():
        print(f"error: vault note not found: {src_file}", file=sys.stderr)
        return 2

    cat = args.add
    dest = topics / f"{cat}.md"
    if dest.exists() and not args.force:
        print(f"error: {dest} exists (use --force to replace)", file=sys.stderr)
        return 2

    src_text = src_file.read_text(encoding="utf-8")
    try:
        rel = str(src_file.relative_to(vault))
    except ValueError:
        rel = str(src_file)
    today = datetime.date.today().isoformat()
    tags = args.tags.strip() if args.tags else (args.category or cat.upper())
    fm = {
        "topic": cat,
        "category": (args.category or cat.upper()),
        "vault_source": rel,
        "vault_sha256": sha(src_text),
        "seed_sha256": sha(src_text),
        "seeded": today,
        "status": "seeded",
        "sync": "manual",
        "tags": f"[{tags}]",
    }
    topics.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(fm, src_text), encoding="utf-8")
    print(f"created {rel_display(dest)}  <-  {rel}  ({len(src_text.splitlines())} lines)")
    for name, act in copy_attachments(src_text, src_file, vault, topics, True):
        print(f"  attachment {name}: {act}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Upload vault notes into knowledge-base/topics (one file per category).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--vault", type=pathlib.Path, default=DEFAULT_VAULT, help="vault root")
    p.add_argument("--topics", type=pathlib.Path, default=DEFAULT_TOPICS, help="destination dir")
    p.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    p.add_argument("--force", action="store_true", help="overwrite local hand edits too")
    p.add_argument("--quiet", action="store_true", help="suppress the report table")
    p.add_argument("--add", metavar="CAT", help="create/replace a category seed")
    p.add_argument("src", nargs="?", help="with --add: vault-relative path to the source note")
    p.add_argument("--category", help="with --add: display name (default: CAT uppercased)")
    p.add_argument("--tags", help="with --add: comma-separated tags")
    args = p.parse_args(argv)

    if not args.vault.is_dir():
        print(f"error: vault not found: {args.vault}", file=sys.stderr)
        return 2
    if args.add:
        if not args.src:
            print("error: --add requires a source path, e.g. --add ocs OCS/Papers/MixNet_Analysis.md",
                  file=sys.stderr)
            return 2
        return cmd_add(args)
    args.topics.mkdir(parents=True, exist_ok=True)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
