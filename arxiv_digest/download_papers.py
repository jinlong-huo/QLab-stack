#!/usr/bin/env python3
"""
Download arXiv papers listed in daily_digest.md into per-topic subfolders.

Each paper is routed by classify.py (keyword rules in config.SUBFOLDER_RULES)
to a topic subfolder under the destination root:

    LLM/moe  LLM/memory  LLM/agents  LLM/train  LLM/eval  LLM/inference  LLM/misc
    OCS/hardware  OCS/topology  OCS/algorithms  OCS/applications
    Distributed/      (top-level; collectives / distributed-training infra)

Usage:
    python3 arxiv_digest/download_papers.py                          # downloads to ~/Downloads/Paper
    python3 arxiv_digest/download_papers.py --dest /some/folder      # custom destination
    python3 arxiv_digest/download_papers.py --dry-run                # show subfolder + evidence, no download
    python3 arxiv_digest/download_papers.py --digest path/to/digest.md  # use a custom digest file
    make download ARGS="--dry-run"                                    # via Makefile

Routing decisions (subfolder + evidence keywords) are appended to
arxiv_digest/download_log.json for review and rule tuning.
"""

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Ensure the project root is on sys.path so `arxiv_digest` is importable
# when this script is run directly (python3 arxiv_digest/download_papers.py
# or via run_daily.sh from inside arxiv_digest/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from arxiv_digest import classify  # noqa: E402
from arxiv_digest import config    # noqa: E402

DIGEST_FILE = config.OUTPUT_FILE
DEFAULT_DEST = Path.home() / "Downloads" / "Paper"
# Base seconds between downloads, be polite. Kept as a module constant
# because verify_downloads.py imports it.
ARXIV_DELAY = config.ARXIV_PDF_DELAY
MAX_TITLE_CHARS = 80   # max chars of title in filename


def _pace_delay() -> float:
    """Jittered inter-download delay — constant intervals look like a bot."""
    j = config.ARXIV_PDF_DELAY_JITTER
    return ARXIV_DELAY * (1.0 - j + random.random() * 2 * j)


def sanitize_title(title: str, max_chars: int = MAX_TITLE_CHARS) -> str:
    """Convert a paper title into a safe filename fragment."""
    # remove anything not a word char, dash, or space; collapse spaces
    safe = re.sub(r"[^\w\s-]", "", title)
    safe = re.sub(r"\s+", "_", safe)
    safe = safe.strip("_")
    if len(safe) > max_chars:
        safe = safe[:max_chars].rstrip("_")
    return safe


def extract_papers(digest_path: Path) -> list[dict]:
    """Parse daily_digest.md and return a list of paper dicts:
      {id, title, author, year, section, keywords, ocs_keywords, snippet}
    section is 'LLM' (Main Digest / Carry-Over) or 'OCS' (OCS Spotlight).
    """
    if not digest_path.exists():
        print(f"Digest file not found: {digest_path}")
        return []

    content = digest_path.read_text(encoding="utf-8")

    papers = []
    seen = set()

    # Split into sections by ## headers
    sections = re.split(r"\n## (.+)\n", content)

    for i in range(1, len(sections), 2):
        section_title = sections[i].strip()
        section_body = sections[i + 1] if i + 1 < len(sections) else ""

        if "ocs" in section_title.lower() or "optical" in section_title.lower():
            section = "OCS"
        else:
            section = "LLM"

        # Split into individual papers by ### N. header
        entries = re.split(r"\n### \d+\. ", section_body)

        for entry in entries:
            # Host-agnostic: the digest link may point at arxiv.org or a mirror
            # (config.ARXIV_ABS_BASE_URL), so don't hardcode the hostname.
            link_match = re.search(
                r"\*\*Link:\*\*\s*(https?://[^\s/]+/abs/[^\s\n]+)", entry)
            if not link_match:
                continue

            paper_id = re.sub(r"^https?://[^\s/]+/abs/", "",
                              link_match.group(1)).strip()
            if paper_id in seen:
                continue
            seen.add(paper_id)

            title_match = re.search(r"^(.*)$", entry, re.MULTILINE)
            author_match = re.search(r"\*\*Author:\*\*\s*(.+)$", entry, re.MULTILINE)
            year_match = re.search(r"\*\*Year:\*\*\s*(.+)$", entry, re.MULTILINE)
            kw_match = re.search(r"\*\*Keywords:\*\*\s*(.+)$", entry, re.MULTILINE)
            ocs_kw_match = re.search(r"\*\*OCS Keywords:\*\*\s*(.+)$", entry, re.MULTILINE)
            snippet_match = re.search(
                r"\*\*Abstract snippet:\*\*\s*\n\n(.*?)\n\n---", entry, re.DOTALL
            )

            papers.append({
                "id": paper_id,
                "title": title_match.group(1).strip() if title_match else paper_id,
                "author": author_match.group(1).strip() if author_match else "Unknown",
                "year": year_match.group(1).strip() if year_match else "0000",
                "section": section,
                "keywords": [k.strip() for k in kw_match.group(1).split(",")] if kw_match else [],
                "ocs_keywords": [k.strip() for k in ocs_kw_match.group(1).split(",")] if ocs_kw_match else [],
                "snippet": snippet_match.group(1).strip() if snippet_match else "",
            })

    return papers


BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _pdf_hosts() -> list[str]:
    """PDF base hosts to try, primary first, then any configured mirrors."""
    hosts = [config.ARXIV_ABS_BASE_URL.rstrip("/")]
    for h in getattr(config, "ARXIV_PDF_FALLBACK_HOSTS", []):
        h = h.rstrip("/")
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def download_pdf(paper_id: str, dest_dir: Path, filename: str | None = None) -> bool:
    """Download a single arXiv paper PDF. Returns True on success.

    arXiv's CDN throttles burst-y automated fetches from shared campus IPs
    (SJTU CGNAT) with HTTP 406 "Not Acceptable" — typically starting around
    the 10th request in a tight loop.  It is a throttle, not a bad URL: the
    identical request succeeds seconds later.  So every retryable failure is
    retried with exponential backoff + jitter (config.ARXIV_PDF_*).

    The payload is validated against the ``%PDF-`` magic bytes so a CDN
    interstitial is never written to disk as a .pdf.
    """
    if filename is None:
        filename = f"{paper_id}.pdf"
    elif not filename.endswith(".pdf"):
        filename += ".pdf"

    dest_path = dest_dir / filename

    if dest_path.exists():
        print(f"  [skip] already exists: {filename}")
        return False

    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/pdf,application/x-pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{config.ARXIV_ABS_BASE_URL}/abs/{paper_id}",
    }

    attempts = config.ARXIV_PDF_MAX_RETRIES + 1
    last_err = "unknown error"

    for attempt in range(attempts):
        for host in _pdf_hosts():
            url = f"{host}/pdf/{paper_id}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=config.ARXIV_PDF_TIMEOUT) as resp:
                    pdf_data = resp.read()

                if not pdf_data.startswith(b"%PDF-"):
                    # CDN interstitial / HTML error page served with HTTP 200
                    last_err = (f"not a PDF ({len(pdf_data)}B, "
                                f"starts {pdf_data[:16]!r})")
                    continue

                dest_path.write_bytes(pdf_data)
                print(f"  [ok] downloaded: {filename}  ({len(pdf_data) // 1024} KB)")
                return True

            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code} {e.reason}"
                if e.code not in config.ARXIV_PDF_RETRY_STATUSES:
                    # 404 / 403 etc. — retrying cannot help
                    print(f"  [fail] {last_err} for {paper_id}")
                    return False
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"

        if attempt < attempts - 1:
            base = config.ARXIV_PDF_RETRY_BACKOFF
            delay = base[min(attempt, len(base) - 1)] * (0.75 + random.random() * 0.5)
            print(f"  [retry] {last_err[:60]} — "
                  f"attempt {attempt + 2}/{attempts}, waiting {delay:.0f}s...")
            time.sleep(delay)

    print(f"  [fail] {last_err} for {paper_id} (gave up after {attempts} attempts)")
    return False


def append_download_log(entries: list[dict]) -> None:
    """Append routing decisions to download_log.json (skip silently on error)."""
    log = []
    if config.DOWNLOAD_LOG.exists():
        try:
            log = json.loads(config.DOWNLOAD_LOG.read_text(encoding="utf-8"))
            if not isinstance(log, list):
                log = []
        except (json.JSONDecodeError, OSError):
            log = []
    log.extend(entries)
    config.DOWNLOAD_LOG.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description="Download arXiv papers from daily_digest.md")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"Destination folder (default: {DEFAULT_DEST})")
    parser.add_argument("--dry-run", action="store_true", help="Show routing decisions without downloading")
    parser.add_argument("--digest", type=Path, default=DIGEST_FILE, help=f"Path to digest file (default: {DIGEST_FILE})")
    args = parser.parse_args()

    digest_path = args.digest
    if not digest_path.exists():
        print(f"Digest file not found: {digest_path}")
        return

    papers = extract_papers(digest_path)

    if not papers:
        print("No papers found in digest.")
        return

    # Route every paper to its topic subfolder
    for p in papers:
        p["route"] = classify.classify_paper(
            p["title"], p["snippet"], p["keywords"], p["ocs_keywords"], p["section"]
        )

    print(f"\nFound {len(papers)} paper(s) in {digest_path.name}:\n")
    for i, p in enumerate(papers, 1):
        route = p["route"]
        ev = ", ".join(route["evidence"][:3]) if route["evidence"] else "no strong signal"
        print(f"  {i:02d}. [{route['path']:17s}] {p['author']}_{p['year']} | {p['title'][:80]}"
              f"{'...' if len(p['title']) > 80 else ''}")
        print(f"       {'':17s} ← {ev}")
    print()

    if args.dry_run:
        print("--dry-run enabled, no downloads made.")
        return

    args.dest.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    failed = 0
    consec_fails = 0
    log_entries = []
    for i, p in enumerate(papers, 1):
        route = p["route"]
        section_dir = args.dest / route["path"]
        section_dir.mkdir(parents=True, exist_ok=True)

        # Golden naming: Author_Year_ShortTitle_arXivID.pdf
        slug = sanitize_title(p["title"])
        filename = f"{p['author']}_{p['year']}_{slug}_{p['id']}.pdf"
        already = (section_dir / filename).exists()
        print(f"[{i}/{len(papers)}] {p['id']}  →  {route['path']}/", end=" ", flush=True)

        ok = download_pdf(p["id"], section_dir, filename=filename)
        if ok:
            downloaded += 1
            consec_fails = 0
        elif already:
            # Present on disk already — not a failure, don't cool down for it.
            consec_fails = 0
        else:
            failed += 1
            consec_fails += 1

        log_entries.append({
            "date": config.bj_today_str(),
            "id": p["id"],
            "title": p["title"],
            "path": route["path"],
            "evidence": route["evidence"],
            "fallback": route["fallback"],
            "downloaded": ok,
        })

        if i < len(papers):
            # Repeated failures mean the CDN flagged this IP — pause longer
            # instead of hammering it into a sustained block.
            if consec_fails >= config.ARXIV_PDF_COOLDOWN_AFTER_FAILS:
                cool = config.ARXIV_PDF_COOLDOWN_SECONDS
                print(f"  [cooldown] {consec_fails} consecutive failures — "
                      f"pausing {cool:.0f}s to let the rate-limit clear...")
                time.sleep(cool)
            time.sleep(_pace_delay())

    append_download_log(log_entries)

    print(f"\nDone. Downloaded {downloaded} / {len(papers)} paper(s) to {args.dest}/")
    if failed:
        print(f"  {failed} paper(s) failed — re-run to retry (already-downloaded "
              f"files are skipped).")
    print(f"Routing decisions logged to {config.DOWNLOAD_LOG.name}")


if __name__ == "__main__":
    main()