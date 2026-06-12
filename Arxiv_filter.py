#!/usr/bin/env python3

import json
import os
import re
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime

import feedparser

# =========================
# 配置
# =========================

RSS_FEEDS = [
    "https://rss.arxiv.org/rss/cs.NI",
    "https://rss.arxiv.org/rss/cs.DC",
    "https://rss.arxiv.org/rss/cs.OS",
    "https://rss.arxiv.org/rss/cs.AR",
    "https://rss.arxiv.org/rss/cs.PF",
    "https://rss.arxiv.org/rss/cs.AI",
    "https://rss.arxiv.org/rss/cs.CV",
    "https://rss.arxiv.org/rss/cs.SY",
    
]

# 关键词权重
# 设计原则：高分给能明确指向 LLM 推理 / GPU 数据中心 / RDMA 网络的词；
# 通用系统词（network, memory, distributed）降为辅助分，不能单独撑过阈值。
KEYWORDS = {
    # === 核心：LLM 推理 ===
    "llm": 6,
    "large language model": 6,
    "inference": 6,
    "serving": 6,
    "kv cache": 6,
    "prefill": 5,
    "decode": 5,
    "transformer": 4,

    # === 核心：高性能网络 / 数据中心 ===
    "rdma": 6,
    "datacenter": 5,
    "congestion": 5,
    "tail latency": 5,

    # === 调度与资源 ===
    "scheduling": 5,
    "resource allocation": 5,
    "load balancing": 5,

    # === GPU / 性能 ===
    "gpu": 4,
    "throughput": 4,
    "latency": 4,

    # === 辅助：分布式 / 并行（单独分值低，避免通用论文误入）===
    "distributed": 3,
    "communication": 3,
    "pipeline": 3,
    "parallelism": 3,
    "network": 2,
    "memory": 2,
}

# 负向词（综述、教程等）
NEGATIVE_KEYWORDS = {
    "survey": -6,
    "a survey": -6,
    "tutorial": -6,
    "benchmark dataset": -6,
    "review": -4,
}

# 第一轮：宽松海选（低门槛，不漏论文）
# 参考：gpu(4)+throughput(4)=8 / distributed(3)+communication(3)+pipeline(3)=9 / network(2)+memory(2)=4
MIN_SCORE = 5

# 第二轮：按分数排序后只保留 TOP-N（控制每日阅读量）
MAX_PAPERS = 15
MAX_OCS_PAPERS = 10

# =========================
# OCS / 光交换 / 新基础设施 关键词（独立于主过滤器）
# =========================

OCS_KEYWORDS = {
    # === 光电路交换 ===
    "optical circuit switch": 6,
    "optical circuit switching": 6,
    "optical switch": 5,
    "optical switching": 5,
    "circuit switch": 5,
    "circuit switching": 6,

    # === 光互连 / 光网络 ===
    "optical interconnect": 5,
    "optical network": 5,
    "optical fabric": 5,
    "photonic interconnect": 5,
    "photonic network": 5,
    "photonic switch": 5,

    # === 可重构光拓扑 ===
    "reconfigurable optical": 5,
    "optical topology": 5,
    "reconfigurable topology": 5,
    "reconfigurable network": 5,

    # === 波分 / MEMS / 交叉连接 ===
    "wavelength division": 5,
    "wdm": 5,
    "optical mems": 5,
    "optical cross-connect": 5,
    "optical cross connect": 5,

    # === 共封装光学 / 新 infra ===
    "co-packaged optics": 5,
    "co-packaged optical": 5,
    "cpo": 6,
    "ccl": 6,
    "optical i/o": 5,
    "optical io": 4,
    "optical transceiver": 5,
    "silicon photonic": 4,

    # === 调度与光网络交叉 ===
    "optical scheduling": 6,
    "topology engineering": 6,
    "topology reconfiguration": 6,
}

OCS_NEGATIVE_KEYWORDS = {
    "survey": -2,
    "tutorial": -2,
    "review": -2,
}

OCS_MIN_SCORE = 5

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "seen_papers.json"
OUTPUT_FILE = SCRIPT_DIR / "daily_digest.md"

# =========================
# 邮件配置（可选：python Arxiv_filter.py --send）
# =========================
# 密码不要写在代码里。二选一：
#   1. 环境变量:  export ARXIV_DIGEST_EMAIL_PASSWORD="你的Gmail应用专用密码"
#   2. 本地文件:  在同目录下创建 .email_password（只包含密码一行，已在 .gitignore）
#
# 获取 Gmail 应用专用密码: https://myaccount.google.com/apppasswords

def _load_email_password():
    """从环境变量或本地文件加载密码，避免明文入 repo"""
    pw = os.environ.get("ARXIV_DIGEST_EMAIL_PASSWORD", "")
    if pw:
        return pw
    pw_file = SCRIPT_DIR / ".email_password"
    if pw_file.exists():
        return pw_file.read_text(encoding="utf-8").strip()
    return ""

EMAIL_CONFIG = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "sender": "rawking1621@gmail.com",
    "password": _load_email_password(),
    "recipient": "rawking1621@gmail.com",
}

# =========================
# 工具函数
# =========================

def load_seen():
    """Return dict: {paper_id: {"title": ..., "keywords": [...]}}"""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(STATE_FILE, "w") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


def clean_text(text):
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def score_paper(title, summary):
    text = clean_text(title + " " + summary)

    score = 0
    matched = []
    max_single = 0  # 最高单关键词分，用于核心词门槛

    for kw, value in KEYWORDS.items():
        if kw in text:
            score += value
            matched.append((kw, value))
            if value > max_single:
                max_single = value

    for kw, value in NEGATIVE_KEYWORDS.items():
        if kw in text:
            score += value

    # sort by score descending, then return just the keyword names
    matched.sort(key=lambda x: x[1], reverse=True)
    matched_keywords = [kw for kw, _ in matched]

    return score, max_single, matched_keywords


def score_paper_ocs(title, summary):
    """独立 OCS 评分，不与主关键词混算"""
    text = clean_text(title + " " + summary)

    score = 0
    matched = []

    for kw, value in OCS_KEYWORDS.items():
        if kw in text:
            score += value
            matched.append((kw, value))

    for kw, value in OCS_NEGATIVE_KEYWORDS.items():
        if kw in text:
            score += value

    matched.sort(key=lambda x: x[1], reverse=True)
    matched_keywords = [kw for kw, _ in matched]

    return score, matched_keywords


def send_email(papers, ocs_papers):
    """发送邮件：仅包含标题 + arXiv 链接"""
    config = EMAIL_CONFIG

    if not config["password"]:
        print("[email] 未配置密码，跳过发送。请编辑 EMAIL_CONFIG。")
        return False

    today = datetime.now().strftime("%Y-%m-%d")

    def format_paper_list(paper_list, title_label):
        if not paper_list:
            return f"{title_label}: 0 papers\n"
        lines = [f"{title_label} ({len(paper_list)} papers):", ""]
        for i, p in enumerate(paper_list, 1):
            lines.append(f"  {i}. {p['title']}")
            lines.append(f"     {p['link']}")
            lines.append("")
        return "\n".join(lines)

    plain_text = (
        f"arXiv Daily Digest ({today})\n"
        f"{'=' * 50}\n\n"
        f"{format_paper_list(papers, 'Main Digest')}\n"
        f"{format_paper_list(ocs_papers, 'OCS & Optical Networking Spotlight')}\n"
        f"---\n"
        f"详细评分见 daily_digest.md 或运行 Arxiv_filter.py\n"
    )

    def format_html_list(paper_list, title_label):
        if not paper_list:
            return f"<h3>{title_label}: 0 papers</h3>"
        items = "".join(
            f'<li><a href="{p["link"]}">{p["title"]}</a></li>'
            for p in paper_list
        )
        return f"<h3>{title_label} ({len(paper_list)} papers)</h3><ol>{items}</ol>"

    html_body = (
        f"<html><body>"
        f"<h2>arXiv Daily Digest ({today})</h2>"
        f"{format_html_list(papers, 'Main Digest')}"
        f"{format_html_list(ocs_papers, 'OCS & Optical Networking Spotlight')}"
        f"<hr><p style='color:#888;font-size:12px;'>"
        f"详细评分见 daily_digest.md</p>"
        f"</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"arXiv Daily Digest ({today}) — {len(papers)}m + {len(ocs_papers)}o"
    msg["From"] = config["sender"]
    msg["To"] = config["recipient"]

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30)
        server.starttls()
        server.login(config["sender"], config["password"])
        server.sendmail(config["sender"], [config["recipient"]], msg.as_string())
        server.quit()
        print(f"[email] 已发送到 {config['recipient']}")
        return True
    except Exception as e:
        print(f"[email] 发送失败: {e}")
        return False


# =========================
# 主逻辑
# =========================

def fetch_papers():

    seen = load_seen()           # 历史元数据，仅供参考，不阻塞选择
    new_seen = dict(seen)        # 本次运行记录（含历史），用于跨 feed 去重
    seen_this_run = set()        # 本次运行已处理的 paper_id，避免同次运行内重复打分

    selected = []
    ocs_selected = []
    stats = {}  # feed_url -> {total, skipped, selected, ocs_selected}

    for feed_url in RSS_FEEDS:

        feed = feedparser.parse(feed_url)
        feed_total = 0
        feed_skipped = 0
        feed_selected = 0
        feed_ocs_selected = 0

        for entry in feed.entries:

            paper_id = entry.id
            feed_total += 1

            # 仅跳过同一次运行内已经处理过的（跨 feed 重复）
            if paper_id in seen_this_run:
                feed_skipped += 1
                continue

            seen_this_run.add(paper_id)

            title = entry.title
            summary = entry.summary
            link = entry.link

            # —— 主过滤器 ——
            score, max_single, matched = score_paper(title, summary)

            if score >= MIN_SCORE:
                selected.append({
                    "title": title,
                    "score": score,
                    "max_single": max_single,
                    "matched": matched,
                    "link": link,
                    "summary": summary[:400]
                })
                feed_selected += 1

            # —— OCS 过滤器（独立） ——
            ocs_score, ocs_matched = score_paper_ocs(title, summary)

            if ocs_score >= OCS_MIN_SCORE:
                ocs_selected.append({
                    "title": title,
                    "score": ocs_score,
                    "matched": ocs_matched,
                    "link": link,
                    "summary": summary[:400]
                })
                feed_ocs_selected += 1

            # store title + top 3 keywords for metadata
            new_seen[paper_id] = {
                "title": title,
                "keywords": matched[:3],
                "ocs_keywords": ocs_matched[:3]
            }

        stats[feed_url] = {
            "total": feed_total,
            "skipped": feed_skipped,
            "selected": feed_selected,
            "ocs_selected": feed_ocs_selected,
        }

    selected.sort(
        key=lambda x: x["score"],
        reverse=True
    )
    ocs_selected.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # 截断到 TOP-N
    total_main = len(selected)
    total_ocs = len(ocs_selected)
    selected = selected[:MAX_PAPERS]
    ocs_selected = ocs_selected[:MAX_OCS_PAPERS]

    save_seen(new_seen)

    # 打印统计信息
    total_in_feeds = sum(s["total"] for s in stats.values())
    total_skipped = sum(s["skipped"] for s in stats.values())
    print(f"Total entries across feeds: {total_in_feeds}")
    print(f"Duplicates skipped (this run): {total_skipped}")
    print(f"Already seen (from prior runs): {len(seen)}")
    print(f"Main filter matched: {total_main} → top {len(selected)}")
    print(f"OCS spotlight matched: {total_ocs} → top {len(ocs_selected)}")
    print(f"New papers seen this run: {total_in_feeds - total_skipped}")
    print()

    return selected, ocs_selected, total_main, total_ocs


def generate_markdown(papers, ocs_papers, total_main, total_ocs):

    today = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"# arXiv Daily Digest ({today})",
        "",
    ]

    # ========================
    # 主 Digest
    # ========================
    main_note = (
        f"Showing top {len(papers)} of {total_main} matched"
        if total_main > len(papers) else f"Total: {len(papers)}"
    )
    lines.extend([
        "## Main Digest",
        "",
        f"{main_note}",
        ""
    ])

    if papers:
        for i, paper in enumerate(papers, start=1):
            lines.extend([
                f"### {i}. {paper['title']}",
                "",
                f"**Score:** {paper['score']}",
                "",
                f"**Keywords:** {', '.join(paper['matched'])}",
                "",
                f"**Link:** {paper['link']}",
                "",
                "**Abstract snippet:**",
                "",
                paper["summary"],
                "",
                "---",
                ""
            ])
    else:
        lines.extend([
            "_No papers matched the main filter today._",
            "",
            "---",
            ""
        ])

    # ========================
    # OCS / 光交换 Spotlight
    # ========================
    ocs_note = (
        f"Showing top {len(ocs_papers)} of {total_ocs} matched"
        if total_ocs > len(ocs_papers) else f"Total: {len(ocs_papers)}"
    )
    lines.extend([
        "## OCS & Optical Networking Spotlight",
        "",
        f"{ocs_note}",
        ""
    ])

    if ocs_papers:
        for i, paper in enumerate(ocs_papers, start=1):
            lines.extend([
                f"### {i}. {paper['title']}",
                "",
                f"**OCS Score:** {paper['score']}",
                "",
                f"**OCS Keywords:** {', '.join(paper['matched'])}",
                "",
                f"**Link:** {paper['link']}",
                "",
                "**Abstract snippet:**",
                "",
                paper["summary"],
                "",
                "---",
                ""
            ])
    else:
        lines.extend([
            "_No OCS-related papers found today._",
            "",
            "---",
            ""
        ])

    OUTPUT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def main():

    do_send = "--send" in sys.argv

    papers, ocs_papers, total_main, total_ocs = fetch_papers()

    generate_markdown(papers, ocs_papers, total_main, total_ocs)

    print(f"Main filter: {len(papers)} papers")
    print(f"OCS spotlight: {len(ocs_papers)} papers")
    print(f"Saved to {OUTPUT_FILE}")

    if do_send:
        send_email(papers, ocs_papers)


if __name__ == "__main__":
    main()