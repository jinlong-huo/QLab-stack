#!/usr/bin/env python3
"""Rename PDF papers to Author_Year_Title.pdf using authoritative metadata (v2).

Single canonical renamer (merged: v2 pipeline + v1's Semantic Scholar fallback).

Pipeline per PDF
  1. local extraction : pdfinfo (Title/Author/dates) + pdftotext (pages 1-2)
  2. identifiers      : arXiv id (from text / filename), DOI (from text)
  3. lookup           : arXiv API (by id) > Crossref (by DOI) > OpenAlex (title)
                       > Semantic Scholar (title) > local text read-off
  4. verification     : the returned title/author/year must be supported by the
                        PDF's own first page text (guards against wrong matches)
  5. propose name     : Author_Year_Title.pdf + confidence (high/medium/low)

Only high/medium confidence proposals are applied (--apply). Every lookup is
cached in .rename_cache_v2.json and every rename is logged in .rename_log_v2.jsonl
(use --revert to undo the last run).

The PDF library is NOT stored in this repo.  The paper root is resolved from
--root, then $QLAB_PAPER_ROOT, then DEFAULT_ROOT (/Users/Vir-G/Downloads/Paper).
The cache, the report and the undo log are tool state and always live next to
this script inside the repo - never inside the paper tree.

Usage (needs poppler + pdfminer.six, so run it with an interpreter that has
them; a clean py_compile does NOT mean this script can run)
  python3 arxiv_digest/rename_papers_v2.py            # report only, no changes
  python3 arxiv_digest/rename_papers_v2.py --limit 40 # only N unknown files
  python3 arxiv_digest/rename_papers_v2.py --apply    # perform the renames
  python3 arxiv_digest/rename_papers_v2.py --revert   # undo the last run
  python3 arxiv_digest/rename_papers_v2.py --dir OCS/hardware
  python3 arxiv_digest/rename_papers_v2.py --root /path/to/Paper
  python3 arxiv_digest/rename_papers_v2.py --file <path>  # debug one file
  python3 arxiv_digest/rename_papers_v2.py --acm      # only top-level ACM
                                                      #   download files
"""

import argparse
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_ROOT = '/Users/Vir-G/Downloads/Paper'
# State (cache / report / undo log) belongs to the tool, so it lives next to it.
STATE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('QLAB_PAPER_ROOT') or DEFAULT_ROOT
EXCLUDE_DIRS = {'_archive_seismic', '__pycache__'}
CACHE_PATH = os.path.join(STATE_DIR, '.rename_cache_v2.json')
REPORT_PATH = os.path.join(STATE_DIR, 'rename_report.tsv')
LOG_PATH = os.path.join(STATE_DIR, '.rename_log_v2.jsonl')
UA = 'paper-rename-v2/1.0 (mailto:paper.rename@example.com)'
MIN_YEAR, MAX_YEAR = 1900, 2026
# Bump when a purely local heuristic changes: cached 'self-ok' verdicts are
# dropped on load so the new rule actually takes effect (no --refresh needed).
CACHE_SCHEMA = 2

# Reuse the pipeline's configurable arXiv endpoint (mirror / GFW bypass) when
# the repo is importable; fall back to the plain legacy API otherwise.
_REPO_ROOT = os.path.dirname(STATE_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
try:
    from arxiv_digest import config as _config
    ARXIV_API_BASE = _config.ARXIV_API_BASE_URL.rstrip('/')
except Exception:
    ARXIV_API_BASE = 'https://export.arxiv.org'


def configure_paths(root=None):
    """Re-point the module globals after argparse (--root)."""
    global ROOT, CACHE_PATH, REPORT_PATH, LOG_PATH
    if root:
        ROOT = os.path.abspath(os.path.expanduser(root))
    CACHE_PATH = os.path.join(STATE_DIR, '.rename_cache_v2.json')
    REPORT_PATH = os.path.join(STATE_DIR, 'rename_report.tsv')
    LOG_PATH = os.path.join(STATE_DIR, '.rename_log_v2.jsonl')

_last_call = {'crossref': 0.0, 'openalex': 0.0, 'arxiv': 0.0, 's2': 0.0}
_DELAY = {'crossref': 0.1, 'openalex': 0.4, 'arxiv': 1.0, 's2': 1.1}


# ────────────────────────────────────────────────────────────── helpers ──────
def log(msg):
    print(msg, file=sys.stderr)


def http_json(url, kind):
    if kind in DISABLED:
        return None
    headers = {'User-Agent': UA}
    key = os.environ.get('S2_API_KEY')
    if kind == 's2' and key:
        headers['x-api-key'] = key
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(1, 3):
        wait = _DELAY[kind] - (time.time() - _last_call[kind])
        if wait > 0:
            time.sleep(wait)
        _last_call[kind] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3 * attempt)
                continue
            log(f'  ! {kind} HTTP {e.code}')
            _bust(kind)
            return None
        except Exception as e:
            if attempt == 2:
                log(f'  ! {kind} request failed: {e}')
                _bust(kind)
            else:
                time.sleep(0.5)
    return None


def _bust(kind):
    FAILS[kind] = FAILS.get(kind, 0) + 1
    if FAILS[kind] >= 3:
        DISABLED.add(kind)
        log(f'  !! {kind} disabled for this run (repeated failures)')


def http_text(url, kind):
    wait = _DELAY[kind] - (time.time() - _last_call[kind])
    if wait > 0:
        time.sleep(wait)
    _last_call[kind] = time.time()
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read().decode('utf-8', 'replace')
            return data
        except Exception as e:
            if attempt == 2:
                log(f'  ! {kind} request failed: {e}')
                _bust(kind)
            else:
                time.sleep(0.5)
    return None


def norm_alnum(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def fold_spaced_letters(line):
    """'A T IME S ERIES' -> 'ATIME SERIES' (fix PDF letter-spaced titles)."""
    toks = line.split()
    if sum(1 for t in toks if len(t) == 1) < 3:
        return None
    out = []
    for t in toks:
        if out and (len(out[-1]) <= 1 or len(t) == 1):
            out[-1] = out[-1] + t
        else:
            out.append(t)
    return ' '.join(out)


def sanitize(s):
    if not s:
        return 'Unknown'
    s = str(s).strip()
    s = s.replace('ø', 'o').replace('ł', 'l')
    s = re.sub(r'[\\/:*?"<>|]', '', s)
    s = re.sub(r"[\s\-–—,;.!@#$%^&*()+=\[\]{}|~`'\"]+", '_', s)
    s = re.sub(r'[^\x00-\x7F]+', '', s)
    s = re.sub(r'_+', '_', s).strip('_')
    if len(s) > 150:
        s = s[:147] + '...'
    return s or 'Unknown'


def file_key(path):
    """Cheap stable content key (size + hash of first/last 256KB)."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            head = f.read(262144)
            f.seek(max(0, size - 262144))
            tail = f.read(262144)
        return hashlib.sha1(head + tail + str(size).encode()).hexdigest()[:20]
    except Exception:
        return None


# ─────────────────────────────────────────────────────── local extraction ────
def pdfinfo(path):
    try:
        out = subprocess.run(['pdfinfo', path], capture_output=True, text=True,
                             timeout=60).stdout
    except Exception:
        return {}
    info = {}
    for line in out.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            info[k.strip()] = v.strip()
    return info


def pdf_text(path, first=1, last=2):
    try:
        return subprocess.run(['pdftotext', '-layout', '-f', str(first), '-l',
                               str(last), path, '-'],
                              capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return ''


DOI_RE = re.compile(r'\b(10\.\d{4,9}/[^\s"<>]+)', re.I)


def find_doi(text):
    """Return (doi, where) preferring an explicit 'DOI:' label."""
    if not text:
        return None
    m = re.search(r'(?:doi|DOI)\s*[:.]?\s*(10\.\d{4,9}/[^\s"<>]+)', text)
    if not m:
        m = DOI_RE.search(text)
    if not m:
        return None
    doi = m.group(1).rstrip('.,;)]>')
    doi = re.sub(r'[.,;]$', '', doi)
    return doi


ARXIV_RE = re.compile(r'arXiv:\s*((?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))\s*(v\d+)?', re.I)


def find_arxiv_text(text):
    if not text:
        return None
    m = ARXIV_RE.search(text)
    return m.group(1) if m else None


def find_arxiv_filename(name):
    m = re.search(r'(?:0[7-9]|1\d|2[0-6])(?:0[1-9]|1[0-2])\.\d{4,5}', name)
    return m.group(0) if m else None


VENUE_TITLE = re.compile(
    r'^(\d+(?:st|nd|rd|th)\s+(?:USENIX|IEEE|ACM|Annual|International|Symposium|'
    r'Conference)|Proceedings|Proc\.\s|Preface|Front Matter|Table of Contents|'
    r'Editorial|Welcome|Message from|Technical Report|Abstract Proceedings|'
    r'Program Committee|Keynote|Poster Session|Tutorial|Session \d|Index of|'
    r'Contents|Cover|Half[- ]title|Title Page|Copyright Page|List of)', re.I)

STOPWORD_RE = re.compile(
    r'\b(the|a|an|of|for|and|with|using|via|in|on|to|from|by|is|are|at|over|'
    r'under|between|towards|toward|based|efficient|scalable|deep|learning|'
    r'network|networks|optical|system|systems|model|models|data)\b', re.I)


FRONT_MATTER = re.compile(
    r'is sponsored by|USENIX Association|All rights reserved|ISBN|'
    r'https?://|@|Technical Program|Program Committee|Organizing Committee|'
    r'General Chair|\bpp\.\s*\d|\beditor(s)?\b|\bcopyright\b|'
    r'Permission to make digital|reprint of|Reprinted from', re.I)


def bad_api_title(t):
    """Reject results that are front matter or a bare author list."""
    if not t:
        return True
    if FRONT_MATTER.search(t):
        return True
    if VENUE_TITLE.match(t.strip()):
        return True
    w = t.split()
    if len(w) >= 5:
        caps = sum(1 for x in w if x[:1].isupper())
        if caps == len(w) and not STOPWORD_RE.search(t):
            return True
    if len(t) < 10 or not re.search(r'[A-Za-z]{3,}\s+[A-Za-z]{3,}', t):
        return True
    if re.search(r'\b(invoice|receipt|purchase order|order summary|statement)\b', t, re.I):
        return True
    if len(w) <= 4 and re.match(
            r'^(design and implementation|design|implementation|evaluation|'
            r'analysis|overview|summary|preface|conclusions?|discussion|'
            r'methods?|results?|background|related work)$', t.strip(), re.I):
        return True
    return False


GENERIC_TITLE = re.compile(
    r'^(introduction|abstract|conclusion|related work|references|acknowledge?ments?|'
    r'preface|contents|table of contents|index|appendix|chapter\s*\d+|part\s*\d+|'
    r'section\s*\d+|proceedings|front matter|editorial|prelim|title page)\b', re.I)

JUNK_LINE = re.compile(
    r'@|https?://|www\.|abstract|index terms|keywords?\b|arXiv:|vol\.\s*\d|no\.\s*\d|'
    r'pp\.\s*\d|©|copyright|\bIEEE\b|\bACM\b|proceedings|\btransactions\b|journal of|'
    r'conference on|preprint|submitted|accepted|manuscript|downloaded|citation|'
    r'\bDOI\b|received|published as|technical report|department of|university|'
    r'institute|laboratory|\binc\b|\bcorp\b|school of|\betc\.\b|figure\s*\d|table\s*\d',
    re.I)


def looks_author_line(line):
    if ',' not in line and ' and ' not in line:
        return False
    words = [w for w in re.split(r'[,\s]+', line) if w]
    if not 2 <= len(words) <= 14:
        return False
    caps = sum(1 for w in words if w[:1].isupper())
    return caps / len(words) > 0.7


class _Alarm(Exception):
    pass


def _alarm(signum, frame):
    raise _Alarm()


def font_lines(path, page=0, timeout=30):
    """Page-1 text boxes with their dominant font size (pdfminer)."""
    # pdfminer chatters about malformed font descriptors on almost every
    # scanned/proceedings PDF; keep the console readable.
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTChar, LTTextContainer
    except ImportError as e:                 # preflight() normally catches this
        log(f'  ! pdfminer unavailable ({e}); using pdftotext heuristics only')
        return []
    items = []
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout)
    try:
        for pl in extract_pages(path, page_numbers=[page]):
            for el in pl:
                if not isinstance(el, LTTextContainer):
                    continue
                chars = []

                def walk(node, acc=chars):
                    if isinstance(node, LTChar):
                        if node.get_text().strip():
                            acc.append(node)
                    elif hasattr(node, '__iter__'):
                        for c in node:
                            walk(c, acc)
                walk(el)
                if not chars:
                    continue
                sizes = {}
                for ch in chars:
                    k = round(ch.size, 1)
                    sizes[k] = sizes.get(k, 0) + 1
                fs = max(sizes, key=sizes.get)
                txt = re.sub(r'\s+', ' ', el.get_text()).strip()
                if txt:
                    items.append({'size': fs, 'text': txt, 'y': el.y1, 'x': el.x0,
                                  'h': max(0.1, el.y1 - el.y0)})
    except Exception:
        return []
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    return items


def font_titles(path):
    """Title candidates from the largest type on page 1 (most reliable)."""
    items = font_lines(path)
    good = []
    for it in items:
        t = it['text']
        if len(t) < 8 or len(t) > 250:
            continue
        alpha = sum(c.isalpha() for c in t)
        if alpha < 0.5 * len(t):
            continue
        if JUNK_LINE.search(t) or looks_author_line(t):
            continue
        if not 1 <= len(t.split()) <= 30:
            continue
        good.append(it)
    if not good:
        return []
    mx = max(it['size'] for it in good)
    top = [it for it in good if it['size'] >= mx * 0.82]
    top.sort(key=lambda it: (-it['y'], it['x']))
    out = []
    # group vertically adjacent lines (multi-line titles)
    groups, cur = [], [top[0]]
    for prev, nxt in zip(top, top[1:]):
        if (prev['y'] - prev['h']) - nxt['y'] < 0.9 * prev['h']:
            cur.append(nxt)
        else:
            groups.append(cur)
            cur = [nxt]
    groups.append(cur)
    for g in groups[:3]:
        out.append(' '.join(x['text'] for x in g[:3]).strip(' -–_:,.').rstrip(':'))
    for it in top[:3]:
        out.append(it['text'].strip(' -–_:,.').rstrip(':'))
    seen, res = set(), []
    for t in out:
        t = re.sub(r'\s+', ' ', t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            res.append(t)
    return res


def title_candidates(text, meta_title=None, extra=None):
    """Ordered list of plausible title strings from page 1 (+ PDF metadata)."""
    cands = []
    for t in (extra or []):
        if 12 <= len(t) <= 220 and t not in cands:
            cands.append(t)

    def add(t):
        if not t:
            return
        t = re.sub(r'\s+', ' ', t).strip(' -–_:,.')
        if not (12 <= len(t) <= 220):
            return
        if GENERIC_TITLE.match(t) or len(t.split()) < 2:
            return
        if t not in cands:
            cands.append(t)

    # 1) embedded metadata title (usually the cleanest) when it looks real
    if meta_title:
        mt = meta_title.strip()
        bad = (not mt or mt.lower().endswith(('.pdf', '.tex', '.doc', '.docx'))
               or 'Microsoft Word' in mt or len(mt) < 12
               or re.fullmatch(r'[\d\-_x\.]+', mt) or 'untitled' in mt.lower())
        if not bad:
            add(mt)

    # 2) heuristic over the first lines of page 1
    lines = [l.strip() for l in (text or '').splitlines()]
    lines = [l for l in lines if l][:40]
    scored = []
    for i, line in enumerate(lines):
        if JUNK_LINE.search(line) or looks_author_line(line):
            continue
        if len(line) < 12 or len(line) > 200:
            continue
        alpha = sum(c.isalpha() for c in line)
        if alpha < 0.55 * len(line):
            continue
        words = line.split()
        if not 2 <= len(words) <= 28:
            continue
        score = 1.0
        if i < 12:
            score += 0.5
        if i < 4:
            score += 0.2
        upper = sum(1 for w in words if w[:1].isupper())
        if upper / len(words) >= 0.6:
            score += 0.35
        if line.isupper():
            score -= 0.6
        if line.endswith('.'):
            score -= 0.4
        digits = sum(c.isdigit() for c in line)
        if digits > 0.08 * len(line):
            score -= 0.6
        if 40 <= len(line) <= 140:
            score += 0.3
        if re.search(r'\b(the|a|an|of|for|and|with|using|via|in|on|to)\b', line, re.I):
            score += 0.25
        scored.append((score, i, line))

    scored.sort(key=lambda x: (-x[0], x[1]))
    for _, i, line in scored[:4]:
        add(line)
        # multi-line titles: join with the following line when it continues
        for j in range(i + 1, min(i + 3, len(lines))):
            nxt = lines[j]
            if JUNK_LINE.search(nxt) or looks_author_line(nxt) or len(nxt) < 4:
                break
            add(line + ' ' + nxt)
            if len(line) > 45:
                break

    # 3) de-spaced variants (PDFs with letter-spaced / tracked-out titles)
    for c in list(cands):
        v = fold_spaced_letters(c)
        if v and v != c:
            add(v)
    return cands


# ──────────────────────────────────────────────────────────────── lookups ────
def crossref_meta(doi):
    d = http_json(f'https://api.crossref.org/works/{urllib.parse.quote(doi)}'
                  f'?mailto=paper.rename@example.com', 'crossref')
    if not d or d.get('status') != 'ok':
        return None
    m = d['message']
    title = (m.get('title') or [''])[0]
    if FRONT_MATTER.search(title):     # DOI lookups are authoritative; only
        return None                    # proceedings front matter is rejected
    authors = [f"{a.get('family', '')}" for a in m.get('author', [])
               if a.get('family')]
    year = None
    for key in ('published-print', 'published-online', 'issued', 'created'):
        parts = ((m.get(key) or {}).get('date-parts') or [[]])[0]
        if parts and parts[0]:
            year = parts[0]
            break
    venue = (m.get('container-title') or [''])[0]
    return {'title': title, 'authors': authors, 'year': year, 'venue': venue,
            'source': f'crossref:{doi}'}


ARXIV_ID_CACHE = {}
FAILS = {}
DISABLED = set()


def arxiv_meta(ids):
    """Batch arXiv lookup (per-id memoised). Returns {id: meta}."""
    out = {}
    ids = [i for i in ids if i]
    fresh = [i for i in ids if i not in ARXIV_ID_CACHE]
    if fresh:
        _arxiv_fetch(fresh)
    for i in ids:
        if i in ARXIV_ID_CACHE:
            out[i] = ARXIV_ID_CACHE[i]
    return out


def _arxiv_fetch(ids):
    """Query the arXiv API in batches and memoise the results."""
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        url = (ARXIV_API_BASE + '/api/query?id_list=' + ','.join(batch) +
               f'&max_results={len(batch)}')
        xml = http_text(url, 'arxiv')
        if not xml:
            continue
        try:
            root = ET.fromstring(xml)
        except Exception:
            continue
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        for e in root.findall('a:entry', ns):
            eid = (e.findtext('a:id', '', ns) or '').rsplit('/', 1)[-1]
            base = re.sub(r'v\d+$', '', eid)
            title = re.sub(r'\s+', ' ', e.findtext('a:title', '', ns) or '').strip()
            authors = [a.findtext('a:name', '', ns)
                       for a in e.findall('a:author', ns)]
            surnames = []
            for a in authors:
                if a and a.strip():
                    surnames.append(a.strip().split()[-1])
            pub = e.findtext('a:published', '', ns) or ''
            ym = re.match(r'(\d{4})', pub)
            year = int(ym.group(1)) if ym else None
            ARXIV_ID_CACHE[base] = {'title': title, 'authors': surnames,
                         'year': year,
                         'venue': 'arXiv preprint', 'source': f'arxiv:{base}',
                         'doi': (e.findtext('arxiv:doi', '', {'arxiv':
                                 'http://arxiv.org/schemas/atom'}) or '').strip(),
                         'journal_ref': (e.findtext('arxiv:journal_ref', '',
                                 {'arxiv': 'http://arxiv.org/schemas/atom'}) or '').strip()}


# year / author hints taken from the PDF's own first page
VENUE_RE = re.compile(
    r'\b(?:ICASSP|OFC|ECOC|ICLR|NeurIPS|NIPS|ICML|CVPR|ICCV|ECCV|ACL|EMNLP|NAACL|'
    r'AAAI|IJCAI|SIGCOMM|NSDI|OSDI|SOSP|ATC|USENIX|EuroSys|ISCA|MICRO|HPCA|DAC|'
    r'INFOCOM|GLOBECOM|ICC|JLT|PTL|OE|Optica|Nature|Science|IEEE|ACM|OSA|SPIE|'
    r'Proc\.|Proceedings|Transactions|Conference|Workshop|Symposium|Journal|'
    r'Letters|Express|Photonics)\b', re.I)


def year_from_text(text):
    """Best-effort publication year printed on page 1."""
    if not text:
        return None, 0.0
    lines = [l.strip() for l in text.splitlines() if l.strip()][:60]
    cands = []
    for i, line in enumerate(lines):
        for m in re.finditer(r'\b((?:19|20)\d{2})\b', line):
            y = int(m.group(1))
            if not (MIN_YEAR <= y <= MAX_YEAR):
                continue
            score = 1.0
            if VENUE_RE.search(line):
                score += 1.0
            if '©' in line or 'copyright' in line.lower() or 'IEEE' in line:
                score += 0.6
            if re.search(r'\bVOL\.|\bNO\.|\bpp\.|, (?:Jan|Feb|Mar|Apr|May|Jun|Jul|'
                         r'Aug|Sep|Oct|Nov|Dec)', line, re.I):
                score += 0.6
            score += max(0.0, 0.4 - i * 0.03)          # earlier lines preferred
            cands.append((score, y))
    if not cands:
        return None, 0.0
    cands.sort(key=lambda x: -x[0])
    # majority vote among the best-scoring years
    best_score = cands[0][0]
    votes = {}
    for score, y in cands:
        if score >= best_score - 0.4:
            votes[y] = votes.get(y, 0) + score
    y = max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return y, round(best_score, 2)


FILLER_WORDS = {'many', 'this', 'that', 'these', 'those', 'with', 'from',
                'using', 'based', 'toward', 'towards', 'optimal', 'efficient',
                'scalable', 'novel', 'deep', 'learning', 'network', 'networks',
                'system', 'systems', 'model', 'models', 'data', 'results',
                'abstract', 'index', 'terms', 'introduction', 'conclusion',
                'university', 'institute', 'department', 'laboratory',
                'the', 'however', 'moreover', 'furthermore', 'centralized',
                'design', 'implementation', 'implementation', 'also', 'our',
                'we', 'this', 'paper', 'propose', 'proposed', 'show', 'shows',
                'first', 'second', 'finally', 'recent', 'recently'}


def plausible_surname(w):
    return bool(w) and len(w) > 2 and w.lower() not in FILLER_WORDS and \
        re.fullmatch(r"[A-Za-z][A-Za-z\-']+", w)

NAME_STOP = {'unknown', 'anonymous', 'eds', 'ed', 'auth', 'author', 'authors',
             'paper', 'final', 'public', 'draft', 'preprint', 'main', 'v1', 'v2',
             'sigcomm', 'osdi', 'sosp', 'nsdi', 'techreport', 'report', 'invoice',
             'chapter', 'book', 'the', 'and', 'for', 'with'}

# Words v1's renamer mistook for surnames (it used the first word of the title
# when it had no metadata), plus the truncation artefacts seen in the library
# ("EARNING_2024_...", "IMULATE_2021_...").  A name built from one of these is
# never accepted as already-correct.
BOGUS_AUTHORS = {
    'transformers', 'learning', 'network', 'interface', 'neural', 'training',
    'understanding', 'for', 'paper', 'use', 'earth', 'shapes', 'record',
    'topology', 'vector', 'method', 'group', 'scheduling', 'ultimodal',
    'geometry', 'multimodal', 'arge', 'computer', 'deep', 'model', 'design',
    'signal', 'storage', 'math', 'classification', 'chat', 'guage',
    'switching', 'networking', 'artificial', 'ranking', 'earning', 'language',
    'environment', 'extrapolation', 'heterogeneous', 'numbers', 'dottorato',
    'article', 'imulate', 'ofattentionresiduals', 'cache', 'cacheppt'}


def bad_author_token(surname, title):
    """True when the name's author part is really a title word (v1 mangling).

    'Learning_2021_Fed_ensemble_...' passes a naive 'the token appears on page
    1' test because 'learning' is ordinary prose there, so the check has to be
    about the token itself and about the title stored in the filename.
    """
    low = (surname or '').lower()
    if not low or low in BOGUS_AUTHORS or low in FILLER_WORDS or low in NAME_STOP:
        return True
    return bool(re.search(r'\b' + re.escape(low) + r'\b',
                          (title or '').replace('_', ' '), re.I))


def author_from_filename(name):
    """Recover an author surname hidden in the current filename (books etc.)."""
    stem = re.sub(r'\.pdf$', '', name, flags=re.I)
    stem = re.sub(r'_\d{4}_', '_', stem)
    toks = [t for t in re.split(r'[_\-\s]+', stem) if t]
    for i, t in enumerate(toks):
        if not re.fullmatch(r"[A-Z][a-z]{2,}|[A-Z][a-z]+\-[A-Z][a-z]+", t):
            continue
        if t.lower() in NAME_STOP or t.lower() in FILLER_WORDS:
            continue
        if i and re.fullmatch(r'[A-Z][a-z]{2,}', toks[i - 1]) and \
                toks[i - 1].lower() not in NAME_STOP:
            return t          # Given Family
        if i + 1 < len(toks) and re.fullmatch(r'[A-Z][a-z]{2,}', toks[i + 1]) and \
                toks[i + 1].lower() not in NAME_STOP:
            return t
    return None


def author_tokens(text):
    """Capitalised name tokens of the first plausible author line on page 1."""
    if not text:
        return []
    lines = [l.strip() for l in text.splitlines() if l.strip()][:25]
    bad = re.compile(r'universit|institute|department|laborator|school|college|'
                     r'editor|member|fellow|academy|science|engineering|research|'
                     r'@|\d|http|abstract', re.I)
    for line in lines:
        if bad.search(line):
            continue
        if not (',' in line or ' and ' in line or '*' in line or '†' in line
                or '∗' in line):
            continue
        toks = [t.strip('*,†‡§¶#∗✝ \'"_.') for t in re.split(r'\s+', line)]
        toks = [t for t in toks if len(t) >= 2 and t[:1].isupper()
                and re.fullmatch(r"[A-Za-zÀ-ÿ'\-]+", t)]
        if len(toks) < 2:
            continue
        caps = sum(1 for t in toks if t[:1].isupper())
        if caps < 2:
            continue
        return toks
    return []


def author_from_text(text):
    """First plausible author surname from page 1 (Given Family order)."""
    toks = author_tokens(text)
    if toks:
        # Given Family  (skip initials such as "T." or "D.")
        i = 0
        while i < len(toks) and len(toks[i]) <= 2 and toks[i].isupper():
            i += 1
        while i + 1 < len(toks):
            cand = toks[i + 1]
            if plausible_surname(cand):
                return cand
            i += 1
    return None


def fix_author_only(name, text, page1, info):
    """Keep title+year of the current name, replace only a bogus author.

    Used when neither an API record nor local metadata could be established but
    the current name's title and year check out against the PDF.
    """
    m = NAME_RE.match(name)
    if not m:
        return None
    author, year, title = m.group(1), int(m.group(2)), m.group(3)
    if text_support(title.replace('_', ' '), page1) < 0.8:
        return None
    if not (MIN_YEAR <= year <= MAX_YEAR):
        return None
    ty, tscore = year_from_text(page1)
    mc = re.search(r'((?:19|20)\d{2})', info.get('CreationDate', ''))
    cy = int(mc.group(1)) if mc else None
    if not (year == ty or (cy and abs(year - cy) <= 1) or str(year) in page1):
        return None
    cur = re.sub(r'_et_al$', '', author).split('_')[0]
    if cur.lower() not in FILLER_WORDS and cur.lower() not in NAME_STOP and \
            norm_alnum(cur) in norm_alnum(page1):
        return None                      # the current author looks genuine
    toks = author_tokens(text)
    if len(toks) < 2:
        return None
    surname = None
    i = 0
    while i < len(toks) and len(toks[i]) <= 2 and toks[i].isupper():
        i += 1
    while i + 1 < len(toks):
        cand = toks[i + 1]
        if plausible_surname(cand):
            surname = cand
            break
        i += 1
    if not surname or norm_alnum(surname) not in norm_alnum(page1):
        return None
    authors = [surname, surname] if len(toks) >= 4 else [surname]
    return {'title': title.replace('_', ' '), 'authors': authors, 'year': year,
            'venue': '(from PDF)', 'source': 'fix-author'}


def local_meta(text, info, page1=None, font_titles_path=None):
    """Fallback metadata built purely from the PDF itself."""
    src = page1 or text
    cands = [c for c in title_candidates(src, info.get('Title', ''),
                                         font_titles_path) if not bad_api_title(c)]
    title = cands[0] if cands else None
    if not title or len(title) > 200:
        return None
    m = re.search(r'((?:19|20)\d{2})', info.get('CreationDate', ''))
    cyear = int(m.group(1)) if m else None
    y, yscore = year_from_text(src)
    # a year printed on page 1 is only trusted when it agrees with the PDF's
    # own creation date (within a year), otherwise the document date wins
    if y is not None and cyear and abs(y - cyear) > 1:
        y, yscore = None, 0.0
    if y is None:
        if cyear and MIN_YEAR <= cyear <= MAX_YEAR:
            y, yscore = cyear, 0.3
    author = author_from_text(src)
    if not author:
        ia = (info.get('Author') or '').strip()
        if ia and not re.search(r'user|admin|ozz|owner', ia, re.I):
            toks = [t for t in re.split(r'[;,\s]+', ia) if len(t) >= 2]
            if toks:
                author = toks[-1]
    if not author or not y:
        return None
    return {'title': title, 'authors': [author], 'year': y, 'venue': '(from PDF)',
            'source': 'local', 'yconf': yscore}


def crossref_search(query, n=5):
    """Crossref bibliographic search (covers IEEE/OSA/SPIE conference papers)."""
    url = ('https://api.crossref.org/works?query.bibliographic=' +
           urllib.parse.quote(query[:250]) + f'&rows={n}&mailto=paper.rename@example.com')
    d = http_json(url, 'crossref')
    if not d:
        return []
    res = []
    for m in d.get('message', {}).get('items', []):
        title = (m.get('title') or [''])[0]
        if bad_api_title(title):
            continue
        authors = [a.get('family', '') for a in m.get('author', []) if a.get('family')]
        year = None
        for key in ('published-print', 'published-online', 'issued', 'created'):
            parts = ((m.get(key) or {}).get('date-parts') or [[]])[0]
            if parts and parts[0]:
                year = parts[0]
                break
        res.append({'title': title, 'authors': authors, 'year': year,
                    'venue': (m.get('container-title') or [''])[0],
                    'doi': m.get('DOI', ''), 'source': 'crossref-search'})
    return res


def openalex_meta(query, n=5):
    q = urllib.parse.quote(query[:300])
    d = http_json(f'https://api.openalex.org/works?search={q}&per-page={n}'
                  f'&mailto=paper.rename@example.com', 'openalex')
    if not d:
        return []
    res = []
    for w in d.get('results', []):
        title = w.get('display_name') or w.get('title') or ''
        if bad_api_title(title):
            continue
        authors = []
        for a in (w.get('authorships') or []):
            name = (a.get('author') or {}).get('display_name') or ''
            if name.strip():
                authors.append(name.strip().split()[-1])
        res.append({'title': title, 'authors': authors,
                    'year': w.get('publication_year'),
                    'venue': ((w.get('primary_location') or {}).get('source') or
                              {}).get('display_name') or '',
                    'doi': (w.get('doi') or '').replace('https://doi.org/', ''),
                    'source': 'openalex'})
    return res


def s2_meta(query, n=5):
    """Semantic Scholar title search (ported from rename_papers.py v1).

    Free tier ~100 req/5min, so it runs only after arXiv/Crossref/OpenAlex
    have all failed. Returns a list of meta dicts shaped like openalex_meta.
    """
    q = urllib.parse.quote((query or '')[:300])
    d = http_json('https://api.semanticscholar.org/graph/v1/paper/search'
                  f'?query={q}&limit={n}'
                  '&fields=title,authors,year,venue,externalIds', 's2')
    if not d:
        return []
    res = []
    for p in d.get('data', []):
        title = p.get('title') or ''
        if bad_api_title(title):
            continue
        authors = []
        for a in (p.get('authors') or []):
            name = (a.get('name') or '').strip()
            if name:
                authors.append(re.sub(r'[^a-zA-Z\-_]', '', name.split()[-1]))
        doi = (p.get('externalIds') or {}).get('Doi') or ''
        res.append({'title': title, 'authors': authors,
                    'year': p.get('year'),
                    'venue': p.get('venue') or '',
                    'doi': doi, 'source': 's2'})
    return res


# ───────────────────────────────────────────────────────────── verification ──
def text_support(api_title, text):
    """Fraction of API title tokens (len>=4) found in the page text."""
    page = norm_alnum(text or '')
    toks = [t for t in re.split(r'[^a-zA-Z0-9]+', api_title or '') if len(t) >= 4]
    if not toks:
        return 0.0
    hit = sum(1 for t in toks if norm_alnum(t) in page)
    return hit / len(toks)


def verify(meta, text, pdf_dates):
    tsup = text_support(meta['title'], text)
    page = norm_alnum(text or '')
    surname = norm_alnum((meta.get('authors') or [''])[0])
    aok = bool(surname) and surname in page
    year = meta.get('year')
    ytxt = bool(year) and str(year) in (text or '')
    ypdf = False
    if year:
        ypdf = any(str(year) in d or str(int(year) - 1) in d for d in pdf_dates if d)
    return tsup, aok, (ytxt or ypdf)


def confidence_of(meta, tsup, aok, ytxt, how):
    year = meta.get('year')
    if not meta.get('title') or not year or not (MIN_YEAR <= year <= MAX_YEAR):
        return 'low'
    if not meta.get('authors'):
        return 'low'
    if how in ('doi', 'arxiv-text') and tsup >= 0.55:
        return 'high'
    if tsup >= 0.8 and aok and ytxt:
        return 'high'
    if tsup >= 0.75 and (aok or ytxt):
        return 'medium'
    return 'low'


# ──────────────────────────────────────────────────────────────── naming ─────
def author_part(meta, current_name):
    surnames = [a for a in (meta.get('authors') or []) if a.strip()]
    if not surnames:
        return None
    first = sanitize(surnames[0])
    multi = len(surnames) > 1
    new = f"{first}_et_al" if multi else first
    # keep the existing author segment when it already matches (avoid churn)
    m = re.match(r'^([A-Za-z][A-Za-z\-]*(?:_[A-Za-z][A-Za-z\-]*)*)_(\d{4})_',
                 current_name)
    if m:
        cur = m.group(1)
        cur_first = re.sub(r'_et_al$', '', cur)
        if norm_alnum(cur_first) == norm_alnum(first):
            return cur
    return new


def propose(meta, current_name):
    ap = author_part(meta, current_name)
    if not ap:
        return None
    year = meta.get('year') or 0
    title = sanitize(meta['title'])
    return f"{ap}_{year:04d}_{title}.pdf" if year else f"{ap}_{title}.pdf"


# ─────────────────────────────────────────────────────────────────── main ────
NAME_RE = re.compile(r'^([A-Za-z][A-Za-z\-]*(?:_[A-Za-z][A-Za-z\-]*)*)_(\d{4})_(.+)\.pdf$')


def self_check(path, info, page1):
    """Cheap check whether the current name already agrees with the PDF.

    Returns True when title, author and year are all supported by page 1
    (avoids spending API calls on files that are already correct).
    """
    m = NAME_RE.match(os.path.basename(path))
    if not m:
        return False
    author, year, title = m.group(1), int(m.group(2)), m.group(3)
    if text_support(title.replace('_', ' '), page1) < 0.8:
        return False
    surname = re.sub(r'_et_al$', '', author).split('_')[0]
    if len(surname) < 2 or bad_author_token(surname, title):
        return False
    if norm_alnum(surname) not in norm_alnum(page1):
        return False
    if re.search(r'Unknown', author, re.I):
        return False
    ty, tscore = year_from_text(page1)
    if ty == year and tscore >= 1.2:
        return True
    mc = re.search(r'((?:19|20)\d{2})', info.get('CreationDate', ''))
    if mc and int(mc.group(1)) == year:
        return True
    return False


def process(path, cache, args):
    name = os.path.basename(path)
    key = file_key(path)
    if re.search(r'invoice|receipt|statement', os.path.basename(path), re.I):
        return {'name': os.path.basename(path), 'result': 'not-paper'}
    if key and key in cache and not args.refresh:
        return cache[key]

    info = pdfinfo(path)
    page1 = pdf_text(path, 1, 1)
    text = page1 + '\n' + pdf_text(path, 2, 3)
    meta_title = info.get('Title', '')
    pdf_dates = [info.get('CreationDate', ''), info.get('ModDate', '')]
    if not args.include_arxiv and find_arxiv_filename(name):
        rec = {'name': name, 'result': 'skip-arxiv'}
        if key:
            cache[key] = rec
        return rec
    if not args.all and self_check(path, info, page1):
        rec = {'name': name, 'result': 'self-ok', 'path_hint': path,
               'schema': CACHE_SCHEMA}
        if key:
            cache[key] = rec
        return rec
    fcands = font_titles(path)

    rec = {'name': name, 'meta_title': meta_title, 'info_author':
           info.get('Author', ''),
           'cands': title_candidates(page1, meta_title, fcands)[:4],
           'has_text': bool(text.strip())}

    # ── identifiers
    aid_text = find_arxiv_text(text)
    doi_p1 = find_doi(page1)
    doi = doi_p1 or find_doi(text)
    # ACM download filenames embed the DOI: Unknown_2026_1851182_1851222.pdf
    # -> 10.1145/1851182.1851222
    doi_fn = doi_libgen = None
    mfn = re.match(r'^\w+_\d{4}_(\d{6,7})_(\d{6,7})\.pdf$', name)
    if mfn:
        doi_fn = f'10.1145/{mfn.group(1)}.{mfn.group(2)}'
        rec['doi_fn'] = doi_fn
    mlg = re.search(r'10_(\d{4,5})_((?:\d|_)+?)(?:_libgen|_compressed|\.pdf|$)', name)
    if mlg:
        tail = mlg.group(2).strip('_').replace('_', '-')
        if re.fullmatch(r'[\d.-]+', tail):
            doi_libgen = f'10.{mlg.group(1)}/{tail}'
            rec['doi_libgen'] = doi_libgen
    if re.search(r'invoice|receipt|statement', name, re.I):
        rec['not_paper'] = True
    aid_name = find_arxiv_filename(name)
    rec['fcands'] = fcands[:3]
    rec['arxiv_text'] = aid_text
    rec['doi'] = doi
    rec['arxiv_name'] = aid_name

    meta, how = None, None

    # 1) arXiv id printed on page 1 of the PDF itself
    if aid_text:
        got = arxiv_meta([aid_text]).get(aid_text)
        if got:
            meta, how = got, 'arxiv-text'
            # if it was published, prefer the publisher's year when the PDF
            # itself shows that year (e.g. "Published as a conference paper at
            # ICLR 2023" -> 2023, not the 2022 preprint date)
            if got.get('doi'):
                pub = crossref_meta(got['doi'])
                if pub and text_support(pub['title'], text) >= 0.6 and pub.get('year') \
                        and str(pub['year']) in text:
                    got['year'] = pub['year']
                    got['venue'] = pub.get('venue') or got.get('journal_ref')
                    got['source'] = f"arxiv:{aid_text}+crossref"
    # 2) DOI: printed on page 1, embedded in the filename (ACM), or deeper in
    #    the text (references only -> strict). Verified against page 1.
    for d_try, need, htag in ((doi_p1, 0.6, 'doi'), (doi_fn, 0.7, 'doi-fn'),
                              (doi_libgen, 0.7, 'doi-fn'),
                              (doi if not doi_p1 else None, 0.9, 'doi-deep')):
        if meta is None and d_try:
            got = crossref_meta(d_try)
            if got and text_support(got['title'], text) >= need:
                meta, how = got, htag
            elif got:
                rec.setdefault('doi_rejected', got['title'])
    # 3) arXiv id taken from the filename (verified against page 1)
    if meta is None and aid_name:
        got = arxiv_meta([aid_name]).get(aid_name)
        if got and text_support(got['title'], text) >= 0.6:
            meta, how = got, 'arxiv-name'
    # 4) Crossref bibliographic search (IEEE/OSA/SPIE conferences & journals)
    if meta is None:
        best, best_score = None, 0.0
        for q in rec['cands'][:2]:
            for cand in crossref_search(q):
                if not cand['title']:
                    continue
                r = difflib.SequenceMatcher(
                    None, norm_alnum(q), norm_alnum(cand['title'])).ratio()
                if r < 0.85:
                    continue
                tsup = text_support(cand['title'], text)
                if tsup < 0.6:
                    continue
                score = 0.4 * r + 0.6 * tsup
                if score > best_score:
                    best, best_score = cand, score
        if best and best_score >= 0.6:
            meta, how = best, 'crossref-search'
    # 5) OpenAlex title search
    if meta is None:
        best, best_score = None, 0.0
        for q in rec['cands'][:3]:
            for cand in openalex_meta(q):
                if not cand['title']:
                    continue
                r = difflib.SequenceMatcher(
                    None, norm_alnum(q), norm_alnum(cand['title'])).ratio()
                tsup = text_support(cand['title'], text)
                if tsup < 0.7 or r < 0.45:
                    continue
                score = 0.5 * r + 0.5 * tsup
                if score > best_score:
                    best, best_score = cand, score
            if best_score >= 0.85:
                break
        if best and best_score >= 0.7:
            best['match_score'] = round(best_score, 3)
            meta, how = best, 'openalex'
    # 5b) Semantic Scholar title search (v1 fallback; only reached when the
    #     authoritative sources above all failed)
    if meta is None:
        best, best_score = None, 0.0
        for q in rec['cands'][:3]:
            for cand in s2_meta(q):
                if not cand['title']:
                    continue
                r = difflib.SequenceMatcher(
                    None, norm_alnum(q), norm_alnum(cand['title'])).ratio()
                tsup = text_support(cand['title'], text)
                if tsup < 0.7 or r < 0.45:
                    continue
                score = 0.5 * r + 0.5 * tsup
                if score > best_score:
                    best, best_score = cand, score
            if best_score >= 0.85:
                break
        if best and best_score >= 0.7:
            best['match_score'] = round(best_score, 3)
            meta, how = best, 's2'
    # 6) purely local: title + author + year read off the PDF itself
    local = None
    if meta is None:
        local = local_meta(text, info, page1, fcands)
        if local:
            meta, how = local, 'local'

    if meta is None:
        meta = fix_author_only(name, text, page1, info)
        if meta:
            how = 'fix-author'

    if meta is None:
        rec.update({'result': 'no-match'})
        if key:
            cache[key] = rec
        return rec

    tsup, aok, ytxt = verify(meta, text, pdf_dates)
    # an API record without authors (book chapters, some conference entries) is
    # still usable: fill the author from the PDF, or leave it Unknown
    if not meta.get('authors'):
        la = author_from_text(text) or author_from_filename(name)
        if la:
            meta['authors'] = [la]
            meta['source'] += '+local-author'
    if not meta.get('authors') and how in ('doi', 'arxiv-text', 'arxiv-name',
                                           'crossref-search') and tsup >= 0.8:
        meta['authors'] = ['Unknown']
    # fall back to the PDF's own metadata whenever the match is untrustworthy
    if how != 'local' and confidence_of(meta, tsup, aok, ytxt, how) == 'low':
        local = local_meta(text, info, page1, fcands)
        if local:
            rec['api_rejected'] = f"{meta['title'][:60]} ({how})"
            meta, how = local, 'local'
            tsup, aok, ytxt = verify(meta, text, pdf_dates)

    if how == 'local':
        if tsup >= 0.9 and len(meta['title']) >= 15:
            conf = 'local' if local.get('yconf', 0) >= 0.9 else 'local-date'
        else:
            conf = 'low'
    elif how == 'fix-author':
        conf = 'medium'
    else:
        conf = confidence_of(meta, tsup, aok, ytxt, how)
    new_name = propose(meta, name)
    rec.update({'result': 'ok', 'how': how, 'conf': conf,
                'api_title': meta['title'], 'api_authors': meta.get('authors'),
                'api_year': meta.get('year'), 'venue': meta.get('venue', ''),
                'tsup': round(tsup, 2), 'aok': aok, 'yok': ytxt,
                'new_name': new_name,
                'changed': bool(new_name and new_name != name)})
    if key:
        cache[key] = rec
    return rec


def save_cache(cache):
    cache['__arxiv__'] = ARXIV_ID_CACHE
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def preflight():
    """Fail loudly when the external PDF toolchain is missing.

    Without pdfminer.six every file raises inside process() and is dropped by
    the per-file handler in main(), which produces an empty report that looks
    exactly like a clean run.
    """
    missing = []
    for tool in ('pdfinfo', 'pdftotext'):
        if not shutil.which(tool):
            missing.append(f'{tool} - install poppler (brew install poppler)')
    try:
        import pdfminer  # noqa: F401
    except ImportError:
        missing.append('pdfminer.six - this interpreter is '
                       f'{sys.version.split()[0]} at {sys.executable}; run '
                       '"python3.12 -m pip install pdfminer.six" or use an '
                       'interpreter that already has it')
    if missing:
        log('cannot run: missing dependencies')
        for m in missing:
            log(f'  - {m}')
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dir', default=None)
    ap.add_argument('--root', default=None,
                    help='paper library root (default: $QLAB_PAPER_ROOT or '
                         f'{DEFAULT_ROOT})')
    ap.add_argument('--file', default=None)
    ap.add_argument('--conf', default='high,medium,fix-author',
                    help="which confidences --apply may write (default: "
                         "high,medium,fix-author).  'local'/'local-date' come "
                         "from the PDF text alone with no API backing, so they "
                         "stay report-only unless you ask for them explicitly.")
    ap.add_argument('--all', action='store_true',
                    help='re-check every PDF, not only suspicious ones')
    ap.add_argument('--include-arxiv', action='store_true',
                    help='also process files already named with an arXiv id '
                         '(by default they are left untouched)')
    ap.add_argument('--acm', action='store_true',
                    help='only process ACM DL downloads sitting directly in the '
                         'scanned root: Unknown_YYYY_<6-7 digits>_<6-7 digits>.pdf')
    args = ap.parse_args()
    configure_paths(args.root)

    if args.revert:
        revert()
        return
    preflight()

    cache = {}
    if os.path.exists(CACHE_PATH):
        try:
            cache = json.load(open(CACHE_PATH))
            ARXIV_ID_CACHE.update(cache.pop('__arxiv__', {}))
        except Exception:
            cache = {}
    stale = [k for k, v in cache.items()
             if v.get('result') == 'self-ok' and v.get('schema') != CACHE_SCHEMA]
    for k in stale:
        cache.pop(k, None)
    if stale:
        log(f'cache: {len(stale)} self-ok verdicts invalidated '
            f'(heuristics changed, schema {CACHE_SCHEMA})')

    if args.file:
        rec = process(args.file, cache, args)
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        save_cache(cache)
        return

    root = ROOT if not args.dir else os.path.join(ROOT, args.dir)
    if not os.path.isdir(root):
        log(f'paper root not found: {root}')
        log('point at the library with --root <path> or $QLAB_PAPER_ROOT')
        sys.exit(2)
    pdfs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for f in filenames:
            if f.lower().endswith('.pdf'):
                pdfs.append(os.path.join(dirpath, f))
    pdfs.sort()
    log(f'{len(pdfs)} PDFs under {root}')

    def suspicious(p):
        """Only the files the previous script clearly mangled.

        arXiv-named files are never touched: their naming rule is the intended
        one and they were already resolved through the arXiv API.
        """
        n = os.path.basename(p)
        if find_arxiv_filename(n):
            return False
        if re.search(r'Unknown|Anonymous', n, re.I):
            return True
        if re.match(r'^\d', n):
            return True
        if re.search(r'(?:^|_)[A-Z]_[A-Z]_', n):       # letter-spaced titles
            return True
        m = re.match(r'^([A-Za-z][A-Za-z\-]*(?:_[A-Za-z][A-Za-z\-]*)*)_(\d{4})_', n)
        if not m:
            return True
        if not (MIN_YEAR <= int(m.group(2)) <= MAX_YEAR):
            return True
        a = m.group(1)
        if re.fullmatch(r'[A-Z]{4,}(?:_[A-Z]+)*', a):   # TRANSFORMERS_2023_...
            return True
        return a.split('_')[0].lower() in BOGUS_AUTHORS

    targets = pdfs if args.all else [p for p in pdfs if suspicious(p)]
    if args.acm:
        acm_dl = re.compile(r'^Unknown_\d{4}_(\d{6,7})_(\d{6,7})\.pdf$')
        acm_hit = {p for p in pdfs
                   if acm_dl.match(os.path.basename(p)) and
                   os.path.dirname(p) == root}
        targets = [p for p in targets if p in acm_hit]
        log(f'{len(acm_hit)} top-level ACM-download files under {root}; '
            f'{len(targets)} to check (copies in subdirs left untouched)')
    log(f'{len([p for p in pdfs if find_arxiv_filename(os.path.basename(p))])} arXiv-named files left untouched')
    if args.limit:
        targets = targets[:args.limit]
    log(f'{len(targets)} files to check')

    # prefetch, in batches, every arXiv id visible in a filename
    pre = sorted({a for p in targets
                  for a in [find_arxiv_filename(os.path.basename(p))] if a})
    pre = [a for a in pre if a not in ARXIV_ID_CACHE]
    if pre:
        log(f'prefetching {len(pre)} arXiv ids from filenames')
        _arxiv_fetch(pre)
        log(f'  arXiv memo now holds {len(ARXIV_ID_CACHE)} entries')

    rows = []
    t0 = time.time()
    for i, p in enumerate(targets, 1):
        try:
            # copy: process() hands back the shared cache dict, and two files
            # with identical content share one key - without the copy, 'path'
            # below would alias them and the second rename would fail.
            rec = dict(process(p, cache, args))
        except KeyboardInterrupt:
            log('interrupted - saving cache')
            break
        except Exception as e:
            log(f'  ! {os.path.basename(p)}: {e}')
            continue
        rec['path'] = p
        rows.append(rec)
        if rec.get('new_name'):
            log(f'[{i}/{len(targets)}] {os.path.basename(p)} -> '
                f'{rec["new_name"]} ({rec.get("conf")}, {rec.get("how")})')
        else:
            log(f'[{i}/{len(targets)}] {os.path.basename(p)} -> '
                f'no change proposed [{rec.get("result")}]')
        if i % 25 == 0:
            save_cache(cache)
            log(f'  {i}/{len(targets)}  {int(time.time()-t0)}s')
    save_cache(cache)

    allowed = set(args.conf.split(','))
    todo = [r for r in rows if r.get('conf') in allowed and r.get('changed')]
    with open(REPORT_PATH, 'w') as f:
        f.write('conf\thow\tcurrent\tproposed\tyear\tvenue\ttsup\tauthors\n')
        for r in sorted(rows, key=lambda r: (r.get('conf', 'zz'), r['path'])):
            f.write('\t'.join(str(x) for x in [
                r.get('conf', r.get('result')), r.get('how', ''),
                os.path.basename(r['path']), r.get('new_name', ''),
                r.get('api_year', ''), (r.get('venue') or '')[:40],
                r.get('tsup', ''), ','.join((r.get('api_authors') or [])[:3])
            ]) + '\n')

    counts = {}
    for r in rows:
        counts[r.get('conf', r.get('result'))] = counts.get(
            r.get('conf', r.get('result')), 0) + 1
    log(f'\nresults: {counts}')
    log(f'proposed changes (conf in {allowed}): {len(todo)}')
    log(f'report: {REPORT_PATH}')

    if args.apply:
        applied = 0
        with open(LOG_PATH, 'a') as lg:
            if lg.tell():
                lg.write('\n')       # blank line = run boundary for --revert
            for r in todo:
                src = r['path']
                dst = os.path.join(os.path.dirname(src), r['new_name'])
                if os.path.exists(dst):
                    base = r['new_name'].rsplit('.', 1)[0]
                    k = 1
                    while os.path.exists(os.path.join(os.path.dirname(src),
                                                     f'{base}_{k}.pdf')):
                        k += 1
                    dst = os.path.join(os.path.dirname(src), f'{base}_{k}.pdf')
                try:
                    os.rename(src, dst)
                    lg.write(json.dumps({'from': src, 'to': dst,
                                         'how': r.get('how'),
                                         'conf': r.get('conf')}) + '\n')
                    applied += 1
                except Exception as e:
                    log(f'  ! rename failed {src}: {e}')
        log(f'renamed {applied} files')
    else:
        for r in todo[:40]:
            log(f'  {os.path.basename(r["path"])[:70]}\n'
                f'    -> {r["new_name"]}')


def revert():
    """Undo the renames of the LAST --apply run only.

    Runs are separated by a blank line in the log; the old code collected every
    non-blank line, so it undid the entire history instead of the last run.
    """
    if not os.path.exists(LOG_PATH):
        log('no log')
        return
    blocks = [b for b in open(LOG_PATH).read().split('\n\n') if b.strip()]
    if not blocks:
        log('no renames logged')
        return
    n = 0
    for ln in blocks.pop().strip().split('\n'):
        try:
            d = json.loads(ln)
            if os.path.exists(d['to']):
                os.rename(d['to'], d['from'])
                n += 1
            else:
                log(f'  ? already gone: {os.path.basename(d["to"])}')
        except Exception as e:
            log(f'  ! {e}')
    with open(LOG_PATH, 'w') as f:
        f.write('\n\n'.join(blocks) + ('\n\n' if blocks else ''))
    log(f'reverted {n} renames')


if __name__ == '__main__':
    main()
