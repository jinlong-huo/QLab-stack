# Reading Roadmap

> "如果你刚加入这个组，按这个顺序读。"

## Phase 0: 组内基础（第一周）

1. [welcome.md](../onboarding/welcome.md) — 了解组文化和规则
2. [glossary.md](glossary.md) — 过一遍术语表，遇到不认识的回来查
3. [tools.md](../onboarding/tools.md) — 装好所有工具

## Phase 1: 系统与网络基础（第 2-4 周）

如果你没有分布式系统背景：

1. **Datacenter TCP 入门** — DCTCP (SIGCOMM 2010)
2. **RDMA 基础** — 选一篇 RDMA survey
3. **分布式训练通信** — 选一篇关于 all-reduce 优化的经典论文

## Phase 2: 按方向深入

### LLM Inference Serving

**★ 必读：**

1. **Efficient Memory Management for Large Language Model Serving with PagedAttention** (Kwon et al., SOSP 2023) — vLLM: PagedAttention + continuous batching, the foundation of modern LLM serving
2. **Orca: A Distributed Serving System for Transformer-Based Generative Models** (Yu et al., OSDI 2022) — Iteration-level scheduling, the precursor to continuous batching
3. **Splitwise: Efficient Generative LLM Inference Using Phase Splitting** (Patel et al., ISCA 2024) — Prefill/decode disaggregation for GPU cost efficiency
4. **Sarathi-Serve: Efficient LLM Inference with Sequence-Parallel Schedulers** (Agrawal et al., OSDI 2024) — Stalls-free scheduling via chunked prefill
5. **DistServe: Disaggregating Prefill and Decoding for Goodput-Optimized LLM Serving** (Zhong et al., OSDI 2024) — Disaggregated architecture with resource elasticity

**推荐阅读：**

- **FlexGen: High-Throughput Generative Inference of Large Language Models with a Single GPU** (Sheng et al., ICML 2023) — Offloading strategies for constrained hardware
- **Fast Distributed Inference Serving for Large Language Models** (Jin et al., ATC 2024) — Pipeline-parallel serving across multiple nodes
- **SGLang: Efficient Execution of Structured Language Model Programs** (Zheng et al., NeurIPS 2024) — RadixAttention + structured generation
- **Taming Throughput-Latency Tradeoff in LLM Inference with Sarathi-Serve** — See above (★)
- **ServerlessLLM: Locality-Enhanced Serverless Inference for LLMs** (Fu et al., OSDI 2024)

### RDMA & Datacenter Networking

**★ 必读：**

1. **DCTCP: Efficient Packet Transport for the Commoditized Data Center** (Alizadeh et al., SIGCOMM 2010) — ECN-based congestion control, the foundation
2. **RDMA over Commodity Ethernet at Scale** (Guo et al., SIGCOMM 2016) — Deploying RoCEv2 in production datacenters
3. **Congestion Control for Large-Scale RDMA Deployments** (Zhu et al., SIGCOMM 2015) — DCQCN: the standard RDMA congestion control
4. **Re-architecting Datacenter Networks and Stacks for Low Latency and High Performance** (Handley et al., SIGCOMM 2017) — NDP: rethinking the whole stack for latency
5. **Aquila: A Unified Congestion Control Framework for RDMA** (Wang et al., SIGCOMM 2024) — Learning-based CC for heterogeneous RDMA

**推荐阅读：**

- **TIMELY: RTT-based Congestion Control for the Datacenter** (Mittal et al., SIGCOMM 2015) — RTT-based CC as an alternative to ECN
- **pHost: Distributed Near-Optimal Datacenter Transport** (Gao et al., SIGCOMM 2016) — NDP variant for incast-heavy workloads
- **HPCC: High Precision Congestion Control** (Li et al., SIGCOMM 2019) — INT-based precise CC
- **IRDMA: Inter-Network RDMA** (SIGCOMM 2023) — RDMA across multiple datacenters
- **PowerTCP: Pushing the Performance Limits of Datacenter Networks** (Addanki et al., SIGCOMM 2022) — Learning-based CC via multi-agent RL

### Optical Circuit Switching & New Infra

**★ 必读：**

1. **Jupiter Evolving: A Decade of Google's Datacenter Network Transformation** (Poutievski et al., SIGCOMM 2022) — OCS deployment journey at Google scale
2. **Helios: A Hybrid Electrical/Optical Switch Architecture for Modular Data Centers** (Farrington et al., SIGCOMM 2010) — Early hybrid OCS architecture
3. **c-Through: Part-time Optics in Data Centers** (Wang et al., SIGCOMM 2010) — The case for optical circuit switching
4. **Rotornet: A Scalable Low-Latency Optical Network** (Mellette et al., SIGCOMM 2017) — Fast-switching OCS with wavelength routing
5. **Topology Engineering: Rethinking Datacenter Network Architecture** (Zhao et al., SIGCOMM 2021) — Dynamic optical topology reconfiguration

**推荐阅读：**

- **Mordia: Intra-Cluster Optical Switching** (Porter et al., SIGCOMM 2012) — Microsecond OCS prototype
- **Sirius: A Flat Datacenter Network with Nanosecond Optical Switching** (Ballani et al., SIGCOMM 2020) — Tunable laser + AWGR
- **Flexfly: Enabling a Reconfigurable Dragonfly through Silicon Photonics** (Wen et al., SC 2016) — Optical reconfiguration in HPC topologies
- **Opera: Optical Network Design for ML Training** (Zhu et al., SIGCOMM 2024) — Optical networking specifically for distributed ML
- **Co-packaged Optics for Datacenter Networking** (multiple, JLT/JOC 2020-2024) — CPO standards and deployment

### Scheduling & Resource Allocation

**★ 必读：**

1. **Dominant Resource Fairness: Fair Allocation of Multiple Resource Types** (Ghodsi et al., NSDI 2011) — DRF: the theoretical foundation
2. **Apollo: Scalable and Coordinated Scheduling for Cloud-Scale Computing** (Boutin et al., OSDI 2014) — Distributed scheduling at Microsoft scale
3. **Tiresias: A GPU Cluster Manager for Distributed Deep Learning** (Gu et al., NSDI 2019) — Two-level scheduling for GPU training jobs
4. **Pollux: Co-Adaptive Cluster Scheduling for Goodput-Optimized Deep Learning** (Qiao et al., OSDI 2021) — Online re-allocation for DL training
5. **Clockwork: Custom Scheduling for Large-Scale Transformer Inference** (Gujarati et al., OSDI 2020) — Predictable latency for inference serving

**推荐阅读：**

- **Gandiva: Introspective Cluster Scheduling for DL Training** (Xiao et al., OSDI 2018) — Timing predictability + packing
- **Sia: Heterogeneity-Aware Scheduling for ML Training Clusters** (Jayaram et al., SIGCOMM 2022) — Heterogeneous GPU scheduling
- **Aryl: Elastic Scheduling for LLM Inference Clusters** (Li et al., ATC 2024) — Elasticity in disaggregated inference
- **Model-Switching: Enabling Fine-Grained Sharing of GPU Resources** (Bai et al., OSDI 2023) — GPU context switching
- **Llumnix: Dynamic Scheduling for Large Language Model Inference** (Sun et al., OSDI 2024) — Rescheduling across instances

---

> 每个方向标注 3-5 篇必读论文（★），再加 5-10 篇推荐论文。读完一篇就在 paper-notes 里写笔记，然后把链接加到对应方向下。
