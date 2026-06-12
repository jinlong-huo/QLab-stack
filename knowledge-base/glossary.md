# Glossary

组内常用术语速查。新人入职第一站。按字母排序。

---

## C

### CCL (Collective Communication Library)
高性能集合通信库（如 NCCL），用于多 GPU/多节点间的 all-reduce、all-gather 等操作。在 LLM 训练和推理中直接影响通信效率。

### CPO (Co-packaged Optics)
共封装光学。将光收发器与 ASIC/switch chip 封装在同一基板上，缩短电互联距离，降低功耗和延迟。数据中心网络的关键新 infra。

---

## K

### KV Cache
LLM 推理中缓存的 Key-Value 状态，避免每个 token 生成时重复计算历史 token 的 self-attention。KV cache 的管理（分配、淘汰、迁移）是 LLM serving 系统的核心问题。

---

## O

### OCS (Optical Circuit Switching)
光电路交换。用 MEMS 微镜或其他光学器件在数据中心内部建立物理光通路，替代传统电交换机。Google Jupiter 架构的核心技术。优势是带宽高、功耗低、与数据速率无关。

---

## R

### RDMA (Remote Direct Memory Access)
远程直接内存访问。允许一台机器直接读写另一台机器的内存，绕过 CPU 和内核网络栈。在分布式训练和推理中用于高效的梯度同步和 KV cache 传输。

---

## T

### Tail Latency
尾延迟。P99/P99.9 的请求延迟。在在线服务中比平均延迟更关键——一个慢请求拖慢整个 batch。LLM serving 的核心指标之一。

---

> 遇到新术语直接加进来，保持一句话定义 + 一段关键背景的格式。
