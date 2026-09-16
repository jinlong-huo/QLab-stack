---
topic: llm
category: LLM
vault_source: LLM/LLMBasics.md
vault_sha256: dff3707e3f169bd2cad353716cfe16168202ff10d7d730bcc15089f95fe1b636
seed_sha256: dff3707e3f169bd2cad353716cfe16168202ff10d7d730bcc15089f95fe1b636
seeded: 2026-09-16
status: seeded
sync: manual
tags: [LLM,basics,inference,kv-cache]
---

<!-- SEED:PROVENANCE:BEGIN -->
> Seeded verbatim from the vault note `LLM/LLMBasics.md` on 2026-09-16.
> Do not hand-edit the body below — edit the vault note, then run `make seed-sync ARGS=--apply`.
<!-- SEED:PROVENANCE:END -->

前置：本文涉及 LLM 推理优化，需要 GPU 基础知识，可参考 陆淳：[GPU基础知识](https://zhuanlan.zhihu.com/p/683016265)

## 一、推理显存优化

### 1、KV Cache

- 背景：LLM 推理服务的吞吐量受显存限制；研究显示现有系统缺乏精细显存管理，浪费 60%~80% 的显存，主要来自 KV Cache，因此有效管理 KV Cache 是重大挑战。
- 原理：Transformer 自回归推理每轮只预测 1 个 token，与历史 tokens 拼接作为下轮输入。前后两轮输入仅差 1 个 token，存在重复计算；KV Cache 把可复用的键值向量结果保存下来避免重复计算。
  - Without KV Cache：每步计算全量 Wq(X)/Wk(X)/Wv(X) 与全量 Attn；
  - With KV Cache：第一步算完整 Attn 并把 KV 存成 KV_cache；第二步只取上一步 Next Token 算 QKV，将 [KV_cache, KV] 联合计算。
- KV cache 峰值显存公式：**2 × Length × batch_size × [d × n_kv_heads] × Layers × k-bits**
  - 2：Key/Value 两个向量；k-bits：数据类型（FP16 占 2 bytes，可量化）；Length：序列长度（可用循环队列管理窗口 KV 来减小）；Layers：层数；d × n_kv_heads：KV 维度（MQA/GQA 通过减少 KV 头数降低显存）；batch_size：与 KV Cache 线性关系，随 batch 增大开销快速增大甚至超过模型本身；操作系统管理：GPU KV Cache 有效存储率低 → PagedAttention。
  - 例：bf16 下 13B 模型仅有约 10G 空间存 KV cache。
- 引入后推理分两阶段：
  - **预填充（Prefill）**：计算第一个输出 token，需为每个 Transformer layer 计算并保存 key/value cache；FLOPs 同 KV Cache 一致，存在大量 GEMM，属 Compute-bound。
  - **解码（Decoder）**：第二个至最后一个 token，KV Cache 已存历史，每轮只读 Cache 并把新 K/V 追加写入；GEMM 变 GEMV，FLOPs 降低、速度更快，属 Memory-bound。

### 2、MQA / GQA

- 论文：MQA [https://arxiv.org/pdf/1911.02150.pdf](https://arxiv.org/pdf/1911.02150.pdf)；GQA [https://arxiv.org/pdf/2305.13245.pdf](https://arxiv.org/pdf/2305.13245.pdf)
- 核心思想：减少 KV cache 数量，以少量 KV cache 对应多个 query。
- MQA（Multi Query Attention，多查询注意力）：MHA 的变体，不同注意力头共享同一份 K、V（矩阵仅一份），每个头只单独保留 query 参数 → 显存占用大幅减少。因改变注意力结构，通常需从训练开始就支持，也可对已训练模型微调添加（约 5% 的原始训练数据量即可达不错效果）。采用模型：Falcon、SantaCoder、StarCoder 等。
- GQA（Grouped Query Attention，分组查询注意力）：介于 MHA 与 MQA 之间的折中——将 query 头分组，每组共享一个 key 头与一个 value 头。既保留 MHA 的一定表达能力，又通过减少内存访问压力加速推理。

### 3、Page Attention（vLLM）

- 论文：[https://arxiv.org/pdf/2309.06180.pdf](https://arxiv.org/pdf/2309.06180.pdf)
- 此前 KV Cache 管理低效：HF Transformers 随执行动态申请显存（GPU 显存分配耗时一般高于 CUDA kernel 执行 → 极大时延开销 + 显存碎片化）；FasterTransformer 预先分配足够长空间（如 LLaMA-7B 上下文 2048 则每用户预分配 2048 tokens 空间，实际用不满即浪费）。
- 思想：把操作系统虚拟内存管理引入 LLM。KV Cache 分成若干块，每块含固定数量 token 的 K/V；块在内存无需连续。类比：块=页面、token=字节、序列=进程。序列的连续逻辑块经 Block Table 映射到非连续物理块，物理块在生成新 token 时按需分配。
- 优点：内存浪费只发生在序列的最后一个块，实践中接近最优（浪费 < 4%）。块中存多个 token 使 PagedAttention 内核可并行处理更多位置（提高硬件利用率、减延迟；块越大碎片越多）。
- 解码示例（prompt "Four score and seven years ago our" 7 tokens，块容量 4）：不预留内存，只为即时计算保留必要 KV 块；前 2 个逻辑块（Block 0/1）映射到物理块 7、1；prefill 用传统自注意力生成 prompt KV 与首个输出 token，前 4 token 存逻辑块 0、后 3 个存逻辑块 1，剩余槽位留作自回归；第一个解码步用 PagedAttention 生成新 token 存进块 1 空槽并同步 Block Table 的 #filled；第二个解码步最后逻辑块已满 → 分配新物理块（物理块 3）并记录映射。
- 内存共享：并行采样时多个输出序列共享同一 prompt 的计算与内存。不同序列可将逻辑块映射到同一物理块共享；跟踪物理块引用计数 + 写时复制（Copy-on-Write）保证安全共享。并行采样和集束搜索的内存使用量降低 55%，可转化为高达 2.2 倍的吞吐量提升。

### 4、FlashAttention

- 论文：[https://arxiv.org/pdf/2205.14135.pdf](https://arxiv.org/pdf/2205.14135.pdf)
- 出发点：此前优化致力于减少 FLOPs，忽略显存 IO。attention O(n²) 复杂度矩阵对 HBM 的重复读写是主要瓶颈。GPU SRAM I/O 速度 19 TB/s vs HBM 1.5 TB/s（差十几倍），容量差几个数量级。计算模式：从 HBM 读块 → SRAM 计算 → 写回 HBM。
- 要解决两件事：① 不访问整个输入计算 softmax；② 不为反向传播存储大的中间 attention 矩阵。
- 方法：
  - **Tiling**：注意力计算重构为把输入分割成块，通过在输入块上多次传递递增地执行 softmax。
  - **Recomputation**：存储前向的 softmax 归一化因子，反向在芯片上快速重算 attention（快于从 HBM 读中间矩阵）。
- 代价：FLOPS 增加，但 HBM 访问大幅减少 → 总体更快（每块输出先按正确归一化因子缩放再相加）。
- v2（出发点：v1 利用 GPU 非对称内存层次把内存降至线性、相对基线快 2~4 倍，但前向吞吐仅达理论峰值 30~50%、反向 25~35%，未达 GEMM 速度；低效源于不同线程块间负载分配不佳 → 低占用率或不必要的共享内存读写）：
  1. **减少中间缩放的次数**：softmax 为数值稳定性（指数增长会溢出）通常减最大值导致遍历 token 3 次；不保存中间最大值和指数和、只保留对数指数和，可减少非矩阵乘法浮点计算。
  2. **序列长度维度的并行**：v1 只在 batch_size/num_heads 维并行，长序列时需减小 batch/head 数导致并行度降低。v2 把 Q 移到外循环、KV 移到内循环（b\*s\*d→b\*s\*k\*m），分块计算的 attention 值块只存 SRAM，避免 HBM 频繁读写。
  3. **分散 warp 间工作负载、减少共享内存通信**：v1 用 split-K（所有 warp 把中间结果写共享内存同步再相加 → 拖慢前向）；v2 用 split-Q，每个 warp 算完 QK^T 后只需对应 V 分片即得 O 分片，无需 warp 间通信。

### 5、Flash-Decoding

- 博客：[https://crfm.stanford.edu/2023/10/12/flashdecoding.html](https://crfm.stanford.edu/2023/10/12/flashdecoding.html)
- 出发点：FlashAttention 不适合直接用于推理——它按 batch size 和 query length 并行，而推理中 query length=1，若 batch < GPU SM 数（A100 有 108 SMs）则只用到 GPU 一小部分；长上下文通常还要减小 batch（batch=1 时 FA 对 GPU 利用率 < 1%）。
- 思路：在 FA 基础上新增并行维度——keys/values 的序列长度。batch 很小但只要上下文够长就能充分利用 GPU；与 FA 类似几乎不用向全局内存存额外大量数据，减少内存开销。
- 三个步骤：① 把 keys/values 分成较小 block；② 用 FlashAttention 并行计算 query 与每个 block 的注意力（与 FA 的最大区别），对每个 block 每行额外记录 attention values 的 log-sum-exp（标量，用于第 3 步 rescale）；③ 对所有 output blocks 做 reduction 得最终输出，用 log-sum-exp 值重调每块贡献。
- 第 1 步分块不涉及 GPU 操作（无需物理分开），只需对 2、3 步执行单独 kernels；最终 reduction 引入少量额外计算，但总体以增加并行化取得更高效率。

## 二、算子融合

- 典型优化技术：减少计算中的访存次数与 Kernel 启动耗时，同样适用于 LLM 推理。例：HF Transformers 推理 LLaMA-7B 有 30 个类型共 2436 个算子（aten::slice 出现 388 次）；大量小算子降低 GPU 利用率、影响速度。
- 业界基本按 Transformer layer 结构手工实现（以 DeepSpeed Inference 为例 4 类）：
  1. 归一化层 + QKV 横向融合：三次 Q/K/V 计算并为一算子，与前置归一化算子融合；
  2. 自注意力计算融合：多个算子合一（FlashAttention 即成熟方案）；
  3. 残差连接、归一化、全连接、激活融合：MLP 中第一个全连接层上下相关算子合一；
  4. 偏置加法 + 残差连接融合。
- 需定制 CUDA kernel、对 GPU 编程要求高；编译器技术涌现出 OpenAI Triton、TVM 等实现自动化/半自动化算子融合。

### FasterTransformer（NVIDIA，GitHub）

- C++/CUDA 编写的 Transformer 推理加速引擎，依赖高度优化的 cuBLAS、cuBLASLt、cuSPARSELt；与 TensorRT 等其他编译器相比特点：支持以分布式方式推理 Transformer 大模型。
- 图融合：多层网络合成单神经网络/单内核计算，减少数据传输、增加数学密度。例：multi-head attention 块所有操作可合并到 1 个内核。FT 仅用 14 个 kernel 完成原近 60 个 kernel 的逻辑：8 个经 cuBLAS 计算矩阵乘（绿色框）+ 6 个自定义 kernel（蓝色框）：add_QKVbias、softmax_kernel、transpose、add_bias_act & add_bias_input_layernorm（含 x\*x\*x 代替 pow、rsqrt、各种 half2 运算）。
- 小 batch 场景（问答、TTS 等）：瓶颈在频繁 kernel launch，简单融合后即显著加速。大 batch：需精细调优矩阵乘与全部自定义 kernel（矩阵乘法算法选择、非矩阵乘操作参数配置、SoftMax 多版本实现、数据结构类型）。
- 附加支持：INT8 低精度量化推理；Ampere 架构部分支持稀疏化；Hopper 架构 FP8 推理；Tensor 并行；Pipeline 并行。

### DeepSpeed Inference（Microsoft）

- 论文：[https://arxiv.org/pdf/2207.00032.pdf](https://arxiv.org/pdf/2207.00032.pdf)
- 与逐元素运算类融合不同，深度融合把逐元素运算、矩阵乘法、转置、约简全部融合进单个内核 → 显著减少内核调用次数与主存访问次数/延迟。
- 推理定制 GeMM：GeMM 性能主要取决于从主存读参数的时间而非计算本身，内核微调以最大化加载参数时的内存带宽利用率；batch 1–10 时比 NVIDIA cuBLAS 高 20%。
- Transformer layer 4 个主要部分：① Input Layer-Norm + QKV GeMMs + bias adds；② Transform + Attention；③ Intermediate FF、Layer-Norm、Bias-add、Residual、GELU；④ Bias-add + Residual。
- 其他优化：多 GPU 并行、INT8 模型量化、推理 pipeline 方案。

### MLC LLM（TVM，GitHub）

- 上述方案主要面向 GPU；MLC LLM 面向轻设备：移动端（iPhone）、消费级电脑（Mac）、Web 浏览器。
- 工作流基于 Apache TVM Unity，扩展 TVM 后端使模型编译透明高效；编译代码转换、融合、内存规划、库卸载（library offloading）等可组合 ML 编译优化是重要特性。
- 特性：Dynamic shape（避免对最大输入长度额外 padding，减少计算量与内存）；低位量化压缩权重 + TVM loop-level TensorIR 为不同压缩编码方案快速定制代码生成；Runtime（TVM 编译库经 TVM runtime 在设备原生环境运行，支持 CUDA/Vulkan/Metal 等主流 GPU 驱动及 C、JavaScript 绑定）。
- 其他支持图融合方案：NVIDIA TensorRT、Tencent TurboTransformers。

### 高性能算子

- 针对 LLM 推理热点函数手写高性能算子可降低时延。
- GEMM（Prefill 阶段）：Self-Attention 与 MLP 的多个 GEMM 占推理时延 80% 以上；NVIDIA 提供 cuBLAS、CUDA、CUTLASS 等不同层级方案（如 FT 大量基于 CUTLASS 的 GEMM 内核）；Self-Attention 是 GEMM+Softmax+GEMM 结构，常与算子融合联合优化。
- GEMV（Decode 阶段）：热点从 GEMM 变 GEMV；GEMV 计算强度更低，优化围绕降低访存开销。
- 高性能算子同样高 GPU 编程要求，且超参数与问题规模相关 → 编译器自动调优（如 autotuning）是研究重点。

## 三、调度优化

- 针对多 Batch 场景，通过对 Batch 的时序优化去除 padding、提高吞吐与设备利用率。传统 Batch 处理是静态的（batch size 在推理完成前保持不变）。

### Async Serving

- Tokenize/Detokenize 在 CPU 执行期间 GPU 空闲；多线程异步 + 流水线 overlap 实现降低时延。

### Dynamic Batch

- 静态 batching 批次固定、无法随计算资源负载动态变化 → GPU 利用率低。
- 动态：维护一个作业队列，在 batch 维度动态插入新序列；缺点：需对输入 padding 使其长度一致，或暂停系统等待构建更大批次。

### ORCA（continuous / iterative-level / in-flight batching）

- 论文：[https://www.usenix.org/system/files/osdi22-yu.pdf](https://www.usenix.org/system/files/osdi22-yu.pdf)
- 问题 1：输入输出长度可变且运行前无法预测 → 传统方法必须等整批序列都完成才执行下一批，出现"气泡"，GPU 利用率低。
- 问题 2：传统 batchsize 固定，无法随负载动态调整（如某时段序列普遍偏短，本可加大 batch）。
- Batching 粒度问题：生成式模型推理是迭代式的，每个请求迭代执行多次、每轮产 1 个 token、迭代次数可能不同（直到 EOS）；以 Request 为粒度的传统 batching 使先结束的请求必须陪跑到整批结束。
- 核心技术（Iteration-level scheduling）：每次只向执行引擎提交一次 Iteration 的计算而非整个 Request → 每步都可动态选择同批请求。调度器维护 Running 与 Waiting 两个队列（状态可相互转换）；每自回归生成 1 个 token 后检查全部序列状态，结束的移出 Running 并标记完成，同时按 FCFS（First Come First Service）从 Waiting 取一个加入 Running → 最大限度消除"气泡"。
- Selective batching：解决 batching 需相同 shape 的问题——并非所有算子都需要相同 shape：Add、Linear、GeLU 等可将 batch flatten，不同 Sequence Length 也能一起算；必须同 shape 的算子（如 Attention）则对不同输入单独计算。
- Dynamic Batching：在 batch 维度动态插入新序列以充分利用显存——每生成 1 个 token 后调度器按剩余显存量动态调整 Running 队列长度（显存多则加长；KV Cache 超显存时把低优先级序列换出至 Waiting 并释放显存）。
- 上述技术已在 Text-Generation-Interface (TGI)、vLLM、OpenPPL-LLM 等框架中实现。

### Dynamic SplitFuse / Sarathi

- Deepspeed FastGen 中处理 prompt 与生成 token 的组合策略。
- 思路：对 prompt 与生成的 token 动态分解与融合，保证模型前向传播大小一致，避免长 prompt 占用过多资源。对比：vLLM 一个前向要么生成 token 要么处理 prompt，token 生成会抢占提示处理；Orca 在生成过程中以完整长度处理提示。
- 实现：长 prompt 分块、经多次前传逐步处理，最后一次与生成的 token 融合；短 prompt 则填充以精确满足目标 token 数量。FastGen = vLLM + Dynamic SplitFuse。
- Sarathi：与 interleaved 1F1B 几乎一致——将一个 batch 的多个 prompt 分割成均匀 chunk，属不同 prompt 的 chunk 交叠排列后执行流水线并行推理；每个 prompt 的 prefill 完成后立即进入生成阶段，后续 prompt 预填充与生成交叠执行，从而减少 bubble。

## 四、分布式并行

- 大模型参数量可能放不进单一设备 → 分布式并行；模型并行、流水线并行、张量并行已应用于 LLM 推理。模型并行把权重参数拆分到多个计算设备分布式计算。

### 数据并行（DP）

- 遵循 SPMD（Single Program Multiple Data）：任务切分到多进程/设备，每进程维护相同且完整的模型参数与相同计算任务，处理不同数据（batch data）。并行对象不只是训练数据，还包括梯度、权重参数、优化器状态等。
- 例（两设备）：数据分两份各给设备 1/2，前向反向得梯度，数据同步 + 梯度累积后完成第一个 step。

### 梯度累积

- 同步：多节点算梯度汇总到主节点求和后更新参数。优点：所有节点基于相同梯度更新、一致性与稳定性好、避免梯度不一致。缺点：需额外主节点（通信与计算负载增加）；慢节点成瓶颈拖慢整体。
- 异步：各节点独立算梯度并更新、不等待其他节点。优点：无集中主节点、通信与计算负载小、强节点更新更快。缺点：易梯度不一致（各节点基于不同梯度），收敛性与稳定性难保证。

### 分布式数据并行（DDP）

- 多进程方式，不受 GIL 限制（GIL 是 CPython 让同一时刻只执行一条 Python 字节码的锁，CPU 密集任务无法用多线程利用多核；仅存在于 CPython，Jython/IronPython 没有；可用多进程或 C 扩展规避）。
- 不同步全部参数，只同步梯度的误差 → 减少通信数据量。
- Ring AllReduce：节点排成环形，每轮把自己的局部数据发给环中下一节点，并接收上一节点数据做归约（求和/平均等）后继续传，重复至所有节点拿到全局归约结果。
- 步骤：① 对梯度分桶（训练批次后把梯度分成多个桶便于管理与通信）；② 对梯度逆向排序定优先级；③ 跳过久未更新或更新太慢的梯度（放低优先级桶，省资源加速训练）；④ 集合通讯（broadcast、reduce、scatter 等）聚合梯度；⑤ 梯度更新（SGD、Adam 等）重复至收敛。

### FSDP

- DP/DDP 用 AllReduce 同步梯度；FSDP 用 all-gather + reduce-scatter 更新模型参数、梯度和优化器状态，更深度利用网络通信空载时间，并把不需要的静态内存卸载到 CPU，减少显存压力。

### 模型并行（MP）

从切分角度有两种含义：① 不同层分配在不同设备（层间模型并行）= 流水线并行；② 同一层参数切分到不同设备（层内模型并行）= 张量并行。

### 流水线并行（PP）

- 把模型不同层放到不同 GPU，通过切割 mini-batch 对训练数据流水线处理，提升 GPU 计算/通讯比。
- Naïve Pipeline：同一时刻只有一个设备计算、其余空闲，利用率低。
- Mini-Batch：把朴素流水线的 batch 再切分，减小设备空闲时间，显著提升利用率。
- GPipe（google），论文 [https://arxiv.org/pdf/1811.06965.pdf](https://arxiv.org/pdf/1811.06965.pdf)：
  - partition-stage：把网络划分成单元，分区算法最小化所有单元估计成本的方差，通过同步各分区计算时间最大化管道效率；
  - micro-batch & pipeline：mini-batch 分为若干 micro-batch，计算完一些就传给下节点，最后同步更新参数；
  - 重计算：用梯度累积优化内存效率，丢弃存储、后向需要 activation 时重算。
  - 例：Transformer-L 模型最大 937.9GB，用 128 块 GPU。
  - 劣势：过多流水线刷新和交互增加空闲；m 太小则重计算开销与频繁管道刷新降低硬件效率（故 m 一般较大），于是需缓存 m 份 activation 导致内存增加（即使 checkpointing，activation 也要等对应后向完成才释放）。令 micro-batch 数 m、阶段数 p，空泡比例 = (p-1)/(p-1+m)。
- 1F1B（one forward and one backward，microsoft）：
  - 思路：尽量早做后向以缩短每个 activation 的保存时间。
  - 方法：每个 GPU 交替执行各 micro-batch 的前向/反向，activation 缓存数量只与 stage 数相关，进一步省显存。启动阶段（Startup State）先读入足够多 micro-batch 以保证稳定阶段各设备都有工作；输出 stage 完成第一批前向后立即对同批做反向，再交替后续批的前后向。局限：不能减少 bubble time。
  - Megatron-LM interleaved 1F1B：每 GPU 从负责若干连续层改为若干不连续层（负责层数不变、顺序改变），stage 数变多 → bubble 减少；要求 micro-batch 数为 stage 数整数倍，且通信量增加。bubble 占比从 (n-1)/(n-1+m) 优化为 (n-1)/(n-1+km)，k 为每设备上的 stage 数。
- PipeDream：权重一致性问题——同一 micro-batch 在不同 stage 做同一操作（同前向/同反向）时使用的参数版本不一致。例：mb5 在 worker1 的前向在 mb1 反向后执行，在 worker2 的前向却在 mb1、mb2 反向后执行。方案：
  - Weight stashing（权重隐藏）：维护多个权重版本（每个 active micro-batch 一份）；每 stage 用最新版参数前向并把该份参数保存给同 micro-batch 的反向 → 保证 stage 内前后向参数一致，但不保证跨 stage 对同一 micro-batch 一致。
  - Vertical Sync（垂直同步）：micro-batch 进入 pipeline 时使用输入 stage 最新版参数，版本号伴随该 micro-batch 整个生命周期，各 stage 全程用同一版本 → 实现跨 stage 一致。
- PipeDream-2BW：流水线中只维护两个版本的模型权重（2BW = double-buffered weights）。每个 micro-batch 生成新版本 K，因剩余反向仍依赖旧版本而无法立即替换；只存两版 → 内存占用极大降低。
- PipeDream-flush：在 2BW 基础上添加全局同步的流水线更新刷新操作，仅保留进行中（in-flight）micro-batch 的 activation → 内存占用更低（只维护一版权重），思路类似 GPipe，代价是吞吐部分下降。

### 张量并行（Tensor Parallel）

- 把模型参数分割到多个设备计算，最后通信聚合结果，实现与不拆分数学等价。最常见的是 MatMul 算子并行，可扩展到 Embedding、MLP、Transformer 算子并行。
- tensor-wise parallelism：
  - MLP 切分：第一个线性层按列切分、第二个线性层按行切分。
  - Self-Attention 切分：多头计算天然适合——每头独立计算后 concat，可把每头参数放一块 GPU；线性层按"行切割"，方式与 MLP 基本一致（forward/backward 原理一致）。
  - 输入层 Embedding：positional embedding 的 max_s 不长，每 GPU 拷一份（显存压力不大）；word embedding 按词表拆分，每块 GPU 维护部分词表——查得到的正常返回词向量、查不到的置 0，全部查完后各 GPU 做一次 AllReduce 得最终输入。
  - 输出层 Embedding：输入输出层共用 word embedding；输入输出层在同一 GPU（流水线并行深度 = 1）时无需处理（实践中大部分 Megatron 项目如此），否则须在权重更新前对两块 GPU 的 word embedding 梯度做一次 AllReduce。
  - CrossEntropy 切分：Step1 数据拆分——logits 按 vocab 维度拆分分发，labels 先 one-hot 再 scatter 到各设备；Step2 logits 最大值同步——logits 减全局最大值后求 softmax（AllReduce(Max) 防溢出）；Step3 exp sum（softmax 分母）经 AllReduce 得全局和；Step4 计算 loss——logits 与 one_hot 相乘求和得 label 位置值，AllReduce(Sum) 全局同步，再 log softmax 取负得分布式交叉熵损失。

## 五、量化

- 量化 = 浮点计算转低比特定点计算：降低模型计算强度、参数大小与内存消耗，但常带来精度损失；极低比特（<4bit）、二值网络（1bit）、甚至量化梯度时精度挑战更大。之前相关：[陆淳：QLoRA：4-bit 级别的量化+LoRA 方法，用 3090 在 DB-GPT 上打造基于 33B LLM 的个人知识库](https://zhuanlan.zhihu.com/p/634516004)
- 矩阵乘法中可组合逐行/逐向量量化获得更精确结果：不用整张量最大绝对值归一化，而是找 A 每行与 B 每列的最大绝对值逐行/逐列归一化后相乘得 C，再以 A、B 最大绝对值向量的外积与 C 求哈达玛积反量化回 FP16。
- 量化公式 Q 与反量化公式 R 的符号：R 输入浮点数据；Q 量化后定点数据；Z 零点（决定是否做偏移/对称）；S 缩放因子。S、Z 的求解例（MinMax 线性量化）：Rmax/Rmin = 输入浮点最大/最小值；Qmax/Qmin = 最大/最小定点值（127/255 与 -128/0）。
- 优点：网络对噪声（量化误差）不敏感，控制量化程度则对高级任务精度影响很小；低比特计算更快，INT8 相对 FP32 加速可达 3 倍以上；FP16/INT8/INT4 占空间小，存储与传输开销大幅下降；位数少 → 搬运数据少（节能）、所需乘法器少（减小芯片面积）。
- 缺点：线性量化对数据分布描述不精确；16bits→4bits 比特越低精度损失越大；任务越复杂（分类/检测/识别）损失越大；模型越小损失越大。
- 方法分类：
  - QAT vs PTQ：
    - QAT（Quantization Aware Training，量化感知训练，论文 [CVPR 2018](https://openaccess.thecvf.com/content_cvpr_2018/papers/Jacob_Quantization_and_Training_CVPR_2018_paper.pdf)）：训练中插入伪量化算子模拟量化误差，通过统计输入输出数据范围提升量化后精度，适用高精度要求场景（部分文献称在线量化）。流程：① 数据集上 FP32 训练得 baseline；② baseline 中插入伪量化节点得 QAT 模型并 finetune；③ 伪量化节点模拟推理量化过程并保存 finetune 中得到的量化参数；④ finetune 后用所得参数量化得到 INT8 模型部署。伪量化（fake quant）= quantization+dequantization 结合，把量化误差当训练噪声让模型适应；节点插在需量化位置——weights 输入 conv 之前（weight quantization）、activation 之后（activation quantization），QAT 训练全用 FP32。
    - PTQ（Post Training Quantization，训练后量化）：LLM 训练完再量化参数，只需少量校准数据，适用追求高易用性、缺乏训练资源场景；不需改架构或重训，主要减少存储与计算复杂度；优势简单高效，但可能引入精度损失。部分文献称离线量化（在线/离线之别指起点是否为已量化好的 checkpoint）。
    - 对比：PTQ 不需重训、训练与量化无联系难保精度、少量无标签数据、适用对量化不敏感/体量大开销大的模型；QAT 需重训、量化参数经 finetune 学习精度损失小、需带标签数据、适用量化敏感场景（目标检测、分割、OCR 等）。
  - 对称 vs 非对称：是否以 0 为对称轴，即公式中 z 是否为 0（s 为量化间隔）。
  - 线性 vs 非线性：线性量化间隔相等，浮点与定点数据是简单线性变换关系；卷积/全连接本是线性计算，可直接用量化后数据计算。非线性量化每个间隔不相等，更能捕捉权重分布的密集点。
  - 动态 vs 静态：静态量化权重提前量化，使用前有 calibrate 过程（校准缩放因子），并基于校准中观察的模型行为预先计算激活的比例因子和偏差；动态量化仅把特定算子权重从 FP32/16 映射为 INT4/8/16，bias 和激活在推理时动态量化、缩放因子对不同输入动态计算 → 性能最差。权重 INT16：精度不受影响、模型大小为原始 1/2；INT8：精度受影响、大小 1/4。
- 主流算法：
  - OBD 系列：
    - OBD（Optimal Brain Damage）：用二阶导数信息度量参数显著性（删除参数对模型的影响），剪掉影响小的参数降低复杂度、提高泛化。
    - OBS（Optimal Brain Surgeon）：OBD 只看海森对角线元素；OBS 考虑海森全局信息，获得参数间相互影响；流程：找影响最小的参数置零 → 更新其余参数补偿；无需重训。
    - OBC（OPTIMAL BRAIN COMPRESSION）：OBS 对整个网络剪枝，OBC 对模型分层剪枝或量化；OBD/OBS 需计算全参数海森（或逆）在上亿参数网络不可行 → 假设同行的参数相互相关、不同行互不相关，海森只需在每行内单独计算。
    - ExactOBS：参数更新与代价评估只需与剪枝参数所在行相关的 d_col×d_col 大小的海森矩阵。
  - GPTQ，论文 [https://arxiv.org/pdf/2210.17323.pdf](https://arxiv.org/pdf/2210.17323.pdf)：OBC 的改进（OBQ 理论好但复杂度太高、太慢）；核心是逐一量化各层且希望量化前后该层输出变化尽量小；取消贪心算法、固定位置优化；延迟部分参数更新缓解带宽压力 + 分组量化 + 并行加速；积累量化参数批量更新其他参数，避免频繁访问内存；海森逆不稳定 → 用数值稳定的 Cholesky 分解提前计算所需信息。
  - SmoothQuant，论文 [https://arxiv.org/pdf/2211.10438.pdf](https://arxiv.org/pdf/2211.10438.pdf)：模型越大，单个 token 的值变化范围越大（activation 难量化），weight 范围较小易量化；引入超参减小 activation 变化范围、增大 weight 范围，均衡两者量化难度；变换后的矩阵可 per-token 或 per-tensor 量化。
  - AWQ，论文 [https://browse.arxiv.org/pdf/2306.00978.pdf](https://browse.arxiv.org/pdf/2306.00978.pdf)：在 SmoothQuant 基础上提出，但为 weight-only 量化；依据输入 X 与参数 W 的绝对大小把 s 分成 S_x 与 S_w，用二者加权乘积作 s——W 越大则 s 越小、X 越大则 s 越大。
  - SpQR：参数对模型的重要程度极不均衡，1% 的参数可能主导量化中的性能损失，保护这 1% 即可极大保护模型性能。实现：每层用一小数据集 X 计算单参数 w_ij 量化前后误差 s_ij，取 top 1% 参数保护，用稀疏矩阵单独保存为 FP16；实验发现重要参数常按行/列聚集 → 用更小 group_size（8 或 16）而非 GPTQ 常用 128。
  - LLM.int8，论文 [https://arxiv.org/pdf/2208.07339.pdf](https://arxiv.org/pdf/2208.07339.pdf)：混合精度分解——把含 Emergent Features 的几个维度从矩阵分离做高精度矩阵乘，其余部分量化。
  - ZeroQuant，论文 [https://arxiv.org/pdf/2206.01861.pdf](https://arxiv.org/pdf/2206.01861.pdf)：权重用分组量化、激活用 token 量化；开发高度优化的推理后端，消除量化/反量化运算符的高成本，在现代 GPU 上实现 INT8 Tensor Core 延迟加速；提出 INT4/INT8 混合精度量化的逐层知识蒸馏（LKD，原网络做老师、量化后网络做学生），逐层蒸馏可迭代最少甚至不访问原始训练数据，缓解精度损失。

## 六、采样解码

- LLM 推理是 Incremental decoding：每一步需额外输入上一步新生成的词，只能串行运行（训练中可用 attention mask 并行化）→ 推理效率低。
- 模型每步直接输出词表中每个词的出现概率，解码策略：
  - 确定性解码（适合翻译、摘要等以输入为基础的任务）：贪心 = 直接取概率最大的词；波束（beam）= 每时间步保留最可能的 num_beams 个序列。
  - 随机性解码（适合开放性、创造力任务）：采样 = 按概率采样（可调 softmax 的 Temperature 调整分布）；Top-K = 只从概率最大的 K 个词中采样（缺点：无法根据分布动态调整采样词集）；Top-p = 在累积概率超过 p 的最小词集中采样。
- **Speculative decoding**，论文 [https://arxiv.org/pdf/2211.17192.pdf](https://arxiv.org/pdf/2211.17192.pdf)：= draft model + rejection sampling + parallel verification。两个模型：原始目标模型 + 小得多的近似模型（近似模型串行自回归采样，大模型评估采样结果）；简单 token 交小模型、困难 token 交大模型。小模型可同结构少参数，或干脆用 n-gram 模型；小模型优势：计算量小且更重要的是减少内存访问需求。例：近似模型生成 5 个 token，目标模型以 "[START] japan's bechmark bond" 一次前向验证；最后 "bond" 被拒绝、重采样得 "n"；中间 4 个 token（"japan" "'s" "benchmark"）由小模型贡献；大模型只 forward 9 次生成 37 个 tokens。步骤：记 Mp、Mq 为大/小模型及 prefix → 小模型串行生成 r 个 token（含概率 q）→ 并行调用大模型算采样概率 p → 按 p/q 对每 token 拒绝采样确定接受数 → 接受数 < r 时按 p、q 分布差值的归一化采样 1 个新 token。缺点：r 步串行小模型自回归 + 并行大模型验证只能保证 n≤r（最坏 n=1），提高生成速度但大幅增加计算压力与显存占用；严重依赖小模型分布与大模型一致。注：图中 norm 是归一化、非正态分布。
- **Blockwise Parallel Decoding**，论文 [NeurIPS 2018](https://proceedings.neurips.cc/paper/2018/file/c4127b9194fe8562c64dc0f5bf2c93bc-Paper.pdf)：= multi-draft model + top-1 sampling + parallel verification。出发点：贪心解码 m 步才产 m 个 token，每步生成 1 个 token 却要搬运全部模型参数与激活张量，解码受内存带宽限制。思路：若有 k-1 个辅助模型各自跳跃预测后 2~k 位 token，辅助模型与原模型可独立运行 → 并行生成后 k 个 token。三个阶段：Predict（原模型 + k-1 辅助模型预测 k 个位置）→ Verify（原模型把 k 个位置组 batch + 合适 attention mask，一次得到各位置词表概率，贪心取最大）→ Accept（验证与预测相同则保留；不同则其后预测全错）。优化：Verify 时顺带预测下一个 k token。注意：不必真的构造 k-1 个辅助模型——在 decoder 与最后一个 projection layer 之间插 FFN（输出 (batch, seq, k\*d_model)），decoder 输出与该 k 个 project layer 输入分别残差连接，k 个 project layer 输出即 k 个不同位置 token 的 logits；改造后需训练（训练时因内存限制不能取 k 个交叉熵均值作 loss，而是每 minibatch 随机均匀选其中一个 layer 输出作 loss）；训练可为 frozen、finetuning、distillation 任一种。
- **SpecInfer**，论文 [https://arxiv.org/pdf/2305.09781.pdf](https://arxiv.org/pdf/2305.09781.pdf)：= Speculative decoding + token tree verification（top-k 采样 + parallel verification）。用一批 small speculative models（SSMs，可为原 LLM 的蒸馏/量化/剪枝版本，甚至是 LLMA 等可检索知识库或用户自定义函数；参数规模通常小 2-3 个数量级）并行预测多个候选。collective boost-tuning：基于 adaptive boosting 思想微调 SSM 池使聚合预测与 LLM 对齐、降低 Verification 成本——一次将一个 SSM 微调到最充分，再从训练样本中去除该 SSM 与 LLM 输出完全一致的样本，用剩余样本最大化微调下一个，重复 → 得到一组互异、聚合输出与 LLM 高度重叠的 SSM。Token Tree Verification：把 SSM 的多候选 merge 成 token tree，用原 LLM 并行验证，显著降低端到端延迟与计算量且保持质量。全流程：输入序列 S → 生成候选 token 树 N → LLM 为每个节点 u∈N 生成 token O(u)（TreeParallelDecode 在一个 decoding step 同时生成全部 O）→ Verify 依 O 检查 N 生成已验证序列 V → 将 V 追加到 S。
- **Medusa**，论文 [https://arxiv.org/pdf/2401.10774.pdf](https://arxiv.org/pdf/2401.10774.pdf)：= multi-decoding head + tree attention + typical acceptance(threshold)。在模型最后一层改成多个可训练 Medusa Heads，各头预测 n+1、n+2、n+3… 位 token；每头留 topK，在其中选最优组合——若每头只留 top1 贪心拼接，准确率约 60%（n+2、n+3 位 token 只有前 n 个 token 信息），故用 top5 笛卡尔积寻优。把每头 top-k 词作节点、每头作树一层，每条到叶子的路径即一组待验证预测；需新设计 attention mask（只限制对某 token 前面 token 的注意力）并给 position embedding 设置正确位置索引。typical acceptance：为摆脱 greedy 解码限制、支持 top-k/top-p 采样——实际中常调温度控制创造力，使 draft 与 target 分布不一致、top-p 会拒绝 draft 致并行解码很短；从 truncation sampling 汲取灵感：根据原模型预测概率设阈值（hard threshold 与 entropy-dependent threshold 取最小值），候选超过阈值即接受；第一个 token 总用 greedy 接受，保证每步至少生成 1 token。特点：冻结大模型只训练解码头，不需重训整个模型。
- **LLMA**（LLM Accelerator，微软，= reuse + parallel verification），本质 "Inference with Reference"。三类动机场景：① retrieval-augmented generation（New Bing 等先返回检索信息再由 LLM 总结，输出常含大量检索文本片段）；② cache-assisted generation（大规模部署中历史输入输出被缓存，相似输入对应相似输出）；③ multi-turn conversations（ChatGPT 场景多轮输出只有少量变化、重复度高）。核心操作：1) 每个解码 step 取当前已生成内容的部分后缀（k 个 token）与参考文本匹配，命中则把参考文本的部分后续片段（k 个 token）拷贝到当前输出末尾；2) 并行调用目标模型检验新增 token 合法性；3) 保留所有合法 token 直至第一个不合法。每次并行调用至少产生 1 个、至多 k+1 个新 token。
- **Lookahead Decoding**，blog [https://lmsys.org/blog/2023-11-21-lookahead-decoding/](https://lmsys.org/blog/2023-11-21-lookahead-decoding/)：= n-gram + Jacobi iteration + parallel verification。
  - Jacobi decoding：随机指定 m 个初始解 y，按自回归方程与初始解迭代更新至收敛；m 步内可得 m 个变量；每步需 >1 个 token 的前向（GPU 并行下通常不变慢），但精确定位正确 token 常出错。
  - 核心观察：Jacobi 每位置的新 token 依据之前迭代的历史值解码，产生一组历史 token 轨迹 → 构成很多 n-grams（回溯 3 个迭代轮次即每位置 3-grams）。Lookahead 在迭代中缓存这些 n-grams，执行 Jacobi decoding 同时并行验证缓存 n-grams；接受一个 n-gram 即一次推进 N 个 token。
  - 方法：每步分两个并行分支——lookahead 分支维护固定大小 2 维窗口（窗口大小 W = 前向生成的 token 数，n-gram 大小 N = 回溯的迭代数；N=2 时退化为 Jacobi decoding），依 Jacobi 轨迹生成 n-gram；verification 分支用字符串匹配识别"首 token 与最后输入 token 匹配"的 n-gram，加入当前输入后 LLM 前向验证；候选 n-gram 数量上限 = W 以控制计算成本。两分支可同一步进行：特殊 attention mask 规则——① lookahead 分支的 token 看不到 verification 分支的 token，反之亦然；② 每 token 只能看自己及之前（causal）。图例：蓝色 0 为当前时刻 token，橘/绿/红为 t-3/t-2/t-1 时刻 Jacobi 迭代生成，数字为相对当前 token 的位置；每时刻利用前 N-1 步轨迹执行 Jacobi 迭代生成 window size=5 个位置的 token，得到多组同位置 n-gram（如蓝0-绿1-红2）。

## 七、其他技术

### 1、MoE

- 设问：激活参数 7B、总参数 34B 的模型，能否做到性能似 34B、吞吐优于 34B、延迟类似 7B？例：具有 7B 密集部分的 50B Mistral MoE 与 34B 的 Yi、67B 的 DeepSeek 性能相似。
- MoEfication：把已训练的稠密模型分解为 MoE 模型，使其和小模型一样高效、像大模型一样强悍。MoE 层把大模型拆成多个小模型（专家 expert），每轮迭代按样本激活部分专家用于计算 → 节省计算资源；引入可训练且保证稀疏性的门（gate）机制保证计算能力优化。

### 2、早退

- 对简单 token 不需计算全部 transformer 层，只计算其中一些层即可。

## 参考文献

- [紫气东来 - 知乎](https://www.zhihu.com/people/zi-qi-dong-lai-1/posts)
- [https://space.bilibili.com/517221395/](https://space.bilibili.com/517221395/)
- [小冬瓜AIGC：【手撕LLM-KVCache】显存刺客的前世今生--文末含代码](https://zhuanlan.zhihu.com/p/667763542)
