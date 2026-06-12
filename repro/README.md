# Repro

论文实验复现记录。每篇复现的论文一个子目录。

## 为什么

- 论文声称的性能和实际跑出来的往往不一样。这些差异是研究 insight 的来源。
- 别人踩过的环境坑，下一个组员不用再踩一遍。
- 跑过的代码是资产，下次做类似方向可以直接改，不用从零开始。

## 使用方式

```bash
cp -r repro/template repro/论文简称
# 边跑边填 README.md
```

## 目录约定

```
repro/
├── template/              ← 复制这个
├── llama-inference-sched/ ← 论文简称
├── ocs-topology-reconf/   ← 论文简称
└── ...
```
