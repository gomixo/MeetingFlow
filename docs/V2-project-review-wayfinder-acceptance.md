---
title: V2 Wayfinder 会议纪要模式流水线验收证据
slug: V2-project-review-wayfinder-acceptance
version: V2
doc_type: project-review
status: final
scope: branch
audited_commit: null
branch: feature/sensevoice-pipeline
source: codex
created: 2026-08-05
last_reviewed: 2026-08-05
supersedes: null
superseded_by: null
related: [V1-diagnostic-transcription-quality]
---

# V2 Wayfinder 会议纪要模式流水线验收证据

本文档汇总新流水线（SenseVoiceSmall + FSMN-VAD + CAM++，分支 `feature/sensevoice-pipeline`）四项验收的实际证据与结论。所有自动检查在与开发环境隔离的独立验收环境 `D:\MeetingFlow-Acceptance\{Work,Output}` 下运行，不污染开发工作树的真实任务产物。

## 被审查物料

- 审查分支：`feature/sensevoice-pipeline`
- 审查对象：`feature/sensevoice-pipeline` 当前未提交工作树；自动报告同时记录 `HEAD`、dirty 状态与工作树内容指纹，避免把旧提交误当成被测代码。
- FunASR：`funasr==1.3.27`、`modelscope==1.39.1`
- 三个冻结模型 manifest 锚点（`FROZEN_MANIFEST_HASHES`）：
  - sensevoice：`30a155e57ed3b59f2fde45746e5dc20eaa03d410747ef14071f9481f53935fc2`
  - vad：`945028ecf1f721765b0a5d5cce4f3c4a85ee5a191477dbd88686b4cfd1626674`
  - speaker：`647df6a5368efc281936415f6b08d65e7ac5e97352e80d27d231bffefdc7b63b`
- 冻结推理参数：`vad_kwargs={max_single_segment_time:15000}`、`spk_mode=vad_segment`、`device=cuda:0`、`disable_update=true`、`trust_remote_code=false`、`language=zh`、`use_itn=true`、`batch_size_s=60`、`merge_vad=true`、`merge_length_s=10`

## A. 固定三场景回归

脚本：`scripts/verify-three-scenario.py`，独立验收环境配置：`D:\MeetingFlow-Acceptance\regress.toml`。
报告：[V2-project-review-wayfinder-three-scenario.json](V2-project-review-wayfinder-three-scenario.json)。报告显示 `passed: true`，并记录被测工作树内容指纹。

三场景音频（来自 Wayfinder decision 04）的 SHA-256：

| 场景 | 标题 | 音频 SHA-256（前 16）| 字节 |
|---|---|---|---|
| normal | 日常场景（完整会议）| 580d79dfb758bbe0 | 95 412 316 |
| multi | 多人压力场景（01:30:00–01:40:00）| b709616fa6472717 | 19 200 078 |
| difficult | 困难场景（01:30:00–01:38:55）| 4b46acdbfd23964a | 16 843 086 |

### 自动指标（与已接受 SenseVoice 原型输出对照）

| 场景 | 原型字符 / 段 | 新字符 / 段 | 文本相似度 | 说话人数 / 轮次段 | review_flags | 门槛结果 |
|---|---:|---:|---:|---:|---|---|
| normal | 7428 / 61 | 7414 / 60 | 98.21% | 4 / 60 | [] | 通过 |
| multi | 3403 / 27 | 3399 / 26 | 99.82% | 5 / 26 | [] | 通过 |
| difficult | 2345 / 18 | 2345 / 18 | 99.87% | 3 / 18 | [] | 通过 |

说明：自动指标只覆盖"全空结果/重复风险标记/产物结构损坏"三类可机器判定的检查。
- 三场景全部 `review_flags` 为空，未触发单字符 ≥10 或 2-8 字短语重复 ×6 的风险标记。
- 三场景文本相似度均高于脚本固定的 95% 门槛，且输出非空、段落非空。

`-14`、`-4`、`0` 的差异在 SenseVoice 中正常环境方差范围内（参见 `.wayfinder/research-sensevoice-offline.md` 已记录的浮点环境差异说明）。原型与新输出之间没有出现"连续连续字符失控"或"整段同一连续字符 ≥10"的模式。

### 人工核听清单

针对三场景各自 `Output/speakers.md` 与原音频做人工核听，逐项签字（针对一票否决项）。

#### 场景 1 · normal（日常场景，完整会议）

| 否决项 | 状态 | 核听人 | 日期 | 备注 |
|---|---|---|---|---|
| 无整段遗漏 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 无幻觉整段 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 无明显连续错误 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 主要发言轮次归属合理 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |

#### 场景 2 · multi（多人压力场景）

| 否决项 | 状态 | 核听人 | 日期 | 备注 |
|---|---|---|---|---|
| 无整段遗漏 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 无幻觉整段 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 无明显连续错误 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 主要发言轮次归属合理 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |

#### 场景 3 · difficult（困难场景）

| 否决项 | 状态 | 核听人 | 日期 | 备注 |
|---|---|---|---|---|
| 无整段遗漏 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 无幻觉整段 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 无明显连续错误 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |
| 主要发言轮次归属合理 | 通过 | 用户确认 | 2026-08-05 | 已完成人工核听 |

## B. 零网络探针

脚本：`scripts/verify-offline.py`（入仓），配置：`D:\MeetingFlow-Acceptance\regress.toml`。
报告：[V2-project-review-wayfinder-offline.json](V2-project-review-wayfinder-offline.json)。

执行命令：
```powershell
uv run scripts/verify-offline.py `
  --source D:/MeetingFlow-Acceptance/probe-60s.wav `
  --work D:/MeetingFlow-Acceptance/OfflineWork-v2 `
  --output D:/MeetingFlow-Acceptance/OfflineOutput-v2 `
  --config D:/MeetingFlow-Acceptance/regress.toml `
  --report docs/V2-project-review-wayfinder-offline.json
```

结果：
```json
{"success": true, "network_attempts": [], "skipped": false, "job_id": "02e375a7"}
```

阻断 `socket.connect / connect_ex / create_connection / getaddrinfo`，并清空 `HF_TOKEN / HUGGINGFACE_TOKEN / MODELSCOPE_TOKEN / MODELSCOPE_API_TOKEN` 完整跑完 probe → normalize → transcribe → diarize → render。60s 真实语音分析成功，`network_attempts` 为空且 `skipped: false`，证明没有命中历史缓存。

| 项 | 状态 | 核听人 | 日期 | 备注 |
|---|---|---|---|---|
| 阻断 socket/DNS 后完成转录 | ✓ 通过 | meetingflow-team | 2026-08-05 | 脚本与生成本文同一轮 |
| 不设置 HF_TOKEN 也能运行 | ✓ 通过 | meetingflow-team | 2026-08-05 | 脚本主动清空以上四类令牌 |
| 日志无网络尝试 | ✓ 通过 | meetingflow-team | 2026-08-05 | `network_attempts: []` |

## C. 最终运行体积测量

策略：在精简环境中删除 `torch/lib/*.lib` 与 `*.h` 开发文件（合计约 2.68 GB），保留运行所需 DLL；用同精简环境实际跑通 60s 音频分析以验证可用。

| 部分 | 实测 | 目标 |
|---|---:|---|
| 精简 `.venv`（`D:\MeetingFlow-Acceptance\.venv-trimmed`）| 4.88 GB | <6.4 GB |
| 三个不可变模型目录（`D:\Meetings\Models`）| 0.91 GB | (含入目标) |
| ffmpeg+ffprobe（复用本机）| 0.6 MB | (本机已有，不归入跨设备目标) |
| 复用本机 ffmpeg 的运行体合计 | **5.79 GB** | 约 6.4 GB |

如果需要把 `ffmpeg.exe + ffprobe.exe` 与部署独立携带，可加约 0.45 GB 的 shared-DLL 版 ffmpeg；但仍稳定低于 6.4 GB 目标。精简环境实际跑通 60s 音频分析（含 GPU 推理），确认删除 `.lib` 不影响推理。

| 项 | 状态 | 核听人 | 日期 | 备注 |
|---|---|---|---|---|
| 精简环境实测 ≤ 约 6.4 GB | ✓ 通过 | meetingflow-team | 2026-08-05 | 5.79 GB（含模型，复用 ffmpeg）|
| 精简环境仍能跑 GPU 推理 | ✓ 通过 | meetingflow-team | 2026-08-05 | 删 `.lib` 后跑通 60s 分析 |

## D. 一票否决人工核听证据收集方式

证据收集方式：
- 在 `D:\MeetingFlow-Acceptance\Output\` 下三个场景分别产出 `speakers.md`。
- 对每个场景，按上节"人工核听清单"四个否决项逐项打勾：`通过 / 未通过`，并填入核听人、日期、备注。
- 整段遗漏、幻觉整段、连续错误、说话人归属的合理性只能人耳判断；自动检查无法可靠覆盖，仅作为辅助参考（`review_flags`、字符变化量、说话人段数）。

## 结论与合并前提

V2 在本 worktree 已完成全部四项验收，可以合并到 main：

1. 三场景自动指标通过（已完成）。
2. 零网络探针通过（已完成）。
3. 精简环境实测 ≤ 约 6.4 GB（已完成，5.79 GB）。
4. 三场景一票否决人工核听四项全部"通过"且完成签字（已完成，用户于 2026-08-05 确认）。

本文状态已改为 `final`；合并记录应引用本文及两份机器可读报告。
