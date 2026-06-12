# arXiv Daily Digest

每天自动从 arXiv 抓取最新论文，按关键词打分排序，选出最相关的 top-15 发送到邮箱。

**关注方向**：LLM 推理 / GPU 数据中心 / RDMA 网络 / 光交换 (OCS) / 调度与资源分配

## 快速开始

### 1. 装依赖

```bash
pip install feedparser
```

### 2. 配邮箱

编辑 `Arxiv_filter.py` 顶部的 `EMAIL_CONFIG`，填好 `sender` 和 `recipient`。密码**不要写在代码里**，二选一：

```bash
# 方法 A：环境变量（推荐）
export ARXIV_DIGEST_EMAIL_PASSWORD="你的Gmail应用专用密码"

# 方法 B：本地文件（已在 .gitignore）
echo "你的Gmail应用专用密码" > .email_password
```

> Gmail 应用专用密码在这里获取：https://myaccount.google.com/apppasswords（需先开两步验证）

### 3. 手动测试

```bash
python3 Arxiv_filter.py --send
```

正常会输出类似：

```
Main filter matched: 87 → top 15
OCS spotlight matched: 23 → top 10
[email] 已发送到 ...
```

生成的 `daily_digest.md` 也会保存在当前目录。

### 4. 设为每天自动跑

**macOS：**

```bash
./setup.zsh
```

之后每天上午 9:00 自动执行。休眠期间错过的任务会在唤醒后补跑。

```bash
# 管理命令
launchctl list | grep arxiv            # 查看状态
launchctl start com.arxiv.daily-digest # 手动触发
tail launchd_stdout.log                # 查看日志
```

**Windows（PowerShell，以管理员身份运行）：**

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\setup.ps1
```

在 Task Scheduler 中创建每天 9:00 的任务，错过补执行。

```powershell
# 管理命令
schtasks /query /tn "arXiv Daily Digest" /v   # 查看详情
schtasks /run /tn "arXiv Daily Digest"         # 手动触发
```

## 调参

编辑 `Arxiv_filter.py` 里的三个参数：

| 参数 | 默认值 | 作用 |
|---|---|---|
| `MIN_SCORE` | 5 | 初筛门槛，越低越多 |
| `MAX_PAPERS` | 15 | 主 digest 最多显示几篇 |
| `MAX_OCS_PAPERS` | 10 | OCS spotlight 最多显示几篇 |

关键词权重在 `KEYWORDS` 和 `OCS_KEYWORDS` 字典里，按需增删。

## 输出说明

`daily_digest.md` 分两个板块：

- **Main Digest** — LLM 推理、GPU、网络、调度相关
- **OCS & Optical Networking Spotlight** — 光交换、光互连、共封装光学

每篇论文附带：分数、命中关键词、arXiv 链接、摘要片段。
