# Changelog

本文件记录 Group Toolkit 自身的变更。论文笔记、知识库日常更新不在此列。

---

## 2026-06-12 — v0.2.0

### Added
- `members/` — 个人工作区命名空间（个人笔记、项目细节、实验记录与共享资产分离）
- `members/_template/` — 新成员目录模板，加入即可 scaffold
- `offboarding/` — 离组流程（exit checklist + knowledge handover + extract script）
- `.gitattributes` — 跨平台换行符规范化
- `.editorconfig` — 编辑器设置一致性
- `.github/CODEOWNERS` — 按目录指定 reviewer
- `Makefile` — 常用命令入口（`make help` 查看全部）
- `CONTRIBUTING.md` — 组内协作规范

### Changed
- `README.md` — 重构，增加目录可见性表格和 offboarding 章节
- `projects.md` — 增加私人项目细节指引和离组处理流程
- `paper-notes/` — 改为仅存放共享模板，个人笔记迁移至 `members/<name>/paper-notes/`

---

## 2026-06-11 — v0.1.0 (initial)

### Added
- `Arxiv_filter.py` — arXiv 每日摘要 + 邮件推送
- `setup.zsh` / `setup.ps1` — macOS / Windows 定时任务安装
- `paper-notes/` — 论文笔记模板
- `knowledge-base/` — glossary + reading-roadmap + topics
- `onboarding/` — welcome + tools + how-we-work
- `repro/` — 实验复现模板
- `templates/` — 周报 / 会议记录 / 论文报告 / 组内预审 / 投稿自查 / Figure 规范
- `survival-guide/` — 选方向 / 写论文 / 讲 talk / 会议列表 / 职业建议
- `projects.md` — 组内活跃项目一览
