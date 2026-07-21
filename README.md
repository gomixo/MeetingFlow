# MeetingFlow

MeetingFlow 是一套面向 Windows 的本地会议音频处理流水线：OBS 负责录音，MeetingFlow 负责音频检查、中文转写、发言人区分和 Markdown/SRT 输出。音频和转写内容始终保留在本机。

## V1 当前状态

V1 的功能开发已完成，可以告一段落并进入日常使用与验收阶段。当前已实现：

- 中文终端菜单，可处理 Inbox 最新文件或拖入/粘贴的单个文件；
- FFmpeg/ffprobe 媒体检查，包括格式、时长、采样率、声道、码率、静音与削波提示；
- WhisperX 本地中文及中英混合转写和时间对齐；
- pyannote 本地发言人区分，可配置说话人数范围；
- 终端内选择历史任务和发言人，并输入中文姓名重新渲染；
- 默认生成带发言人的 Markdown，可切换为 SRT 或两者；
- SHA-256 任务去重、SQLite 阶段状态、失败重试、原子写入和 JSONL 日志；
- Windows FFmpeg/PyTorch DLL 路径注册和单 GPU 串行模型执行；
- 旧版平铺任务兼容，原始录音始终只读。

自动化测试覆盖输出布局、格式偏好、中文姓名、旧任务兼容、最新文件选择、媒体探测和 FFmpeg 解码；本机也已有多份真实录音完成 `probe → transcribe → diarize` 全流程。发布前只需用最新终端菜单再做一次代表性会议人工验收，检查转写质量、发言人区分和最终 Markdown；这属于效果验收，不是缺失的 V1 功能。

V1 明确不包含后台目录监听、会议总结、GUI、实时字幕、Web 服务或音频上传。

## 安装

复制 `config/meetingflow.toml.example` 为 `config/meetingflow.toml`，按本机目录调整后运行：

```powershell
uv sync
```

首次进行说话人识别前，须在 Hugging Face 接受 [`speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1) 的使用条款，并设置用户环境变量 `HF_TOKEN`，不要把令牌写入配置或仓库。

## 终端菜单

双击 `run.bat`，或运行：

```powershell
uv run meetingflow --config config/meetingflow.toml
```

菜单提供四项操作：

1. 处理 Inbox 中修改时间最新的音频或视频；
2. 处理拖入终端或粘贴路径的文件；
3. 选择已完成任务和发言人，连续修改多个中文姓名；发言人列表按 `0` 返回任务列表，任务列表按 `0` 返回主菜单；
4. 设置输出 Markdown、SRT 或两者，默认只输出 Markdown。

也可以把一个文件直接拖到 `run.bat`，或继续使用适合脚本调用的命令：

```powershell
uv run meetingflow --config config/meetingflow.toml process "D:\Meetings\Inbox\meeting.mp4"
uv run meetingflow --config config/meetingflow.toml retry <job-id> --from diarize
uv run meetingflow --config config/meetingflow.toml render <job-id>
```

相同 SHA-256 的成功任务会复用已有模型结果；`process --force` 才会重新执行。

## 输出文件

`Output/<任务>/` 只放最终成品：

- `speakers.md`：带时间戳和发言人姓名的对话稿，适合交给 ChatGPT 总结；
- `speakers.srt`：可选的带发言人字幕。

为了支持改名、重渲染、去重和失败恢复，以下内部文件保存在 `Work/jobs/<sha256>/`：

- `source.json`：源文件路径、哈希和媒体信息；
- `transcript.raw.json`：模型原始转写；
- `speakers.json`：说话人时间段；
- `speaker-map.toml`：说话人标签与姓名映射；
- `run.jsonl`：阶段、参数、耗时和错误日志。

输出格式偏好保存在 `Work/preferences.json`。旧版本平铺在 Output 中的任务无需迁移，仍可改名和重新渲染。

## 数据原则

- 原始录音只读，不覆盖、不删除。
- 模型任务串行执行，避免 RTX 4060 8GB 显存被同时占用。
- 不提交真实会议录音、访问令牌、模型缓存、本机配置或运行数据。
- Agent 开发约束见 [AGENTS.md](AGENTS.md)，详细设计见 [docs/V1-详细开发方案.md](docs/V1-详细开发方案.md)。
