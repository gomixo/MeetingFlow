# MeetingFlow

MeetingFlow 是一套面向 Windows 和 Apple Silicon macOS 的本地会议音频处理流水线：OBS 负责录音，MeetingFlow 负责音频检查、中文转写、发言人区分和 Markdown/SRT 输出。音频和转写内容始终保留在本机。

## 当前状态

当前实现是已完成验收的 V2 Wayfinder 链路。已实现：

- 中文终端菜单，可处理 Inbox 最新文件、从 Inbox 最近 6 个文件中选择，或拖入/粘贴单个文件；
- FFmpeg/ffprobe 媒体检查，包括格式、时长、采样率、声道、码率、静音与削波提示；
- 本地 SenseVoiceSmall + FSMN-VAD + CAM++ 单次语音分析，产出段级文字和主要发言轮次；
- 终端内选择历史任务和发言人，并输入中文姓名重新渲染；
- 默认生成带发言人的 Markdown，可切换为 SRT 或两者；
- SHA-256 任务去重、SQLite 阶段状态、失败重试、原子写入和 JSONL 日志；
- Windows FFmpeg/PyTorch DLL 路径注册、CUDA 推理和 macOS CPU 推理；
- 完全离线运行，不依赖 Hugging Face、ModelScope 或 Token；
- 旧版平铺任务兼容，原始录音始终只读。

## V2 Wayfinder 链路

V1 流水线已用 Wayfinder 选型替换为 `SenseVoiceSmall + FSMN-VAD + CAM++` 单次本地分析，代号 V2。Wayfinder 盲评比较确认 SenseVoice 在日常与困难两场胜出、Turbo 在多人压力场胜出，当前 large-v3 基线因连续重复失控淘汰。

**自动检查（程序判定）**：

- `analyze()` 全空结果视为失败（保证任务不静默产出空转录）。
- 持久化分析产物损坏（缺字段、字段值不合法、路径穿越、多余文件）→ 加载阶段拒绝并提示 `--from transcribe` 重新生成。
- 重复风险标记：`review_flags: ["repetition"]` 写入产物与 Markdown 头部 "⚠ 存在待人工核听标记"。

**人工核听（必须人耳判定）**：

- 整段遗漏（会议中间清晰发言被漏掉）。
- 真实重复 vs 模型循环（参会人口吃/强调/列表 vs ASR 失控）。
- 幻觉内容（无中生有的整段、错位归属）。
- 主要发言人轮次归属的合理性。

**发布门（合并到 main 前必须完成）**：

- 固定三场景盲评比对（含人工核听四项）— 见 `docs/V2-project-review-wayfinder-acceptance.md`。
- 零网络探针（`scripts/verify-offline.py`）。
- 最终运行体积精简环境实测 ≤ 约 6.4 GB。
- 一票否决人工核听四项签字。

自动化测试和前两项是合并前的必做门；体积与人工核听是合并前必完成项，不是合并后事项。

明确不包含后台目录监听、会议总结、GUI、实时字幕、Web 服务或音频上传。

## 安装

复制 `config/meetingflow.toml.example` 为 `config/meetingflow.toml`，按本机目录调整后运行：

```powershell
uv sync
```

## 模型准备

转写与说话人识别使用三个本地版本化模型目录（`[models]` 段），各含 `manifest.json` 全目录 SHA-256 清单，与 `analyze.py` 中 `FROZEN_MANIFEST_HASHES` 信任锚点严格对齐。首次或在新机器上一次性准备：

```powershell
uv run scripts/prepare-models.py
# 或自定义根目录：
uv run scripts/prepare-models.py --root D:/MeetingFlow/Models
```

该脚本按固定 commit 从 ModelScope 下载 `iic/SenseVoiceSmall`、`iic/speech_fsmn_vad_zh-cn-16k-common-pytorch`、`iic/speech_campplus_sv_zh-cn_16k-common`。它以远程 commit 的文件清单为范围，逐文件核对字节数和 SHA-256，再按大小写无关的路径顺序生成跨平台一致的 manifest。下载器的本地元数据不会进入模型目录。目录已存在时跳过下载，最后由 `analyze.verify_models` 执行只读校验。

日常运行完全离线：启动前校验目录与哈希，缺失或不匹配直接失败，不会回退在线下载，也无需 `HF_TOKEN`。

## 终端菜单

双击 `run.bat`，或运行：

```powershell
uv run meetingflow --config config/meetingflow.toml
```

菜单提供五项操作：

1. 处理 Inbox 中修改时间最新的音频或视频；
2. 从 Inbox 中按修改时间倒序显示最近 6 个音视频文件，使用上下键或编号选择；
3. 处理拖入终端或粘贴路径的文件；
4. 选择已完成任务和发言人，连续修改多个中文姓名；发言人列表按 `0` 返回任务列表，任务列表按 `0` 返回主菜单；
5. 设置输出 Markdown、SRT 或两者，默认只输出 Markdown。

也可以把一个文件直接拖到 `run.bat`，或继续使用适合脚本调用的命令：

```powershell
uv run meetingflow --config config/meetingflow.toml process "D:\Meetings\Inbox\meeting.mp4"
uv run meetingflow --config config/meetingflow.toml retry <job-id> --from diarize
uv run meetingflow --config config/meetingflow.toml render <job-id>
```

相同 SHA-256 的成功任务会复用已有模型结果；`process --force` 才会重新执行。

## Agent JSON 接口

本机 Agent 通过 stdin 提交单个 JSON 请求。stdout 只返回一个 JSON，不混入处理进度：

```powershell
'{"schema_version":1,"operation":"submit","source":"D:\\Meetings\\Inbox\\meeting.mp4"}' |
  uv run meetingflow --config config/meetingflow.toml agent
```

`submit` 返回完整 SHA-256 `job_id`。Agent 使用同一入口轮询：

```json
{"schema_version":1,"operation":"status","job_id":"<64位SHA-256>"}
```

任务失败后可发送 `retry`，不接受内部阶段参数。查询成功即返回退出码 0，包括任务状态为 `failed`；非法请求或配置错误返回 2。成功状态的 `result_path` 指向公开的 `result.json`。请求、响应和结果 Schema 位于 `schemas/`。

## 输出文件

`Output/<任务>/` 只放最终成品：

- `result.json`：供 Agent 使用的版本化结构化转录结果；
- `speakers.md`：带时间戳和发言人姓名的对话稿，适合交给 ChatGPT 总结；
- `speakers.srt`：可选的带发言人字幕。

为了支持改名、重渲染、去重和失败恢复，以下内部文件保存在 `Work/jobs/<sha256>/`：

- `source.json`：源文件路径、哈希和媒体信息；
- `analysis.sensevoice.json`：SenseVoice + FSMN-VAD + CAM++ 的原生分析产物，是后续派生与重渲染的唯一来源；
- `transcript.raw.json`：从原生分析派生的段级转写，相邻同说话人合并为主要发言轮次；
- `speakers.json`：说话人时间段；
- `speaker-map.toml`：说话人标签与姓名映射；
- `run.jsonl`：阶段、参数、耗时和错误日志。

任务成功后会删除 `audio-16k-mono.wav`，需要重跑模型时从只读原始录音重新标准化。旧版本平铺任务中的 `transcript.aligned.json`（词级说话人分配）仍可读取，但新任务不再生成。

输出格式偏好保存在 `Work/preferences.json`。旧版本平铺在 Output 中的任务无需迁移，仍可改名和重新渲染。

## 数据原则

- 原始录音只读，不覆盖、不删除。
- 模型任务串行执行；Windows 上的进程锁避免 RTX 4060 8GB 显存被同时占用。
- 阶段产物按参数指纹复用：模型清单哈希、冻结参数或有效设备变化时重跑受影响阶段及下游。
- 不提交真实会议录音、访问令牌、模型缓存、本机配置或运行数据。
- Agent 开发约束见 [AGENTS.md](AGENTS.md)，当前设计见 [docs/V2-tech-design-wayfinder-pipeline.md](docs/V2-tech-design-wayfinder-pipeline.md)，验收见 [docs/V2-project-review-wayfinder-acceptance.md](docs/V2-project-review-wayfinder-acceptance.md)，全部文档见 [docs/README.md](docs/README.md)。

## macOS 支持

项目可在 Apple Silicon Mac 上本机运行（实测 M5 / 16 GB）：

- torch/torchaudio 仅在 Windows 上使用 cu128 索引（`pyproject.toml` 按 `platform_system` 标记门控），macOS 使用 PyPI 默认源；
- Apple Silicon macOS 无 CUDA 时 `analyze()` 使用 CPU 推理；Windows 仍要求 NVIDIA CUDA。有效设备会写入转写指纹和 `run.jsonl`，CPU/CUDA 产物不会互相复用；
- 终端菜单方向键兼容 macOS 终端（`_read_key` 使用 termios 原始模式）；
- FFmpeg 通过 Homebrew 安装（`brew install ffmpeg`），`config/meetingflow.toml` 中配置本机路径即可。

实测：80 秒双发言人合成音频全流程约 15 秒（含模型加载）；`uv run pytest` 全部通过。CPU 推理尚未完成代表性测试集的固定三场景回归和人工核听，因此 macOS 支持在补齐该证据前不应合并到 `main`。Windows + CUDA 的已验收结论不变。

## 反馈问题

如果发现缺陷或安装问题，请在 [GitHub Issues](https://github.com/gomixo/MeetingFlow/issues) 中提交。请勿附加真实会议音频、完整转写内容、访问令牌或本机配置。

## 许可证

本项目采用 [MIT License](LICENSE)。
