# 探针实测记录

这个文件记录那些「查文档查不出确定答案、只能实测」的结论。
每次跑完 `scripts/probe_deepseek_audio.py` 后把结果抄到这里，注明日期和模型名。

## 为什么需要探针

调研阶段关于「`deepseek-flash` 收不收音频输入」得到了**互相矛盾**的结论：

- 官方 Responses API 的内容块列表里只有 `input_text` / `input_image` / `reasoning_text`，
  **没有 `input_audio`**；第三方跨厂商对比（2026-08）也写明托管 API 不支持 audio。
- 但 2026 年 9 月确实存在过一个原生多模态内测版本，官方社区称可统一处理
  文本 / 图像 / 语音输入，模型 ID 为 `deepseek-v4.1-flash-expires-on-0910`
  —— 带过期后缀，已下线。
- 第三方网关（如 NagaAI）确实提供 `input_audio` 参数并能挂 `deepseek-v4.1-flash`，
  但文档注明模型读不了该 part 会返回 400 `unsupported_modality`。

结论既然有分歧，就不该靠猜来定架构。探针脚本用真实 API 跑一次，把这条钉死。

**注意**：无论探针结果如何，方案里「转写与复盘分两步」的架构都不变，因为还有两条更硬的约束：
90 分钟的录音远超单请求体积上限（必须切片），且大模型不做说话人分离
（而「区分面试官和我」正是本项目的核心需求）。

---

## 探针记录

### 2026-10-04 · `deepseek-flash` · 结论：**不支持音频**

运行命令：

```
backend/.venv/Scripts/python scripts/probe_deepseek_audio.py
```

| 探测项 | 结论 | 备注 |
|---|---|---|
| 模型列表 | 可用 | `deepseek-flash` 存在，账号下可见的模型只有它和 `deepseek-v4-pro` |
| JSON 输出模式 | 可用 | `response_format={"type":"json_object"}` 稳定返回可解析 JSON |
| 思考模式 + temperature | 不报错 | 服务端接受 `enable_thinking=true` 与 `temperature` 并存，不返回错误 |
| **音频输入** | **不支持** | 三种写法全部被拒 |

**关键证据** —— 服务端返回的错误信息直接列出了它接受的内容块类型：

```
HTTP 422 unknown variant `input_audio`, expected one of `text`, `image_url`, `file`
```

三种尝试（`input_audio`、`audio_url`、裸 `audio`）都是同一个 422，错误里那句
`expected one of text, image_url, file` 就是权威答案：**这台服务端的内容块只有
文本、图片、文件三种，没有音频**。注意 `file` 是文件类型（PDF 之类），不是音频。

这一条比调研阶段所有二手资料都可靠 —— 之前两个来源给的结论互相矛盾
（一方说支持音频，一方说纯文本），现在不用再猜了。

**对方案的影响**：无。架构本来就是「音频 → ASR → 文本 → DeepSeek」两步走，
理由也不只是这一条（90 分钟录音超单请求体积上限、大模型不做说话人分离）。
转录与复盘分离的设计无需调整。

**一个意外收获**：`enable_thinking=true` 与 `temperature` 同时传**不会报错**。
实现里可以少一层条件分支，不过按官方文档该参数在思考模式下会被忽略，
所以仍然不该指望用 `temperature` 控制思考模式的输出随机性。
