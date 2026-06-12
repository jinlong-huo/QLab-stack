# Group Toolkit

> 可传承的组内工作流基础设施。
> 自动化发现论文 → 结构化阅读 → 知识沉淀 → 新成员上手，全链路模板化。

## 地图

```
group-toolkit/
│
├── Arxiv_filter.py              ← 🔍 发现：每日自动抓论文 + 邮件推送
├── setup.zsh / setup.ps1        ← macOS / Windows 定时任务安装
│
├── paper-notes/                 ← 📖 理解：论文笔记库（每篇一文件）
│   ├── template.md              ←    标准笔记模板（what/why/how/pros/cons）
│   └── 2026/                    ←    按年份归档
│
├── knowledge-base/              ← 🧠 沉淀：按主题组织的研究知识
│   ├── glossary.md              ←    术语表（新人第一站）
│   ├── reading-roadmap.md       ←    按方向分级的阅读路线图
│   └── topics/                  ←    各方向的论文脉络与 SOTA
│
├── onboarding/                  ← 🚀 上手：新成员入职指南
│   ├── welcome.md               ←    组文化 + 第一周 checklist
│   ├── tools.md                 ←    工具安装 & 使用
│   └── how-we-work.md           ←    沟通、代码、会议、文档规范
│
├── repro/                       ← 🔬 复现：实验复现记录
│   └── template/                ←    复现模板（环境 / 结果 / 踩坑记录）
│
├── templates/                   ← 📋 模板：组内通用格式
│   ├── weekly-report.md         ←    周报
│   ├── meeting-notes.md         ←    会议记录
│   ├── paper-presentation.md    ←    组会论文报告
│   ├── internal-review.md       ←    投稿前组内预审
│   ├── writing-checklist.md     ←    论文投稿前自查清单
│   └── figure-guide.md          ←    Figure 规范（颜色 / 排版 / 工具）
│
├── survival-guide/              ← 🧭 生存手册：组员最需要但最少被写下来的
│   ├── how-to-choose-problem.md ←    怎么选研究方向
│   ├── how-to-write-paper.md    ←    从 outline 到 camera-ready
│   ├── how-to-give-talk.md      ←    组会 & conference presentation
│   ├── conference-list.md       ←    各顶会 deadline 和投稿经验
│   └── career-advice.md         ←    找教职 / 实习 / 工业界
│
└── projects.md                  ← 📊 组内活跃项目一览（谁在做什么）
```

---

## 🔍 arXiv Daily Digest

每天自动从 arXiv 抓取最新论文，按关键词打分排序，选出最相关的 top-15 发送到邮箱。

**❗ 注意关键词选取和对应权重设置，直接关乎筛选文章质量。**
可以配合 Semantic Scholar、Hugging Face Daily Papers 等其他推荐源使用。

**关注方向**：LLM 推理 / GPU 数据中心 / RDMA 网络 / 光交换 (OCS) / 调度与资源分配

### 快速开始

**1. 装依赖**

```bash
pip install feedparser
```

**2. 配邮箱**

密码**不要写在代码里**，二选一：

```bash
export ARXIV_DIGEST_EMAIL_PASSWORD="你的Gmail应用专用密码"
# 或: echo "密码" > .email_password
```

> Gmail 应用专用密码：https://myaccount.google.com/apppasswords（需先开两步验证）

**3. 测试**

```bash
python3 Arxiv_filter.py --send
```

**4. 设为每天自动跑**

- macOS: `./setup.zsh`
- Windows (管理员 PowerShell): `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned; .\setup.ps1`

### 调参

| 参数 | 默认值 | 作用 |
|---|---|---|
| `MIN_SCORE` | 5 | 初筛门槛，越低越多 |
| `MAX_PAPERS` | 15 | 主 digest 最多显示几篇 |
| `MAX_OCS_PAPERS` | 10 | OCS spotlight 最多显示几篇 |

---

## 📖 Paper Notes

每读完一篇论文，复制模板 → 填空 → 提交 PR。

```bash
cp paper-notes/template.md paper-notes/2026/作者-关键词.md
```

模板覆盖 **What / Why / How / Pros / Cons / Follow-ups**。三个月后你不会记得这篇论文讲了什么，但笔记会。

→ [paper-notes/template.md](paper-notes/template.md)

---

## 🧠 Knowledge Base

按主题沉淀知识，不按论文排列。

- [glossary.md](knowledge-base/glossary.md) — 术语表，新人第一站
- [reading-roadmap.md](knowledge-base/reading-roadmap.md) — 按方向分级的阅读路线
- [topics/](knowledge-base/topics/) — 各方向论文脉络与 SOTA

---

## 🚀 Onboarding

新成员入职指南，按顺序读：

1. [welcome.md](onboarding/welcome.md) — 组文化 & 第一周 checklist
2. [tools.md](onboarding/tools.md) — 装好所有工具
3. [how-we-work.md](onboarding/how-we-work.md) — 日常规范
4. [reading-roadmap](knowledge-base/reading-roadmap.md) — 开始读论文

---

## 🔬 Repro

复现论文的实验记录。每篇一个目录，记录环境、结果、踩坑。跑过的代码是资产——下次做类似方向可以直接改，不用从零开始。

→ [repro/README.md](repro/README.md)

---

## 📋 Templates

| 模板 | 用途 | 频率 |
|---|---|---|
| [weekly-report.md](templates/weekly-report.md) | 周报 | 每周五 |
| [meeting-notes.md](templates/meeting-notes.md) | 会议记录 | 每次会议 |
| [paper-presentation.md](templates/paper-presentation.md) | 组会讲论文 | 轮到你 |
| [internal-review.md](templates/internal-review.md) | 投稿前组内预审 | 每次投稿前 |
| [writing-checklist.md](templates/writing-checklist.md) | 论文投稿前自查 | 每次投稿前 |
| [figure-guide.md](templates/figure-guide.md) | Figure 规范 | 画图前看一眼 |

---

## 🧭 Survival Guide

组员真正需要、但很少被写下来的东西。按需阅读：

| 文档 | 什么时候读 |
|---|---|
| [how-to-choose-problem.md](survival-guide/how-to-choose-problem.md) | 不知道做什么方向 |
| [how-to-write-paper.md](survival-guide/how-to-write-paper.md) | 第一次写 paper |
| [how-to-give-talk.md](survival-guide/how-to-give-talk.md) | 下个月要讲 talk |
| [conference-list.md](survival-guide/conference-list.md) | 选投稿目标 |
| [career-advice.md](survival-guide/career-advice.md) | 考虑下一步 |

---

## 📊 Active Projects

→ [projects.md](projects.md) — 谁在做什么，什么阶段，什么产出。

---

## 设计原则

- **模板驱动** — 能填空不写空白页。
- **写下来才算发生过** — 讨论、决策、理解，全落成文字。
- **一个人的笔记 → 全组的资产** — glossary 补一个术语、reading-roadmap 加一篇推荐，下一个成员直接复用。
- **迭代优于完美** — 不完整的 PR > 空着的 TODO。
