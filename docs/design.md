# 设计说明

记录这个项目里**不那么显然**的设计决策和它们的理由。代码本身能说明「怎么做的」，
这个文件说明「为什么这么做」。

## 架构

```
浏览器 (React + Vite)
   │  REST + SSE
   ▼
FastAPI（单进程，--workers 1）
   ├─ api/         路由层
   ├─ pipeline/    进程内 asyncio 作业执行器 + 状态机 + 断点续跑
   ├─ services/    audio(ffmpeg) / storage_oss / asr_bailian / llm_deepseek
   ├─ prompts/     版本化 prompt 链
   └─ db/          SQLModel + SQLite (WAL)
```

## 流水线的八个阶段

| 阶段 | 做什么 | 失败重试策略 |
|---|---|---|
| `s0_preprocess` | ffmpeg 归一化 16kHz/单声道，探测时长 | 立即重试 ≤3 次 |
| `s1_upload_oss` | 上传 OSS，生成签名 URL | 重试 ≤3 次，签名可重新生成 |
| `s2_transcribe` | 百炼 `paraformer-v2` 提交 + 轮询 | **提交成功即落库 task_id，之后只重轮询，绝不重新提交** |
| `s3_transcript_repair` | LLM 纠正 ASR 的技术名词识别错误 | 分批处理，**单批失败不影响其余批次** |
| `s4_role_mapping` | LLM 判定谁是面试官 / 候选人 | 退避重试 ≤3，JSON 失败触发一次修复调用 |
| `s5_qa_segmentation` | LLM 切分「问题-回答」对 | 同上 |
| `s6_qa_scoring` | LLM 逐题评分（分批 5–8 题） | **批级幂等**，只重跑失败的批次 |
| `s7_summary` | LLM 生成整体报告 | 同 s4 |

> `s3_transcript_repair` 是实测之后补上的。ASR 对中英混合的技术名词识别很差
> （真实录音里 `SQL → circle`、`LangGraph → long graph`、`Agent → A` 反复出现），
> 先纠错能让后面每一步都建立在更可靠的文本上。详见 `docs/probes.md`。

## 关键决策与理由

### 为什么转写和复盘是两步，不能合并

调研发现「DeepSeek 是否支持音频输入」存在互相矛盾的资料（详见 `docs/probes.md`）。
但即使它支持，架构也不会变，因为有两条更硬的约束：

1. **90 分钟的录音远超单请求体积上限**（第三方网关通常 20–32 MiB/请求），切片逃不掉。
2. **大模型不做说话人分离**，而「区分面试官和我」是本项目的核心需求。

所以音频永远不进 DeepSeek，只把转写后的文本喂进去。

### 为什么不需要 RAG

90 分钟面试转写后约 2–4 万 token，而 `deepseek-flash` 有 1M 上下文，全文直接喂即可。
引入向量检索只会增加复杂度和不确定性，没有任何收益。

### 为什么不用 Celery，也不用 BackgroundTasks

- `BackgroundTasks` 绑定请求生命周期、没有持久化、应用重启即丢，无法满足「断点续跑」。
- Celery / RQ 需要额外的 broker，单机自用属于过度设计。

采用「进程内 asyncio 队列 + SQLite 持久化」：`lifespan` 启动时建队列和一个常驻消费协程，
作业状态全写 `pipeline_run` / `pipeline_stage` 表。重启时扫描未完成的 run，
从最后一个 `done` 的阶段继续。

### 为什么后端必须 --workers 1

任务队列和 SSE 进度总线（`pipeline/progress.py`）都是**进程内状态**。
多 worker 会出现「任务在 A 进程执行、前端连到 B 进程订阅进度」而永远收不到消息。

将来若真要多进程，只需把 `progress.publish/subscribe` 换成 Redis Pub/Sub，
其余代码不用动 —— 这也是把进度总线单独抽成一个模块的原因。

### 幂等键：既省钱，也是可靠性的基础

每个阶段的 `input_hash = sha256(上游产物 + prompt 版本 + 模型名)`：

- **命中且已 done** → 标记 `skipped`，直接复用 `output_json`，不重新调用付费服务。
- **改了 prompt** → 版本号变化导致 hash 变化 → 该阶段自动重跑。

后者解决的是一个特别难排查的问题：「改了提示词，但结果没变」。
把 prompt 版本纳入 hash，这类问题在结构上就不可能发生。

### 成本红线

百炼按音频时长计费，重复提交就是重复花钱。因此：

- 转写任务的 `task_id` 一经返回立刻落库，重跑时**只重新轮询**。
- 音频文件的 sha256 作为 `AudioAsset.sha256`，同一文件重复上传直接复用已有转写。

### 说话人角色为什么是独立一步

云 ASR 只返回匿名编号（`speaker_0` / `speaker_1`），不告诉你谁是面试官，
而且编号**不跨录音稳定**。所以角色判定必须由 LLM 根据对话内容推断
（谁在提问、谁说「请先自我介绍一下」），结果落在 `speaker_mapping` 表，
并支持人工改判（`manual_override`）。

判定置信度低于 0.6 时标记 `needs_manual_review`，前端高亮提示人工确认。

## 开发约定

### 编码

Windows 控制台默认 GBK，中文会乱码。脚本和日志都已强制切到 UTF-8
（见 `app/core/logging.py` 与 `scripts/*.py` 开头）。

### 密钥管理

- 所有凭据走 `.env`，代码里不出现任何明文。
- `app/core/logging.py` 有一层脱敏过滤器，日志里的密钥和 OSS 签名 URL 会被打码。
- 启用提交前扫描：

```bash
git config core.hooksPath .githooks
```

钩子会拦截 `.env`、数据库文件、音频文件，以及文件内容里形如
`sk-*` / `LTAI*` / `AKID*` / PEM 私钥的字符串。

### 数据与隐私

- 面试录音属于隐私：`.gitignore` 排除了所有音频格式。
- `backend/data/` 含 SQLite 库和临时 wav，同样被忽略。
- 转写完成后即删中间 wav（D 盘空间紧张），只保留 OSS 上的对象。

### 不要做的事

- 不要在本地跑语音识别模型。D 盘只剩约 12 GB，Whisper large-v3 一个模型就 3 GB，
  且本地模型不解决说话人分离的准确性问题。云 ASR 0.288 元/小时，每月还送 10 小时。
