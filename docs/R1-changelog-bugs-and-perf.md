---
title: MeetingFlow BUG 修复与性能提升变更记录（R1）
slug: R1-changelog-bugs-and-perf
version: R1
doc_type: changelog
status: final
scope: branch
audited_commit: a0fa9c2
branch: fix/bugs-and-perf
source: codex-self
created: 2026-07-21
last_reviewed: 2026-07-22
supersedes: null
superseded_by: null
related:
  - V1-audit-chatgpt-static
  - V1-diagnostic-transcription-quality
  - R1-review-branch-bugs-and-perf
  - V1-review-project-postfix
  - V1-visualization-4-stage-pipeline
---

# MeetingFlow BUG 修复与性能提升变更记录

## 1. 概述

本记录描述 `fix/bugs-and-perf` 分支相对 `main`（提交 `eb84e39`）的全部变更。改动基于两份诊断文档：

- `docs/ChatGPT代码审计结果.md`：静态代码审计，列出 14 项问题（4 个 P0、9 个 P1、3 个 P2/P3）。
- `docs/转录质量问题诊断与改进建议.md`：基于 98 分钟真实录音的转写质量诊断，给出 P0~P4 改进顺序。

目标是将 V1 从"功能可用"推进到"可长期日常使用"，重点修复缓存错误复用、多进程显存冲突、长会议内存峰值、段内说话人标错等根因问题。

## 2. 分支与提交

分支 `fix/bugs-and-perf`，共 6 个提交：

| 提交 | 说明 |
|------|------|
| `d38cd66` | docs: 纳入代码审计与转录质量诊断文档作为修复依据 |
| `6440d5d` | 批次1：可靠性基础（WAL、配置校验、FFmpeg 错误、OBS 稳定性、进程锁） |
| `25818d2` | 批次2：标准化 WAV 阶段（根治内存峰值与重复解码） |
| `5c02b20` | 批次3：指纹缓存与 retry 阶段化 |
| `c62f021` | 批次4：词级说话人分配 |
| `93792c3` | 批次5：转写参数化、文档同步、Ruff lint/format |

## 3. 架构变化

### 流水线阶段

原流水线：

```
probe -> transcribe -> diarize -> render
```

新流水线：

```
probe -> normalize -> transcribe -> diarize -> render
```

新增 `normalize` 阶段产出标准化中间产物 `Work/jobs/<sha256>/audio-16k-mono.wav`（16kHz、单声道、float32 PCM），后续转写、对齐、说话人识别、音量检测全部复用该文件，替代原先各阶段独立解码。

### 缓存复用模型

原模型：仅判断"SHA-256 相同 + 数据库成功 + 产物文件存在"。

新模型：每个阶段保存独立参数指纹（`stage_fingerprints` 表），复用前比对指纹；参数变化时只废弃该阶段及下游，不再全量重跑或错误复用旧结果。

### 说话人分配模型

原模型：每个转写段遍历所有说话人时间段，选重叠最长者，整段只标一个说话人（`_speaker_for`，O(N×M)）。

新模型：按词级时间戳为每个词分配说话人，渲染时按说话人变化点拆分行，连续同说话人词合并；段级匹配降级为无词级时间戳时的 fallback。

## 4. 批次详细说明

### 批次1：可靠性基础

纯标准库改动，不涉及模型加载。

**1.1 SQLite WAL 与 busy_timeout**
- `_open_database` 增加 `PRAGMA busy_timeout = 5000` 和 `PRAGMA journal_mode = WAL`，避免多进程并发时 `database is locked` 和读写互斥。

**1.2 配置校验 `validate_settings`**
- `load_settings` 末尾一次性收集并抛出所有配置问题：`batch_size > 0`、`min_speakers > 0`、`max_speakers >= min_speakers`、模型/语言非空、inbox/work/output 互不包含。
- 相对路径改为相对配置文件目录解析（原先相对当前工作目录）。

**1.3 FFmpeg 错误处理**
- `_max_volume` 检查 `returncode`，非零抛出明确异常；只有输出含 `max_volume: -inf` 才判静音，区分解码失败与真正静音。
- 新增 `ensure_ffmpeg_available()`，`main()` 启动时检查 ffmpeg/ffprobe 在 PATH。

**1.4 OBS 文件稳定性 `wait_until_stable`**
- 连续 3 次检查（间隔 1s）文件大小和 mtime 不变；Windows 下尝试以禁止共享写方式打开检测句柄占用；超时（默认 60s）提示"文件可能仍在录制"。
- 集成到菜单"转写 Inbox 中最新文件"路径（CLI `process` 假定用户已确认）。

**1.5 进程锁**
- `_file_lock`：基于 `os.open(O_CREAT|O_EXCL)` 的独占文件锁，锁文件写入 PID，崩溃残留时按 PID 存活检测回收。
- 全局 GPU 锁 `Work/.gpu.lock` + 任务锁 `Work/.lock-<job_id>`，`process()` 入口先取全局锁再取任务锁，落实"单 GPU 串行"。
- 获取失败提示"另一个 MeetingFlow 正在运行"或"该任务正由另一个 MeetingFlow 处理"。

### 批次2：标准化 WAV 阶段

跨模块重构，是本期最大的结构性改动。

**2.1 `normalize_audio`**
- FFmpeg 流式写 `Work/jobs/<id>/.audio-16k-mono.wav.tmp`（`-ac 1 -ar 16000 -c:a pcm_f32le`），成功后原子 rename 为 `audio-16k-mono.wav`。
- 同一次 FFmpeg 调用附加 `volumedetect` 取 max_volume，不再单独扫描源文件。
- 返回 `(wav_path, max_volume)`，max_volume 写入 `source.json`。

**2.2 transcribe 改用 WAV 路径**
- `transcribe(audio_path, settings)` 接收标准化 WAV 路径，WhisperX `load_model`/`transcribe`/`align` 全部指向 WAV。

**2.3 diarize 改用 WAV 路径**
- `diarize(audio_path, settings)` 接收 WAV 路径。
- 删除 `_decode_audio` 的 `capture_output=True` 整段读入 + `bytearray` 双副本逻辑，改用 `torchaudio.load` 单副本读盘。
- 3 小时会议仅音频副本即从约 1.29 GiB 降至单副本约 659 MiB，且不再多次解码。

**2.4 pipeline 集成 normalize 阶段**
- probe 后插入 normalize 阶段，写 `stages` 表和 `run.jsonl`。
- WAV 视为可复用中间产物；旧任务无 WAV 时按需补生成，向后兼容。
- 阶段编号更新为 4 阶段制：normalize 1/4、转写 2/4、词级对齐 3/4、说话人识别 4/4。
- `probe_audio` 不再单独 `volumedetect`，移除 `_max_volume`（逻辑并入 `normalize_audio`）。

### 批次3：指纹缓存与 retry 阶段化

**3.1 数据库结构（零迁移）**
- 新增独立表 `stage_fingerprints(job_id, name, fingerprint, PRIMARY KEY(job_id, name))`，不动现有 `jobs`/`stages` 表。
- 旧任务无指纹记录视为"兼容复用"，不破坏已成功任务。

**3.2 指纹计算**
- `_probe_fingerprint`：常量 `probe-v1`。
- `_normalize_fingerprint`：编码/采样率/声道参数。
- `_transcription_fingerprint`：模型、语言、compute_type、batch_size。
- `_diarization_fingerprint`：模型 ID、min/max_speakers。
- 指纹为参数字典的 canonical JSON 的 SHA-256。

**3.3 复用判断改造**
- `_should_rerun`：综合 `rerun_active`（上游已重跑则下游必重跑）、`start_stage`（强制重跑起点）、产物存在性、指纹匹配。
- `_all_fingerprints_match`：整体 skip 前检查所有阶段指纹，避免改参数后仍整体复用旧产物。
- 每阶段成功后写指纹；参数变化只废弃该阶段及下游，不再全量重跑。

**3.4 retry 阶段化**
- `process` 签名 `force: bool` 改为 `start_stage: str | None`。
- `retry(job_id, from_stage)` 调用 `process(..., start_stage=from_stage)`，实现真正阶段级重跑：`--from diarize` 只重跑说话人+渲染，保留转写；`--from transcribe` 重跑转写+说话人。
- CLI `--from` 增加 `normalize` 选项；`--force` 等价 `start_stage="probe"`，向后兼容。

### 批次4：词级说话人分配

转录质量 P0，根因明确、收益确定的第一优先级。

**4.1 词级分配**
- `_assign_word_speakers`：遍历转写结果的 `words`，按每个词的 `start`/`end` 匹配 `list[SpeakerSegment]` 中重叠最长的说话人，给词加 `speaker` 字段。
- 等价于 WhisperX `assign_word_speakers`，但基于 `list[SpeakerSegment]` 实现，避免 pyannote `Annotation` 依赖，便于测试与 mock。
- 结果存 `transcript.aligned.json`。

**4.2 render 词级拆分**
- `render_speakers_markdown`/`render_speakers_srt` 重写为遍历词，按说话人变化点拆分行，连续同说话人词重新合并为行文本。
- 删除段级 `_speaker_for` 的 O(N×M) 整段匹配，保留为无词级时间戳时的 fallback。

**4.3 旧任务兼容**
- `_render_outputs` 优先读 `transcript.aligned.json`，缺失时回退 `transcript.raw.json` 段级渲染。
- 旧任务（无 aligned 或无词级时间戳）仍可重新渲染，输出格式不变。

### 批次5：转写参数化、文档与 Ruff

**5.1 转写参数化**
- `TranscriptionSettings` 增加 `repetition_penalty`（默认 1.0）、`no_repeat_ngram_size`（默认 0）、`chunk_size`（默认 30），默认值保持当前行为。
- `_asr_options`：仅当重复惩罚或 ngram 非默认时构造 `asr_options` 传入 `model.transcribe`，避免覆盖 WhisperX 默认值。
- 参数暴露为配置项，待用代表性片段 A/B 核听后再固化推荐值（不建议未经核听直接改默认）。

**5.2 配置示例与文档**
- `config/meetingflow.toml.example` 增加转写参数注释和离线环境变量说明（`HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`、`PYANNOTE_METRICS_ENABLED`）。
- README 同步：normalize 阶段产物、进程锁落实、指纹复用说明。

**5.3 Ruff 引入**
- 新增 Ruff dev 依赖与 `[tool.ruff]` 配置，lint 规则集 `E/F/W/I/UP/B/C4/SIM`。
- `ignore E701/E702` 保留项目既有的紧凑单行多语句风格。
- `ruff check --fix` 修复 import 排序、SIM117；手动修复 SIM103（`_should_rerun` 简化）、B904（`_ensure_punkt_tab` 的 `raise ... from`）。
- `ruff format` 统一格式。

## 5. 审计问题修复对照

| 审计编号 | 问题 | 修复 |
|---|---|---|
| P0-1 | 缓存不识别模型和参数变化 | 批次3 指纹缓存 |
| P0-2 | 多进程并行加载 GPU | 批次1 进程锁 + WAL |
| P0-3 | 长会议说话人识别内存峰值 | 批次2 normalize 流式写盘 + torchaudio 单副本 |
| P0-4 | 同一录音多次完整解码 | 批次2 标准化 WAV 一次生成复用 |
| P1-5 | 发言人匹配慢且段内标错 | 批次4 词级说话人分配 |
| P1-6 | retry 未真正实现阶段级 | 批次3 start_stage |
| P1-7 | 未确认 OBS 文件写入完成 | 批次1 wait_until_stable |
| P1-8 | FFmpeg 失败误判静音 | 批次1 returncode 检查 + ensure_ffmpeg_available |
| P1-9 | 配置无边界验证 | 批次1 validate_settings |
| P2-10 | 离线模式未闭环 | 批次5 离线环境变量文档 |
| P3-13 | 类型/lint 无工具保障 | 批次5 Ruff |

## 6. 转录质量改进对照

| 质量文档优先级 | 问题 | 修复 |
|---|---|---|
| P0 | 段内说话人切换标错 | 批次4 词级说话人分配 |
| P1 | 重复幻觉 | 批次5 重复惩罚参数化（默认未改，待核听） |
| P2 | VAD 漏字 | 推迟，需核听确认 |
| P3 | faster-whisper 顺序解码 | 推迟 |
| P4 | 领域词提示 | 推迟 |

## 7. 新增产物与配置

### 任务目录 `Work/jobs/<sha256>/` 新增文件

- `audio-16k-mono.wav`：标准化 16kHz 单声道 float32 中间音频。
- `transcript.aligned.json`：词级说话人分配后的转写，渲染时按词级边界拆分。

### 数据库新增表

- `stage_fingerprints(job_id, name, fingerprint)`：各阶段参数指纹。

### 配置项新增

```toml
[transcription]
repetition_penalty = 1.1      # 默认 1.0，核听后再固化
no_repeat_ngram_size = 3      # 默认 0，核听后再固化
chunk_size = 20               # 默认 30，核听后再固化
```

### 环境变量（建议设置，不入仓库）

- `HF_HUB_OFFLINE=1`
- `TRANSFORMERS_OFFLINE=1`
- `PYANNOTE_METRICS_ENABLED=0`

## 8. 测试覆盖

`uv run pytest`：**34 passed**。

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_pipeline.py` | 主流程、输出布局、格式切换、中文姓名、旧任务兼容、菜单交互、媒体探测、配置校验、FFmpeg 错误、OBS 稳定性、asr_options |
| `tests/test_normalize.py` | 标准化 WAV 生成（16kHz/单声道）、无临时文件残留 |
| `tests/test_fingerprint.py | 改模型只重跑转写+说话人、改说话人数只重跑说话人、retry --from diarize/transcribe 阶段语义 |
| `tests/test_speaker_assignment.py` | 词级说话人拆分、连续同说话人合并、段级 fallback、_assign_word_speakers 标注 |
| `tests/test_concurrency.py` | 文件锁获取/释放、存活进程拒绝、残留锁回收、process 全局锁拒绝 |

`uv run ruff check`：All checks passed。`uv run ruff format --check`：12 files already formatted。

## 9. 验收状态

- 自动化测试：全绿（34 passed）。
- Lint/格式：全过。
- 真实音频人工验收：**未完成**，需用代表性短录音跑全流程核对（见第 11 节）。

## 10. 明确未做（计划推迟项）

- faster-whisper 顺序解码（转录质量 P3，文档要求核听后再定）。
- 领域词 `initial_prompt`/`hotwords`（转录质量 P4）。
- 模型 revision 固定（审计 P2-11）。
- mypy/pyright/pip-audit/coverage/bandit（审计 P3-13，本期仅 Ruff）。
- `meetingflow doctor` 子命令（审计 P2-10，离线模式仅以环境变量文档形式提供）。
- 转写参数默认值固化（待 A/B 核听）。

## 11. 人工验收指引

用一份代表性短录音跑全流程，逐项核对：

1. **词级说话人拆分**：一段内说话人切换是否拆成两行（对比旧版整段一人）。
2. **normalize 复用**：重复执行同一文件是否显示"复用已有标准化音频"。
3. **参数变化缓存失效**：改模型后重跑是否只重跑转写+说话人，probe+normalize 复用；改说话人数是否只重跑说话人。
4. **retry 阶段语义**：`retry <id> --from diarize` 是否只重跑说话人，保留转写；`--from transcribe` 是否重跑转写+说话人。
5. **并发锁**：双开 `run.bat` 是否提示"另一个 MeetingFlow 正在运行"。
6. **OBS 稳定性**：菜单"转写 Inbox 最新文件"是否先确认文件稳定。
7. **长会议内存**：1~3 小时录音跑 diarize 时内存峰值是否显著下降（不再 capture_output 整段入内存）。
8. **转写参数**：配置 `repetition_penalty=1.1` 等后是否生效，连续重复字是否减少（需核听确认不误伤）。

## 12. 遗留提醒

`uv add --dev ruff` 因 `.venv` 的 `torchgen/static_runtime` 目录被占用失败，改用 `uv pip install ruff` 安装成功，`pyproject.toml` 与 `uv.lock` 已含 ruff dev 依赖。但 `.venv` 的完整 `uv sync` 仍被该锁阻塞，下次手动 `uv sync` 前需确认无其他 Python 进程持有 `.venv`。
