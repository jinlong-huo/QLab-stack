#!/usr/bin/env python3
"""ArXiv API 获取：构造查询、拉取 feed、带重试和错误分类。

每个分类独立请求（每页 max 200 results，自动翻页），请求间延时 API_DELAY 秒，
避免单次大查询触发 arXiv 限流。

支持区间回填：fetch_all(date_from=..., date_to=...) 按 RANGE_WINDOW_DAYS 天
分窗口逐窗口拉取，用于长时间未运行后补拉漏掉的论文。
"""

import os
import random
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

import feedparser

from arxiv_digest import config
from arxiv_digest import filter as flt

# ── Proxy bypass (no_proxy) for arXiv hosts ────────────────────
# Many proxy/VPN tools (Clash X, Surge, etc.) intercept export.arxiv.org
# and cause 429 rate-limits or timeouts.  We set no_proxy for these hosts
# so the OS proxy settings are bypassed for arXiv API/PDF requests.
_ARXIV_NO_PROXY = ",".join(config.ARXIV_PROXY_BYPASS_HOSTS)
_NO_PROXY_OLD = os.environ.get("no_proxy", "")
if config.ARXIV_BYPASS_PROXY:
    # Append arXiv hosts to any existing no_proxy
    if _ARXIV_NO_PROXY not in _NO_PROXY_OLD:
        os.environ["no_proxy"] = (
            f"{_NO_PROXY_OLD},{_ARXIV_NO_PROXY}"
            if _NO_PROXY_OLD else _ARXIV_NO_PROXY
        )
    # Also set http_proxy to empty for these hosts via urllib handler
    # (the no_proxy env var covers most tools; urllib also respects NO_PROXY)


def _parse_date_arg(s):
    """'YYYY-MM-DD' → datetime.date。格式错误抛 ValueError。"""
    return datetime.strptime(s, "%Y-%m-%d").date()


def _iter_date_windows(d_from, d_to):
    """(from, to) 日期窗口生成器：新→旧，每个窗口最多 RANGE_WINDOW_DAYS 天（含端点）。"""
    step = timedelta(days=config.RANGE_WINDOW_DAYS - 1)
    cur_end = d_to
    while cur_end >= d_from:
        cur_start = max(d_from, cur_end - step)
        yield cur_start, cur_end
        cur_end = cur_start - timedelta(days=1)


def _build_category_url(category, date_from, date_to, start=0):
    """构造单个分类的查询 URL。

    date_from/date_to: datetime.date（闭区间）。
    start: 翻页偏移（每页 max_results = MAX_PER_CATEGORY）。
    """
    search_query = (f"cat:{category} AND submittedDate:"
                    f"[{date_from:%Y%m%d}0000 TO {date_to:%Y%m%d}2359]")
    params = {
        "search_query": search_query,
        "start": str(start),
        "max_results": str(config.MAX_PER_CATEGORY),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    qs = urllib.parse.urlencode(params)
    base = config.ARXIV_API_BASE_URL.rstrip("/")
    return f"{base}/api/query?{qs}"


def _is_fatal_error(bozo_exception):
    """不可重试的错误（SSL 证书、DNS 解析）→ 重试是浪费时间。"""
    if bozo_exception is None:
        return False
    msgs = []
    ex = bozo_exception
    seen = set()
    while ex is not None and id(ex) not in seen:
        seen.add(id(ex))
        msgs.append(str(ex))
        msgs.append(type(ex).__name__)
        if hasattr(ex, 'reason') and not isinstance(ex.reason, type(ex)):
            if isinstance(ex.reason, str):
                msgs.append(ex.reason)
                break
            else:
                ex = ex.reason
        elif hasattr(ex, '__cause__') and ex.__cause__ is not None:
            ex = ex.__cause__
        else:
            break
    combined = ' '.join(msgs)
    if 'SSL' in combined or 'certificate verify failed' in combined.lower():
        return True
    if 'gaierror' in combined or 'errno 8' in combined.lower():
        return True
    return False


def _classify_status(feed):
    """返回 (status_int, bozo_exception, is_429, is_503, is_fatal)。

    统一的状态分类，供 _fetch_once 和重试逻辑使用。
    """
    bozo = getattr(feed, 'bozo_exception', None)
    status = getattr(feed, 'status', 'N/A')
    status_int = int(status) if str(status).isdigit() else 0
    is_429 = (status_int == 429)
    is_503 = (status_int == 503)
    fatal = _is_fatal_error(bozo)
    return status_int, bozo, is_429, is_503, fatal


def _feed_total(feed):
    """arXiv feed 的总命中数（opensearch_totalresults），未知时为 None。"""
    try:
        return int(feed.feed.get('opensearch_totalresults'))
    except (AttributeError, TypeError, ValueError):
        return None


def _prompt_retry(reason, wait_sec=None):
    """Ask user interactively whether to wait and retry. Returns True if retrying."""
    if wait_sec is None:
        wait_sec = 300 + random.randint(0, 60)
    mins = wait_sec // 60
    secs = wait_sec % 60

    # 非交互环境（管道 / cron）：不要卡在 input() 上
    if not sys.stdin.isatty():
        print(f"\n  {reason}")
        print("  (non-interactive — skipping retry; use --wait for auto-retry)")
        return False

    print(f"\n{'─'*55}")
    print(f"  {reason}")
    print(f"  Wait {mins}m{secs}s and retry automatically? [Y/n] ", end="", flush=True)

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer in ("", "y", "yes"):
        print(f"  Waiting {wait_sec}s...", end="", flush=True)
        for remaining in range(wait_sec, 0, -1):
            time.sleep(1)
            if remaining % 30 == 0:
                print(f"\n  ({remaining // 60}m{remaining % 60:02d}s remaining)...", end="", flush=True)
        print(" retrying!")
        return True
    else:
        print("  Skipping — run again later or use --wait for auto-retry.")
        return False


USER_AGENT = "arXivDailyDigest/1.0 (mailto:rawking1621@gmail.com)"


def _is_arxiv_error_feed(feed):
    """Detect arXiv API error masquerading as HTTP 200 with a single 'Error' entry.

    arXiv's legacy API returns HTTP 200 with one Atom entry whose title is
    "Error" when the query is malformed (e.g. max_results too high, bad
    boolean syntax).  That entry has no arxiv_primary_category, so it all
    lands in 'unknown' and looks like "0 entries for ALL categories."
    """
    if not feed.entries or len(feed.entries) != 1:
        return None
    entry = feed.entries[0]
    title = getattr(entry, 'title', '').strip()
    if title.lower() == 'error':
        summary = getattr(entry, 'summary', '')
        return f"arXiv API rejected the query: {summary}" if summary else \
               "arXiv API returned 'Error' entry (no details)"
    return None


def _fetch_bytes_no_proxy(url, timeout=30):
    """Fetch URL bytes while bypassing the system/VPN proxy for arXiv hosts.

    feedparser internally uses urllib.request, which on macOS honours the
    system proxy (including VPN/Clash X/Surge interceptors).  Those proxies
    often 429-rate-limit or time out on export.arxiv.org.  We instead use a
    fresh urlopen with an empty ProxyHandler for arXiv domains so requests
    go direct to arXiv.

    Returns (data, status) where:
      - data = raw bytes (only for 2xx responses)
      - status = HTTP status int, or 0 on connection/timeout error
      On non-2xx responses, data is None so _do_fetch falls back to the
      normal feedparser path which classifies errors correctly.
    """
    import urllib.request as _urllib_request
    import ssl as _ssl

    data = None
    status = 0

    if config.ARXIV_BYPASS_PROXY:
        # No-op ProxyHandler => bypass HTTP(S)_PROXY + macOS system proxy
        opener = _urllib_request.build_opener(
            _urllib_request.ProxyHandler({}),
            _urllib_request.HTTPSHandler(context=_ssl.create_default_context()),
        )
    else:
        opener = _urllib_request.build_opener()

    req = _urllib_request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml, application/xml, */*",
    })
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", resp.getcode() or 0)
            if 200 <= status < 300:
                data = resp.read()
    except urllib.error.HTTPError as e:
        # HTTP error with a status code (429, 503, etc.)
        status = e.code
        return None, status
    except Exception:
        # Connection/timeout/SSL error — no valid status
        return None, 0

    return data, status


def _feed_from_bytes(data):
    """Parse raw bytes with feedparser (works the same as .parse(url))."""
    if not data:
        return None
    try:
        return feedparser.parse(data)
    except Exception:
        return None


def _do_fetch(url):
    """Single fetch attempt.  Returns (feed, status_int, bozo, is_429, is_503, fatal).

    If config.ARXIV_BYPASS_PROXY is True, the download bypasses the system
    proxy so VPN/Clash X/Surge cannot rate-limit or drop arXiv requests.

    If the bypass fails (no data), we fall back to feedparser.parse(url)
    so the existing error-classification / retry machinery still applies.
    """
    # Try direct (proxy-bypass) fetch first
    data, status_int = _fetch_bytes_no_proxy(url)
    feed = _feed_from_bytes(data)

    if feed is None:
        # Fallback: let feedparser handle (goes through system proxy)
        feed = feedparser.parse(url, agent=USER_AGENT)
    status_int, bozo, is_429, is_503, fatal = _classify_status(feed)
    return feed, status_int, bozo, is_429, is_503, fatal


def _fetch_one_page(url, wait_on_429, label, rate_limit_retries=0):
    """Fetch a single page URL with full retry logic.

    Returns (entries, total): list of feedparser entries (empty on
    failure/skip) and arXiv's opensearch_totalresults (None if unknown).
    Prints progress using ``label`` (e.g. the category name).

    Handles: HTTP 429 (rate-limit), HTTP 503 (unavailable), parse errors,
    fatal errors (SSL/DNS), and arXiv "Error" pseudo-entries.

    rate_limit_retries: how many 429 cooldowns have already been spent on
    this page. With wait_on_429=True, 429s are retried up to
    config.MAX_429_RETRIES times before giving up.
    """
    # ── Attempt 1 ──────────────────────────────────────────────
    feed, status_int, bozo, is_429, is_503, fatal = _do_fetch(url)

    # Debug: surface arXiv "Error" entry (malformed query masquerading as success)
    error_msg = _is_arxiv_error_feed(feed)
    if error_msg:
        print(f"\n  [{label}] ⛔ {error_msg}")
        print(f"         This is NOT a rate-limit — the query itself is invalid.")
        print(f"         URL: {url}")
        return [], None

    # Success on first attempt
    if feed.entries:
        print(f"  [{label}] ✓ {len(feed.entries)} entries")
        return feed.entries, _feed_total(feed)

    # HTTP 200 + 干净解析 + 0 条目 = 该窗口确实没有论文（如周末 / 冷门分类）
    if status_int == 200 and bozo is None:
        print(f"  [{label}] ✓ 0 entries (no papers in this window)")
        return [], _feed_total(feed)

    # ── No entries — classify the failure ─────────────────────
    if is_429:
        wait_sec = 300 + random.randint(0, 60)
        if wait_on_429 and rate_limit_retries < config.MAX_429_RETRIES:
            print(f"\n  [{label}] ⛔ HTTP 429 rate-limit "
                  f"({rate_limit_retries + 1}/{config.MAX_429_RETRIES}). "
                  f"Waiting {wait_sec}s ({wait_sec // 60}min) then retrying...")
            time.sleep(wait_sec)
            return _fetch_one_page(url, wait_on_429=True, label=label,
                                   rate_limit_retries=rate_limit_retries + 1)
        if wait_on_429:
            print(f"\n  [{label}] ⛔ HTTP 429 persists after "
                  f"{config.MAX_429_RETRIES} cooldowns — giving up on this page.")
            return [], None
        if _prompt_retry(f"[{label}] arXiv is rate-limiting this IP (HTTP 429).", wait_sec):
            return _fetch_one_page(url, wait_on_429=False, label=label)
        return [], None

    if fatal:
        print(f"  [{label}] ⛔ [FATAL] non-retryable error: {str(bozo)[:200]}")
        return [], None

    if is_503:
        retry_reason = "HTTP 503 — arXiv API is temporarily unavailable"
    elif status_int == 200 and bozo is not None:
        retry_reason = f"HTTP 200 but feed parse failed" \
                       f"{f' — {str(bozo)[:100]}' if bozo else ''}"
    else:
        retry_reason = f"HTTP {status_int} — empty response (no entries)"

    # ── Retry loop ────────────────────────────────────────────
    for attempt in range(config.MAX_RETRIES):
        base = config.RETRY_BACKOFF_503_BASE if is_503 else config.RETRY_BACKOFF_BASE
        if attempt < len(base):
            delay = base[attempt] * (0.75 + random.random() * 0.5)
        else:
            delay = base[-1] * (0.75 + random.random() * 0.5)
        print(f"  [{label}] [retry] {retry_reason[:80]} — "
              f"attempt {attempt + 2}/{config.MAX_RETRIES + 1}, "
              f"waiting {delay:.1f}s...")
        time.sleep(delay)

        feed, status_int2, bozo2, is_429_2, is_503_2, fatal2 = _do_fetch(url)

        # Check for arXiv error entry on retry too
        error_msg = _is_arxiv_error_feed(feed)
        if error_msg:
            print(f"\n  [{label}] ⛔ {error_msg}")
            print(f"         URL: {url}")
            return [], None

        if feed.entries:
            print(f"  [{label}] ✓ {len(feed.entries)} entries (after retry)")
            return feed.entries, _feed_total(feed)

        # 重试后仍 200 + 空：该窗口确实没有论文
        if status_int2 == 200 and bozo2 is None:
            print(f"  [{label}] ✓ 0 entries (no papers in this window)")
            return [], _feed_total(feed)

        if is_429_2:
            wait_sec = 300 + random.randint(0, 60)
            if wait_on_429 and rate_limit_retries < config.MAX_429_RETRIES:
                print(f"  [{label}] [RATE-LIMITED] HTTP 429 on retry "
                      f"({rate_limit_retries + 1}/{config.MAX_429_RETRIES}) — "
                      f"waiting {wait_sec}s ({wait_sec // 60}min) then retrying...")
                time.sleep(wait_sec)
                return _fetch_one_page(url, wait_on_429=True, label=label,
                                       rate_limit_retries=rate_limit_retries + 1)
            if wait_on_429:
                print(f"  [{label}] ⛔ HTTP 429 persists after "
                      f"{config.MAX_429_RETRIES} cooldowns — giving up on this page.")
                return [], None
            if _prompt_retry(f"[{label}] HTTP 429 on retry — arXiv is rate-limiting.", wait_sec):
                return _fetch_one_page(url, wait_on_429=False, label=label)
            return [], None

        if fatal2:
            print(f"  [{label}] [FATAL] non-retryable error on retry — aborting")
            return [], None

        # Update retry_reason for next iteration
        if is_503_2:
            retry_reason = "HTTP 503 — arXiv API is temporarily unavailable"
            is_503 = True
        elif status_int2 == 200 and bozo2 is not None:
            retry_reason = "HTTP 200 but feed parse failed"
            is_503 = False
        else:
            retry_reason = f"HTTP {status_int2} — empty response"
            is_503 = False

    # ── After all retries ──
    if feed.entries:
        print(f"  [{label}] ✓ {len(feed.entries)} entries (final)")
        return feed.entries, _feed_total(feed)

    # All attempts exhausted, no data — one last prompt
    status_final = getattr(feed, 'status', 'N/A')
    print(f"\n  [{label}] ⛔ 0 entries after "
          f"{config.MAX_RETRIES + 1} attempt(s) (HTTP {status_final}).")
    if not wait_on_429:
        if _prompt_retry(f"[{label}] All {config.MAX_RETRIES + 1} attempts exhausted "
                         f"(HTTP {status_final})."):
            return _fetch_one_page(url, wait_on_429=False, label=label)
    return [], None


def _fetch_window(category, date_from, date_to, wait_on_429):
    """拉取单个分类在 [date_from, date_to] 窗口内的全部论文（自动翻页）。

    逐页请求（每页 MAX_PER_CATEGORY），直到页不满一页或达到
    opensearch_totalresults / MAX_PAGES 上限。返回 entries 列表。
    """
    label = category
    all_entries = []
    start = 0
    total = None

    for page in range(config.MAX_PAGES):
        url = _build_category_url(category, date_from, date_to, start=start)
        entries, total = _fetch_one_page(url, wait_on_429=wait_on_429, label=label)
        if not entries:
            break
        all_entries.extend(entries)
        start += len(entries)

        if (total is not None and start >= total) or len(entries) < config.MAX_PER_CATEGORY:
            break

        delay = config.API_DELAY * (0.75 + random.random() * 0.5)
        print(f"  [{label}] [next page] waiting {delay:.1f}s...")
        time.sleep(delay)
    else:
        # for 循环未被 break → 翻页上限
        if total is None or start < total:
            print(f"  [{label}] ⚠ page cap reached ({config.MAX_PAGES} pages) — "
                  f"got {len(all_entries)} entries; consider a smaller range")

    return all_entries


class _HtmlEntry:
    """Minimal duck-typed entry that mimics feedparser entry attributes.

    The rest of the pipeline accesses entry.id / entry.title / entry.summary
    / entry.link / entry.author / entry.published_parsed. This class provides
    those attributes from the HTML scrape data.
    """

    def __init__(self, id=None, title=None, summary=None, link=None,
                 arxiv_primary_category=None, author=None, authors=None,
                 published=None, updated=None, published_parsed=None):
        self.id = id
        self.title = title
        self.summary = summary
        self.link = link
        self.arxiv_primary_category = arxiv_primary_category or {"term": ""}
        self.author = author
        self.authors = authors or []
        self.published = published
        self.updated = updated
        self.published_parsed = published_parsed

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __repr__(self):
        return f"<HtmlEntry id={self.id!r} title={self.title!r}>"


# ── HTML search fallback (when export.arxiv.org is rate-limited) ─
# arXiv's legacy API at export.arxiv.org uses Fastly CDN, which
# aggressively rate-limits certain IP ranges.  The main arxiv.org
# website (served from separate infrastructure) is usually still
# reachable.  When the API returns 0 entries for a category, we fall
# back to searching arxiv.org/search/ and parsing the HTML result.


def _search_html_category(category, date_from, date_to,
                          max_results=config.MAX_PER_CATEGORY):
    """Fallback: scrape arxiv.org HTML search for a category + date range.

    This mirrors the API's _fetch_window() but uses the main www site,
    which is on separate CDN infrastructure and less subject to the
    rate-limits that hit export.arxiv.org.

    Returns a list of *dicts* (not feedparser entries) with keys matching
    feedparser entry attributes: id, title, summary, link, arxiv_primary_category.

    Returns empty list if the search itself is blocked.
    """
    import html as _html
    import json as _json
    import urllib.request as _urllib_request
    import ssl as _ssl
    import re as _re

    all_results = []
    seen_links = set()

    # Build category-based query — use quoted category name (e.g. "cs.NI") 
    # which is what arxiv.org/search/ expects.  The "cat:" prefix from the
    # API doesn't work in HTML search.
    raw_cat = category
    cat_query = '"' + raw_cat + '"'

    # arXiv HTML search sorts by relevance by default; add date sort and filter
    date_filter = (f'submittedDate:[{date_from:%Y%m%d}0000 '
                   f'TO {date_to:%Y%m%d}2359]')
    query = f"({cat_query}) AND ({date_filter})"

    for start in range(0, min(max_results, 200), 50):
        params = urllib.parse.urlencode({
            "query": query,
            "searchtype": "all",
            "start": str(start),
        })
        url = f"{config.ARXIV_LIST_BASE_URL.rstrip('/')}/search/?{params}"

        # Fetch HTML (bypass proxy for arXiv hosts)
        data, status = _fetch_bytes_no_proxy(url, timeout=15)

        # If proxy bypass failed, try via normal urllib
        if data is None:
            try:
                opener = _urllib_request.build_opener(
                    _urllib_request.HTTPSHandler(
                        context=_ssl.create_default_context()))
                req = _urllib_request.Request(
                    url, headers={"User-Agent": USER_AGENT})
                with opener.open(req, timeout=15) as resp:
                    data = resp.read()
            except Exception:
                pass

        if not data:
            print(f"  [{category}] ⛔ HTML search fallback: "
                  f"no data (page {start // 50 + 1})")
            break

        html = data.decode("utf-8", errors="replace")

        # Check for rate-limit or block page
        if "Rate exceeded" in html or "too many requests" in html.lower():
            print(f"  [{category}] ⛔ HTML search also rate-limited")
            break

        # Parse paper list items
        paper_items = _re.findall(
            r'<li class="arxiv-result">.*?</li>', html, _re.DOTALL)

        if not paper_items:
            print(f"  [{category}] ✓ HTML search: 0 papers in page")
            break

        for item in paper_items:
            # arXiv ID
            id_match = _re.search(r'arXiv:(\d+\.\d+)', item)
            if not id_match:
                continue
            paper_id = id_match.group(1)
            if paper_id in seen_links:
                continue
            seen_links.add(paper_id)

            # Title
            title = "Untitled"
            t_match = _re.search(
                r'<p class="title is-5 mathjax">(.*?)</p>', item, _re.DOTALL)
            if t_match:
                title = _re.sub(r'<.*?>', '', t_match.group(1))
                title = _html.unescape(title).strip()

            # Abstract snippet
            summary = ""
            s_match = _re.search(
                r'<span class="abstract-short">(.*?)</span>', item, _re.DOTALL)
            if s_match:
                summary = _re.sub(r'<.*?>', '', s_match.group(1))
                summary = _html.unescape(summary).strip()
            else:
                # Try full abstract
                s_match = _re.search(
                    r'<span class="abstract-full">(.*?)</span>',
                    item, _re.DOTALL)
                if s_match:
                    summary = _re.sub(r'<.*?>', '', s_match.group(1))
                    summary = _html.unescape(summary).strip()

            # Link
            link = f"{config.ARXIV_ABS_BASE_URL.rstrip('/')}/abs/{paper_id}"

            # Primary category — extract from the listing
            primary_cat = category
            cat_match = _re.search(
                r'<span class="tag is-small is-link">(.*?)</span>',
                item, _re.DOTALL)
            if cat_match:
                primary_cat = cat_match.group(1).strip()

            # Build a duck-compatible entry (attribute access like feedparser)
            entry = _HtmlEntry(
                id=link,
                title=title,
                summary=summary,
                link=link,
                arxiv_primary_category={"term": primary_cat},
                author="Unknown",
                authors=[],
                published=date_from.strftime("%Y-%m-%d") + "T00:00:00Z",
                updated=date_from.strftime("%Y-%m-%d") + "T00:00:00Z",
                published_parsed=None,
            )
            all_results.append(entry)

        if len(paper_items) < 50:
            break  # Last page

        time.sleep(config.API_DELAY * (0.5 + random.random() * 0.3))

    return all_results


def fetch_all(wait_on_429=False, date_from=None, date_to=None):
    """拉取全部 CATEGORIES 的论文（每个分类独立请求），返回 (entries_by_category, stats).

    date_from/date_to: 'YYYY-MM-DD' 字符串（闭区间）。缺省时拉最近 3 天
    （或 --date 覆盖日的前 3 天），并自动附加一个回看窗口
    （LOOKBACK_FROM_DAYS_AGO ~ LOOKBACK_TO_DAYS_AGO 天前）用于 carry-over
    补遗；给定区间时按 RANGE_WINDOW_DAYS 天分窗口、逐窗口逐分类拉取
    （用于长时间未运行后的补拉，此时不附加回看窗口）。

    结果按 arxiv id 全局去重（arXiv 跨列表：同一论文可能出现在多个分类
    的查询结果中）。

    entries_by_category: list of (category, [feedparser entries])
    stats: dict[category] = {total, skipped, already_seen, selected, ocs_selected}

    wait_on_429: if True, wait 5 min and retry when rate-limited instead of prompting.
    """
    # ── 决定拉取窗口 ────────────────────────────────────────
    if date_from or date_to:
        try:
            d_to = _parse_date_arg(date_to) if date_to else config.bj_now().date()
            d_from = _parse_date_arg(date_from) if date_from else d_to
        except ValueError:
            print("[error] 日期格式应为 YYYY-MM-DD")
            return _empty_result()
        if d_from > d_to:
            print("[error] --from 不能晚于 --to")
            return _empty_result()
        windows = list(_iter_date_windows(d_from, d_to))
        print(f"  Plan: {len(windows)} window(s), {d_from} → {d_to} "
              f"across {len(config.CATEGORIES)} categories")
    else:
        d_to = config.bj_now().date()
        d_from = d_to - timedelta(days=3)
        windows = [(d_from, d_to)]
        # 自动回看窗口：仅默认日常模式（无 --date / --from / --to）启用，
        # 覆盖 arXiv 延迟上架 + 忘记运行的日子，供 carry-over 补遗使用。
        if config.DATE_OVERRIDE is None:
            lb_to = d_to - timedelta(days=config.LOOKBACK_TO_DAYS_AGO)
            lb_from = d_to - timedelta(days=config.LOOKBACK_FROM_DAYS_AGO)
            if lb_from <= lb_to:
                windows.append((lb_from, lb_to))
                print(f"  Plan: daily {d_from} → {d_to}  +  "
                      f"lookback {lb_from} → {lb_to}  "
                      f"across {len(config.CATEGORIES)} categories")
        if len(windows) == 1:
            print(f"  Plan: 1 window, {d_from} → {d_to} "
                  f"across {len(config.CATEGORIES)} categories")

    # Pre-fetch jitter — avoid hitting the API at predictable instants
    pre_jitter = random.uniform(1.0, config.API_DELAY)
    time.sleep(pre_jitter)

    entries_by_cat = {cat: [] for cat in config.CATEGORIES}
    seen_ids = set()   # 跨窗口 / 跨分类去重

    # 自适应请求间隔：被 429 后指数加缓，连续成功后回落。
    # 双窗口日常模式下请求数翻倍，固定 5s 间隔极易触发持续限流。
    throttle = {"delay": config.API_DELAY, "strikes": 0}

    first_request = True
    for w_from, w_to in windows:
        print(f"\n  [window] {w_from} → {w_to}")
        for cat in config.CATEGORIES:
            # Delay between requests (skip the very first one; pre_jitter covers it)
            if not first_request:
                delay = throttle["delay"] * (0.75 + random.random() * 0.5)
                print(f"  [wait] {delay:.1f}s before next category...")
                time.sleep(delay)
            first_request = False

            win_entries = _fetch_window(cat, w_from, w_to, wait_on_429)

            # ── Fallback: if API returned 0 entries, try HTML search ──
            if not win_entries and config.ARXIV_HTML_FALLBACK:
                print(f"  [{cat}] API returned 0 entries — "
                      f"falling back to arxiv.org HTML search...")
                win_entries = _search_html_category(cat, w_from, w_to)
                if win_entries:
                    print(f"  [{cat}] ✓ HTML search: "
                          f"{len(win_entries)} entries (fallback)")

            if win_entries:
                # 成功 → 连续两次成功后间隔回落一级
                throttle["strikes"] = max(0, throttle["strikes"] - 1)
            else:
                # 空结果（含 429/503 放弃）→ 加缓，上限 60s
                throttle["strikes"] += 1
            throttle["delay"] = min(60.0,
                                    config.API_DELAY * (1.5 ** throttle["strikes"]))

            for e in win_entries:
                eid = flt.normalize_arxiv_id(e.id)
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    entries_by_cat[cat].append(e)

    # ── Report ────────────────────────────────────────────────
    total_entries = sum(len(v) for v in entries_by_cat.values())
    succeeded = sum(1 for v in entries_by_cat.values() if v)
    print(f"\n  Summary: {succeeded}/{len(config.CATEGORIES)} categories returned data, "
          f"{total_entries} total entries (deduped)")
    empty_cats = [cat for cat in config.CATEGORIES if not entries_by_cat[cat]]
    if empty_cats:
        print(f"  Empty: {', '.join(empty_cats)}")

    return _build_result(entries_by_cat)


def _empty_result():
    """无数据时的空结果。"""
    return ([(cat, []) for cat in config.CATEGORIES],
            {cat: {"total": 0, "skipped": 0, "already_seen": 0,
                   "selected": 0, "ocs_selected": 0}
             for cat in config.CATEGORIES})


def _build_result(entries_by_cat):
    """将 {cat: [entries]} 转为 (entries_by_category, stats)。"""
    entries_by_category = []
    stats = {}
    for cat in config.CATEGORIES:
        cat_entries = entries_by_cat.get(cat, [])
        entries_by_category.append((cat, cat_entries))
        stats[cat] = {"total": len(cat_entries), "skipped": 0,
                       "already_seen": 0, "selected": 0, "ocs_selected": 0}
    return entries_by_category, stats
