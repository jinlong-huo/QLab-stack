# 论文投稿前自查清单

> 逐项核对。全部勾上再投。作者自己填，然后让同事抽查。

---

## Abstract

- [ ] 第一句说清问题
- [ ] 第二句说清方法（不模糊，不用 "novel approach" 这种空话）
- [ ] 有具体数字（提升 X%，降低 Yms）
- [ ] 250 字以内
- [ ] 不含引用

## Introduction

- [ ] 第一段：问题是什么，为什么重要（让 non-expert 能懂）
- [ ] 第二段：现有方法的局限性（公平描述，不 strawman）
- [ ] 第三段：我们的核心 idea（一张图 + 一段话）
- [ ] 第四段：contributions（bulleted list，每条一句话）
- [ ] 最后一段：paper structure（可选，部分 venue 不需要）

## Method

- [ ] 关键设计决策解释了 **why**，不仅是 **what**
- [ ] 每个公式的符号在第一次出现时定义
- [ ] 没有 "obviously" 或 "clearly"（如果你觉得 obvious，可能写漏了推导）
- [ ] 算法伪代码和文字描述一致

## Experiments

- [ ] 所有 baselines 是当前 SOTA（不是 5 年前的方法）
- [ ] 每个 figure/table 在正文中被引用
- [ ] Figure caption 独立可读（不依赖正文上下文）
- [ ] 误差线 / 标准差标注了
- [ ] 超参选择有 justification（grid search 还是 heuristic）
- [ ] 消融实验回答了 "每个组件贡献多少"
- [ ] 和最相关的 baseline 做了统计显著性检验

## Related Work

- [ ] 每个方向 3-5 篇代表性论文
- [ ] 和我们的方法对比时，写清楚了区别，不只是并列
- [ ] 没有遗漏最近 2 年的相关工作
- [ ] 引用了我们组自己的前序工作（如果有）

## 其他

- [ ] 所有作者确认了 author list 和顺序
- [ ] 没有超过 page limit
- [ ] 附录和正文内容没有重复
- [ ] 代码/repo 链接已准备好（如果要求开源）
- [ ] PDF 编译无 error/warning
- [ ] 给同事发了 internal-review request
