# Contributing

> 组内协作规范。怎么提交、怎么 PR、不同类型的内容放哪里。

## 目录规则速查

| 你要做什么 | 放哪里 | 谁看得见 |
|---|---|---|
| 读了一篇论文，写笔记 | `members/<name>/paper-notes/` | 组内 |
| 开始一个新项目 | `projects.md`（标题） + `members/<name>/projects/`（细节） | 标题全组 / 细节本人+导师 |
| 复现了一篇论文 | `members/<name>/repro/` | 组内 |
| 补充术语 | `knowledge-base/glossary.md` | 全组 |
| 推荐阅读路线 | `knowledge-base/reading-roadmap.md` | 全组 |
| 改进模板 | `templates/` | 全组 |
| 改进流程 | PR + 更新相关 README | 全组 |

## 分支策略

```
main          ← 稳定版，所有人从这里 clone
feature/*     ← 改模板、加知识库内容、修工具
```

- `main` 受保护，不允许直接 push
- 所有改动通过 PR 合入
- PR 至少需要一个 approve

## Commit 规范

```
<type>: <简短描述>

类型:
  note      论文笔记（新增或更新）
  kb        知识库更新（glossary / roadmap / topics）
  template  模板更新
  tool      工具脚本更新（Arxiv_filter.py / setup 等）
  docs      文档更新（README / onboarding / offboarding）
  infra     基础设施（Makefile / CI / .gitignore）
  member    成员变更（onboard / offboard）
  chore     杂项

示例:
  note: add Attention Is All You Need
  kb: glossary - add "KV cache" entry
  template: weekly-report - add blocker section
  tool: Arxiv_filter - add OCS keyword weights
  member: onboard zhangsan
  member: offboard lisi
```

无强制格式，但推荐。重点是让 `git log --oneline` 可读。

## PR 流程

1. 从 `main` 拉分支
2. 改内容
3. 提交 PR
4. 等 CODEOWNERS review
5. Merge

## 新成员 checklist

- [ ] 确认你有 repo 写权限
- [ ] `cp -r members/_template members/<your-name>` 建个人目录
- [ ] 读 [onboarding/](onboarding/)
- [ ] 更新 `projects.md`（如果立即参与项目）
