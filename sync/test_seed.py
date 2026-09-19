#!/usr/bin/env python3
"""seed.py 的离线测试（纯离线：临时 vault + 临时 topics 目录，不碰真实文件）。

覆盖：
  - --add 新建 seed：frontmatter 完整、body 逐字一致、附件被复制
  - 稳态：报告 exit 0，state=OK
  - 干净漂移：vault 笔记改了 → DRIFT（报告 exit 1），--apply 后回到 OK 且哈希刷新
  - 冲突：vault 改了 + 仓库副本被手改 → CONFLICT，写 .seed-new.md 旁挂文件，原文件不动
  - 本地手改但 vault 未变 → LOCAL-EDIT，--apply 不覆盖
  - MISSING：vault_source 指到不存在的文件

运行: python3 sync/test_seed.py   （或 make test）
"""

import importlib.util
import hashlib
import pathlib
import shutil
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("seed", _HERE / "seed.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)

CHECKS = 0


def check(label, cond, extra=""):
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{(' — ' + extra) if extra else ''}")
        sys.exit(1)


def new_fixture():
    root = pathlib.Path(tempfile.mkdtemp(prefix="qlab-seed-test-"))
    vault = root / "Vault"
    topics = root / "topics"
    (vault / "LLM").mkdir(parents=True)
    (vault / "Zotero" / "attachments").mkdir(parents=True)
    (vault / "LLM" / "Basics.md").write_text(
        "# LLM Basics\n\nKV cache peak memory formula.\n", encoding="utf-8")
    (vault / "Zotero" / "Paper.md").write_text(
        "# Paper\n\nSee the table:\n\n![t](attachments/ABC123.png)\n", encoding="utf-8")
    (vault / "Zotero" / "attachments" / "ABC123.png").write_bytes(b"\x89PNG-fake")
    topics.mkdir(parents=True)
    return root, vault, topics


def run(vault, topics, *argv, quiet=True):
    extra = ["--quiet"] if quiet else []
    return seed.main(["--vault", str(vault), "--topics", str(topics), *extra, *argv])


def state_of(topics, name):
    seeds = seed.load_seeds(topics)
    match = [s for s in seeds if s["path"].name == name]
    assert match, f"{name} not loaded"
    return seed.classify_seed(match[0], topics.parent / "Vault")[0]


def tree_hash(root):
    """Recursive content fingerprint — the upload direction must never change the vault."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


print("seed.py — 离线用例")
print("─" * 60)

root, vault, topics = new_fixture()
try:
    # --- 1. --add creates a seed -------------------------------------------
    rc = run(vault, topics, "--add", "llm", "LLM/Basics.md", "--tags", "LLM,basics")
    check("--add returns 0", rc == 0, f"rc={rc}")
    llm = topics / "llm.md"
    check("seed file created", llm.is_file())
    text = llm.read_text(encoding="utf-8")
    fm, rest = seed.read_front(text)
    check("frontmatter has vault_source", fm.get("vault_source") == "LLM/Basics.md")
    check("frontmatter has both hashes",
          fm.get("vault_sha256") == fm.get("seed_sha256") == seed.sha((vault / "LLM" / "Basics.md").read_text()))
    _, body = seed.split_provenance(rest)
    check("body is verbatim", body.rstrip() == (vault / "LLM" / "Basics.md").read_text().rstrip())
    check("provenance marker present", "SEED:PROVENANCE:BEGIN" in text)

    # --- 2. steady state ---------------------------------------------------
    rc = run(vault, topics)
    check("report on steady state returns 0", rc == 0, f"rc={rc}")
    check("state OK", state_of(topics, "llm.md") == "OK")

    # --- 3. clean drift -> DRIFT -> --apply --------------------------------
    (vault / "LLM" / "Basics.md").write_text(
        "# LLM Basics\n\nKV cache peak memory formula.\n\n## MQA / GQA\nnew section\n",
        encoding="utf-8")
    rc = run(vault, topics)
    check("report on drift returns 1", rc == 1, f"rc={rc}")
    check("state DRIFT", state_of(topics, "llm.md") == "DRIFT")
    vault_before = tree_hash(vault)
    rc = run(vault, topics, "--apply")
    check("--apply returns 0", rc == 0, f"rc={rc}")
    check("state OK after apply", state_of(topics, "llm.md") == "OK")
    check("body updated", "new section" in (topics / "llm.md").read_text(encoding="utf-8"))
    check("upload never modifies the vault (byte-identical)", tree_hash(vault) == vault_before)
    check("no stray files outside topics/",
          sorted(p.name for p in topics.iterdir()) == sorted(["llm.md"]))

    # --- 4. attachments ----------------------------------------------------
    rc = run(vault, topics, "--add", "zotero", "Zotero/Paper.md", "--tags", "Zotero")
    check("--add with attachment returns 0", rc == 0, f"rc={rc}")
    check("attachment copied", (topics / "attachments" / "ABC123.png").is_file())
    check("attachment bytes match",
          (topics / "attachments" / "ABC123.png").read_bytes()
          == (vault / "Zotero" / "attachments" / "ABC123.png").read_bytes())

    # --- 5. LOCAL-EDIT: repo copy hand-edited, vault untouched -------------
    edited = (topics / "llm.md").read_text(encoding="utf-8") + "\nmy own local note\n"
    (topics / "llm.md").write_text(edited, encoding="utf-8")
    check("state LOCAL-EDIT", state_of(topics, "llm.md") == "LOCAL-EDIT")
    rc = run(vault, topics, "--apply")
    check("--apply does not clobber local edit", "my own local note" in (topics / "llm.md").read_text(encoding="utf-8"))
    check("local edit still reported (rc=1)", rc == 1, f"rc={rc}")

    # --- 6. CONFLICT: vault changed AND repo copy hand-edited --------------
    (vault / "LLM" / "Basics.md").write_text("# LLM Basics\n\nrewritten in the vault\n", encoding="utf-8")
    check("state CONFLICT", state_of(topics, "llm.md") == "CONFLICT")
    before = (topics / "llm.md").read_text(encoding="utf-8")
    rc = run(vault, topics, "--apply")
    check("--apply on conflict returns 1", rc == 1, f"rc={rc}")
    check("original untouched on conflict", (topics / "llm.md").read_text(encoding="utf-8") == before)
    side = topics / "llm.seed-new.md"
    check("sidecar written", side.is_file())
    check("sidecar holds the incoming body", "rewritten in the vault" in side.read_text(encoding="utf-8"))
    check("sidecar is ignored by the loader", all(s["path"].name != side.name for s in seed.load_seeds(topics)))

    # --- 7. MISSING source -------------------------------------------------
    (topics / "hcf.md").write_text(
        "---\ntopic: hcf\nvault_source: HCF/None.md\nvault_sha256: x\nseed_sha256: y\n---\n\nbody\n",
        encoding="utf-8")
    check("state MISSING", state_of(topics, "hcf.md") == "MISSING")
    rc = run(vault, topics)
    check("report with missing source returns 1", rc == 1, f"rc={rc}")

    # --- 8. --add refuses to overwrite without --force ---------------------
    rc = run(vault, topics, "--add", "llm", "LLM/Basics.md")
    check("--add refuses existing file", rc == 2, f"rc={rc}")
    rc = run(vault, topics, "--add", "llm", "LLM/Basics.md", "--force")
    check("--add --force replaces", rc == 0 and state_of(topics, "llm.md") == "OK", f"rc={rc}")
finally:
    shutil.rmtree(root, ignore_errors=True)

print("─" * 60)
print(f"  All {CHECKS} checks passed.")
print("─" * 60)
