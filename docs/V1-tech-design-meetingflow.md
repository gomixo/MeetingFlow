---
title: MeetingFlow V1 详细开发方案
slug: V1-tech-design-meetingflow
version: V1
doc_type: tech-design
status: superseded
scope: project
audited_commit: null
branch: null
source: human
created: 2026-07-20
last_reviewed: 2026-08-05
supersedes: V0-project-plan-meetingflow
superseded_by: V2-project-review-wayfinder-acceptance
related: [V1-audit-chatgpt-static, V1-diagnostic-transcription-quality]
---

# MeetingFlow V1 详细开发方案

> 历史文档：其中的 WhisperX、pyannote 与词级对齐链路已由 V2 Wayfinder 方案取代。

> 日期：2026-07-20
>
> 状态：已定稿，开发前仍需完成 OBS 测试录音验收

## 1. V1 的目标与边界

V1 要把一份已经录制完成的会议文件可靠地变成可核对、可重跑的转写资料，并通过终端菜单按需处理。

V1 必须交付：

1. 音频完整性、时长、采样率、声道和音量预检；
2. 中文为主、中英混合的本地转写；
3. 分段及单词级时间戳、带发言人的 Markdown 和可选 SRT；
4. 发言人区分及 `Speaker N` 人工姓名映射；
5. 可追溯的任务状态、模型参数、耗时和错误日志；
6. CLI、文件拖拽入口和中文终端菜单；
7. 阶段级重跑，且不重复处理已经成功的同一录音。

V1 不做：会议纪要、文本润色、GUI、实时字幕、自动识别真实姓名、自动推断业务项目、分布式任务队列、录音功能、自动删除源文件。

## 2. 本机环境检查结论

检查日期为 2026-07-20，以下内容来自本机命令和 OBS 配置，不是估算。

| 项目 | 当前状态 | V1 结论 |
|---|---|---|
| 系统 | Windows 11 Home China，Build 26200 | Windows 原生运行 |
| CPU | Intel Core Ultra 5 125H，14 核 18 线程 | 音频预处理足够 |
| 内存 | 24 GB | 单任务串行，避免同时加载多个模型 |
| GPU | RTX 4060 Laptop，8 GB VRAM，40 W | `large-v3` 使用小 batch 和 `int8_float16` 起步 |
| NVIDIA 驱动 | 610.74，驱动报告最高 CUDA 13.3 | 可运行 CUDA 12.8 用户态组件 |
| Python | 系统默认 3.14；已有 uv 管理的 3.12 | 项目固定 Python 3.12，不使用 3.14 |
| uv | 0.11.26 | 用于环境、依赖和锁文件 |
| FFmpeg | 7.1.5 GPL Shared，ffmpeg/ffprobe 已在 PATH | 固定 7.1；TorchCodec 当前不支持 FFmpeg 8 |
| CUDA Toolkit | 12.8.93，`CUDA_PATH` 和 PATH 已配置 | 与 WhisperX/CTranslate2 的 CUDA 12 要求一致 |
| Python GPU 运行时 | PyTorch 2.8.0+cu128、cuDNN 9.10.2、CTranslate2 4.8.1 | CUDA 张量计算通过，RTX 4060 可见 |
| 发言人模型凭据 | 未配置 Hugging Face 环境变量 | diarization 前需配置 HF token；密钥不入库 |
| 磁盘 | C: 剩余约 38 GB；D: 约 148 GB；G: 约 36 GB | 模型、临时音频和录音优先放 D: |

### CUDA 版本选择

不安装当前最新的 CUDA 13.3。WhisperX 当前 Windows 安装说明指定 CUDA Toolkit 12.8，faster-whisper/CTranslate2 当前要求 CUDA 12 和 cuDNN 9。驱动向后兼容，因此选 12.8 是本机最小风险组合。

项目已创建隔离的 Python 3.12 环境，并验证 `torch.cuda.is_available()` 为真、设备为 RTX 4060、cuDNN 为 9.10.2、CTranslate2 能发现一个 CUDA 设备。`faster-whisper` 的 `tiny` 模型已在 CUDA 上完成 1 秒短音频推理，因此系统 CUDA、Python CUDA wheel、cuDNN 和 CTranslate2 链路均已打通。

Windows 的 Python DLL 搜索规则还要求程序在导入 TorchCodec/CTranslate2 前，用 `os.add_dll_directory` 注册 FFmpeg `bin` 和 PyTorch `lib`。这个兼容处理放在唯一的程序入口，不复制到各模块。

参考：

- [WhisperX 官方仓库](https://github.com/m-bain/whisperX)
- [faster-whisper 官方仓库](https://github.com/SYSTRAN/faster-whisper)
- [pyannote.audio 官方仓库](https://github.com/pyannote/pyannote-audio)

## 3. OBS 检查结论与录音整改

### 3.1 当前配置

| 项目 | 当前值 | 判断 |
|---|---|---|
| OBS | 32.1.1，64 位 | 可用 |
| OBS 采样率 | 48 kHz，Stereo | 正确 |
| 录制目录 | `C:\Users\xiaob\Videos` | C 盘空间偏紧，建议迁到 D 盘 |
| 录制格式 | Hybrid MP4，AAC 160 kbps | 可处理，但音频项目无需 1080p60 视频负担 |
| 视频 | 1920×1080，60 FPS，x264 | 纯会议录音不必要，占 CPU 和磁盘 |
| 桌面音频 | `default` | 会随系统默认设备改变，不稳定 |
| 麦克风 | `default` | 会随系统默认设备改变，不稳定 |
| 监听设备 | CABLE Input (VB-Audio Virtual Cable)，48 kHz | 采样率一致 |
| Windows Realtek 输出 | 48 kHz | 一致 |
| Windows 麦克风阵列 | 48 kHz | 一致 |
| VB-Cable 输入/输出 | 48 kHz | 一致 |
| tranScreen 麦克风 | 44.1 kHz | 与主链路不一致 |

最近 OBS 日志证明默认设备曾在运行中切到蓝牙耳机 `Mainoo - Find My`，其麦克风以 16 kHz 初始化；这会触发重采样，也会让录音链路在会议中途改变，是当前最明确的变速、变调和音质不稳定风险。

此外，两个全局音频源当前 `monitoring_type=1`，即“仅监听（输出静音）”。结合监听设备为 VB-Cable，这可能是有意把混音送入虚拟麦克风，也可能导致录制音轨没有声音。现有十份 OBS 日志没有实际开始/停止录制记录，`Videos` 中也没有样本，因此目前不能证明最终文件包含本地和远端两路声音。

Windows 当前的 Console、Multimedia、Communications 三种角色均使用同一组默认设备：

- 输出：`扬声器 (Realtek(R) Audio)`，ID `{0.0.0.00000000}.{f18c6be2-ebad-4cf4-9c75-633852c417c9}`；
- 输入：`麦克风阵列 (适用于数字麦克风的英特尔® 智音技术)`，ID `{0.0.1.00000000}.{0644b179-6382-481d-9f81-b945fa0bb5e2}`。

这组设备当前都是 48 kHz，可以作为测试基线，但 OBS 必须绑定设备本身而不是绑定 `default`。

### 3.2 录音整改顺序

VB-Cable 只服务于过去的 memo.ai 转录链路，V1 不再使用。录音链路已确定为直接设备：会议软件和 OBS 都使用 Realtek 输出与内置麦克风阵列，OBS 只做旁路录音。

| 软件/来源 | 固定设备 | OBS 监听 | 录制音轨 |
|---|---|---|---|
| 会议软件扬声器 | Realtek | 不适用 | 不适用 |
| 会议软件麦克风 | 内置麦克风阵列 | 不适用 | 不适用 |
| OBS 桌面音频 | Realtek | 关闭 | 音轨 1 |
| OBS 麦克风 | 内置麦克风阵列 | 关闭 | 音轨 1 |

VB-Cable 驱动可以保留，但不再选为 OBS 监听设备或会议软件麦克风。当前两个 OBS 音频源都是“仅监听”，必须改为监听关闭并保留音轨 1 输出。

在开发转写代码前完成以下手动设置并录制样本：

1. 在 OBS“设置 → 音频”中把桌面音频固定为 Realtek，把麦克风固定为内置麦克风阵列；不要使用“默认”。
2. 在“高级音频属性”中把桌面音频和麦克风的音频监听都改为“监听关闭”，并确认两者进入音轨 1。
3. 会议软件直接使用 Realtek 和内置麦克风阵列，不选择 VB-Cable。
4. 蓝牙耳机如果只用于听音，禁用其 Hands-Free/免提麦克风作为系统默认输入；不要让录音链路在会议中途切到 16 kHz。
5. 将参与录音的设备统一为 48 kHz。当前 `tranScreen Audio` 是 44.1 kHz，不纳入主链路。
6. 把录制目录改为 `D:\Meetings\Inbox`，避免 C 盘和 G 盘云同步影响写入稳定性。
7. 第一轮仍可保留 Hybrid MP4，先减少变量；将视频降到 1280×720、10 FPS 并改用 NVENC，或在确认 audio-only 工作流后改为 MKA/FLAC。
8. 录制 5—10 分钟测试样本，覆盖本地讲话、远端讲话、双方交替和短暂重叠。
9. 用 ffprobe 对比墙钟时长和文件时长，并人工确认音高、速度、两路声音和静音段。

OBS 配置不由程序自动改写。配置文件包含设备 ID，盲目写入会破坏现有会议路由；先在 OBS UI 中完成并用样本验收。

## 4. 技术方案

### 4.1 运行栈

- Python 3.12 + uv；
- FFmpeg/ffprobe：探测、响度统计；
- WhisperX：复用 faster-whisper 转写、VAD、词级时间对齐；
- Whisper `large-v3`，初始 `compute_type=int8_float16`、`batch_size=4`、语言默认 `zh`；
- pyannote `speaker-diarization-community-1`：本地发言人区分；
- SQLite：任务去重和状态；
- TOML：项目配置；
- Python 标准库终端输入与本地文件选择；

### 4.2 为什么不再拆更多服务

这是一台单用户、单 GPU 的 Windows 电脑。V1 使用一个 Python 包和一个 SQLite 文件即可；不引入 Redis、Celery、消息队列、数据库服务、Web API 或插件系统。GPU 阶段严格串行，转写模型释放显存后再运行 diarization。

### 4.3 目录结构

```text
meeting-flow/
├── pyproject.toml
├── uv.lock
├── run.bat
├── config/
│   └── meetingflow.toml.example
├── src/meetingflow/
│   ├── __main__.py
│   ├── pipeline.py
│   ├── audio.py
│   ├── transcribe.py
│   ├── diarize.py
│   ├── render.py
│   └── jobs.py
└── tests/
    └── test_pipeline.py
```

只在职责确实独立时保留这些模块；实现阶段若文件很短，可合并，目录结构不是硬性配额。

运行数据不放仓库：

```text
D:\Meetings\
├── Inbox\
├── Work\
│   ├── meetingflow.db
│   ├── preferences.json
│   └── jobs\<sha256>\
└── Output\2026-07-20_会议名\
```

### 4.4 单次任务产物

```text
Output/2026-07-20_会议名/
├── speakers.md             # 默认成品，适合交给 ChatGPT
└── speakers.srt            # 可选的带发言人字幕

Work/jobs/<sha256>/
├── source.json             # 原文件路径、SHA-256 与媒体信息
├── transcript.raw.json     # 模型原始段落与时间戳
├── speakers.json           # diarization 时间段
├── speaker-map.toml        # Speaker 标签到姓名的映射
└── run.jsonl               # 阶段、参数、耗时与错误
```

原始录音不复制时，`source.json` 必须记录绝对路径与哈希；如执行归档，则只复制，不删除原文件。

## 5. Pipeline 设计

### 5.1 任务标识与状态

流式计算源文件 SHA-256 作为任务 ID。SQLite 只需要两张表：`jobs` 和 `stages`。

任务状态：`pending → running → succeeded | failed | needs_review`。每个阶段记录开始时间、结束时间、参数和产物路径。相同哈希已经成功时默认跳过；显式 `--force` 才重跑。

### 5.2 阶段

1. **discover**：接收 CLI 路径、拖入路径或手动选择 Inbox 最新文件。
2. **probe**：记录格式、时长、采样率、声道、码率；检测时长异常、静音、削波和明显损坏。
3. **transcribe**：WhisperX `large-v3`，保存可复用的原始 JSON。
4. **diarize**：释放转写模型显存，运行 pyannote，支持 `min_speakers`/`max_speakers` 提示。
5. **render**：应用人工姓名映射，按偏好生成带发言人的 Markdown/SRT。
6. **finalize**：校验必需产物并更新数据库状态。

所有输出先写同目录临时文件，再原子重命名。失败只标记当前阶段，不删除已成功产物。

### 5.3 CLI

计划中的最小命令：

```powershell
uv run meetingflow process "D:\Meetings\Inbox\meeting.mp4"
uv run meetingflow retry <job-id> --from diarize
uv run meetingflow render <job-id>   # 修改 speaker-map.toml 后重渲染
```

无参数运行进入中文菜单。`run.bat` 双击时打开菜单，拖入文件时仍把 `%~1` 原样传给 `meetingflow process`，不重复实现业务逻辑。

### 5.4 全局配置

V1 只有一个统一 Inbox，使用单个 TOML 配置，不引入配置框架和项目概念。

```toml
inbox = "D:/Meetings/Inbox"
work = "D:/Meetings/Work"
output = "D:/Meetings/Output"

[transcription]
model = "large-v3"
language = "zh"
compute_type = "int8_float16"
batch_size = 4
```

Hugging Face token 只从环境变量读取，不进入 TOML 和 Git。

## 6. V1 定稿决策

1. **音频路由**：不使用 VB-Cable，OBS 直接捕获 Realtek 与内置麦克风阵列，两路都关闭监听并录入音轨 1。
2. **功能范围**：只做到音频预检、转写、时间戳、SRT、发言人区分和人工姓名映射；会议纪要延后。
3. **输入方式**：手动选择统一 Inbox 的最新录音，或拖入/粘贴单个文件路径；不运行后台监听。

这三个决策是 V1 的固定边界。后续增加总结或多项目配置时再单独设计，不在当前代码中预留后端接口。

## 7. 开发里程碑与验收

### M0：录音与环境基线

- CUDA 12.8、Python 3.12、FFmpeg 可用；
- GPU 短推理通过；
- OBS 固定设备为 48 kHz；
- 一份 5—10 分钟样本的文件时长误差小于 1 秒，无变速变调，本地/远端声音齐全。

M0 不通过，不进入模型效果调优。

### M1：单文件转写

- CLI 接受含中文和空格的 Windows 路径；
- ffprobe 失败能给出可操作错误；
- 输出 raw JSON、TXT、SRT、Markdown 和日志；
- 用 5—10 分钟样本记录处理耗时、峰值显存和人工错误清单。

### M2：发言人与可重跑

- 两人和三人样本均输出发言人稿；
- 支持说话人数上下限；
- 修改 `speaker-map.toml` 后无需重新跑模型；
- 相同文件不会重复处理，失败可从失败阶段继续。

### M3：终端操作界面

- 可选择 Inbox 最新文件或拖入单个文件；
- 可按任务和发言人编号输入中文姓名；
- 可持久选择 Markdown、SRT 或两者；
- Output 只展示成品，内部恢复材料保存在 Work。

## 8. 测试策略

不建立庞大测试框架。保留三类最小证据：

- 单元测试：时间段合并、SRT 渲染、任务去重；
- 集成测试：一个短合成音频验证 ffprobe、输出和失败恢复；
- 人工基准：5—10 分钟真实会议样本，记录时长、速度、漏字、术语和说话人错误。

真实会议录音不提交仓库。测试夹具使用合成或公开、无敏感信息的短音频。

## 9. 后续版本

- 根据转写稿生成结构化会议纪要；
- 多项目独立配置、词汇表、模板与归档目录；
- 自动匹配真实发言人姓名；
- 轻量任务管理界面和系统通知。

## 10. 开工顺序

按 `M0 → M1 → M2 → M3` 开发。不做 watcher、GUI、总结或多项目扩展点；日常只通过终端菜单按需处理一两个会议文件。
