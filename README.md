# 面试复盘助手

上传面试录音（mp3 / m4a / wav，30–90 分钟中文对话），自动转写、区分面试官和候选人，
调用大模型生成结构化复盘报告：**每道题答得怎么样、更好的答法是什么、可能被追问什么**。

本地自用的网页应用，全部数据留在自己机器上（音频会临时上传到你自己账号的 OSS）。

## 它是怎么工作的

```
上传音频
  → ffmpeg 归一化 16kHz/单声道
  → 阿里云百炼 paraformer-v2 转写（带说话人分离）
  → LLM 判定谁是面试官、谁是候选人
  → LLM 切分出「问题-回答」对
  → LLM 逐题评分 + 改进建议 + 知识点补充 + 追问预测
  → LLM 汇总成整体报告
```

**为什么转写和复盘要分两步**：DeepSeek 官方 API 没有可用的音频输入通道
（Responses API 的内容块只有 `input_text` / `input_image` / `reasoning_text`，
没有 `input_audio`），所以音频必须先转成文本。这个结论是实测过的，见 `docs/probes.md`。

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python 3.13 + FastAPI | |
| 前端 | React 19 + TypeScript + Vite + Tailwind 4 | |
| 转写 | 阿里云百炼 `paraformer-v2` | 0.288 元/小时，每月赠 10 小时，原生支持说话人分离 |
| 音频托管 | 阿里云 OSS | 百炼的文件转写只接受公网 URL，必须先传 OSS |
| 大模型 | DeepSeek `deepseek-flash` | 1M 上下文，支持 JSON 输出 |
| 存储 | SQLite + SQLModel | 单机自用，一个文件、零配置 |

**成本**：一场 90 分钟的面试约 **¥1**（转写 ¥0.43 + 大模型 ¥0.4–1.0）。

## 快速开始

### 1. 配置密钥

复制 `.env.example` 为 `.env`，填入四项（其余已有默认值）：

```dotenv
DEEPSEEK_API_KEY=       # https://platform.deepseek.com/
DASHSCOPE_API_KEY=      # https://bailian.console.aliyun.com/
OSS_BUCKET=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
```

> OSS 建议用 RAM 子账号，只授予该 bucket 的读写权限，不要用主账号 AK。

### 2. 装后端依赖

```bash
python -m venv backend/.venv && backend/.venv/Scripts/python -m pip install -r backend/requirements.txt
```

ffmpeg 不用单独装 —— 依赖里的 `imageio-ffmpeg` 自带静态二进制，免去 winget 和管理员权限。

### 3. 自检

```bash
backend/.venv/Scripts/python scripts/check_env.py
```

### 4. 启动

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --workers 1
```

```bash
cd frontend && npm run dev
```

打开 http://localhost:5173 。接口文档在 http://127.0.0.1:8000/docs 。

> **后端必须 `--workers 1`**。流水线的任务队列和 SSE 进度推送都是进程内状态，
> 多 worker 会出现「任务在 A 进程跑、前端连到 B 进程订阅进度」而永远收不到消息。

## 目录结构

```
backend/app/
  core/       配置、日志（带密钥脱敏）、异常体系
  db/         引擎与全部表定义
  api/        路由
  services/   audio(ffmpeg) / storage_oss / asr_bailian / llm_deepseek / hashing
  pipeline/   状态机、进度总线、执行器与各阶段
  prompts/    版本化的 prompt 链
scripts/      check_env / probe_deepseek_audio / smoke_pipeline
frontend/src/ React 前端
```

## 开发进度

- [x] **Phase 0** 项目骨架、配置、数据库、日志脱敏、环境自检、DeepSeek 探针
- [x] **Phase 1** 打通到转写：ffmpeg → OSS → 百炼，已用真实面试录音验证
- [x] **Phase 1.5** 转写纠错：LLM 修正 ASR 的技术名词识别错误
- [x] **Phase 2** 端到端报告：角色判定 → 问答切分 → 逐题评分 → 整体汇总
- [x] **Phase 3** Web 化：上传 / 进度 / 报告页面
- [ ] **Phase 4** SSE 实时进度、成本面板、说话人手工改判
- [ ] **Phase 5** 报告导出 Markdown、历史对比

命令行查看报告（前端做出来之前的入口）：

```bash
python scripts/show_report.py --title 杭州      # 某场面试的完整报告
python scripts/show_report.py --detail 2        # 某道题的完整分析
python scripts/show_report.py --list            # 所有面试及总分
python scripts/show_report.py --cost            # 各阶段的大模型成本
```

## 几个容易踩的坑

- **不要用 `qwen3-asr-flash-filetrans`**。它名字最像最新款，但**不支持说话人分离**。
  要用 `paraformer-v2`。
- **云 ASR 返回的说话人编号是匿名的**（`speaker_0`/`speaker_1`），不告诉你谁是面试官，
  而且不跨录音稳定。角色判定是流水线里独立的一步，不是 ASR 能给的。
- **`deepseek-flash` 思考模式默认开启，开启时不能传 `temperature` / `top_p`**。
- **幂等是成本红线**：百炼按音频时长计费，转写任务的 `task_id` 一落库就绝不重新提交，
  重跑只重新轮询。同一个文件重复上传靠 sha256 指纹直接复用已有转写。

## 隐私说明

- 面试录音属于隐私，`.gitignore` 已排除所有音频格式，不要提交进版本库。
- `docs/probes.md`（实测记录）引用了真实面试的转写摘录，同样只存本地、不入库。
- 密钥一律走 `.env`（已忽略），代码里不出现任何明文凭据。
- `.githooks/pre-commit` 会在提交前扫描疑似密钥并拦截。启用方式见 `docs/design.md`。
