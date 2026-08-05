---
title: MeetingFlow V2 Wayfinder 会议纪要模式流水线
slug: V2-tech-design-wayfinder-pipeline
version: V2
doc_type: tech-design
status: final
scope: project
audited_commit: null
branch: main
source: codex
created: 2026-08-05
last_reviewed: 2026-08-05
supersedes: V1-tech-design-meetingflow
superseded_by: null
related:
  - V1-tech-design-meetingflow
---

# MeetingFlow V2 Wayfinder 会议纪要模式流水线

> 状态：方案、实现与验收均已完成；本文是当前架构依据。

## 目标与边界

V2 把已完成写入的会议音频转换为适合整理会议纪要的本地对话稿。目标是内容准确、主要发言轮次可区分、可在无网络和无 Token 的环境运行。

V2 不做字幕级逐字时间戳、逐字说话人归属、会议总结、GUI、实时字幕、Web 服务或上传音频。原始录音始终只读。

## 选型结论

采用 SenseVoiceSmall + FSMN-VAD + CAM++ 单次本地分析，替代 V1 的 WhisperX、Wav2Vec2 词级强制对齐和 pyannote 链路。

固定三场景盲评中，SenseVoice 在日常和困难场景胜出；Turbo 在多人场景胜出，但 V2 以 2/3 多数决作为会议纪要模式的默认方案。V1 large-v3 因连续重复失控淘汰。

## 稳定业务契约

- 保留单文件 CLI、源文件 SHA-256 身份、幂等处理、SQLite 任务数据库与原子写入。
- 保留 probe、normalize、transcribe、diarize 阶段名称和 retry --from 兼容入口。
- diarize 在 V2 只从原生分析产物派生 transcript.raw.json、speakers.json 并重渲染；不得重新运行 GPU 模型。原生分析缺失时，应要求从 transcribe 重试。
- 保留 speakers.md、speakers.json、speaker-map.toml、run.jsonl 及人工改名后无需重新运行模型的重渲染。
- 旧任务的 transcript.aligned.json 继续可读；新任务不再生成词级对齐产物。

## 处理链路与产物

1. probe：维持现有 FFmpeg/ffprobe 媒体检查。
2. normalize：生成 16 kHz 单声道 WAV。
3. transcribe：串行加载 SenseVoiceSmall、FSMN-VAD、CAM++，生成包含文字、VAD 时间和说话人段的原生分析产物。
4. diarize：根据原生分析派生段级转写与主要发言人轮次，更新说话人映射并渲染 Markdown/SRT。
5. 成功后删除可重建的标准化 WAV；重跑时从原始录音重新生成。

长期保留原生分析产物、段级转写、说话人段、映射、最终 Markdown/SRT 和小体积日志；不保留词级对齐、词级时间戳或标准化 WAV。

## 冻结运行条件

- funasr==1.3.27、modelscope==1.39.1、modelscope-hub==0.2.0。
- language=zh、use_itn=True、batch_size_s=60。
- FSMN-VAD：max_single_segment_time=15000、merge_vad=True、merge_length_s=10。
- CAM++：spk_mode=vad_segment。
- GPU：device=cuda:0，模型串行加载。
- 禁止在线更新与远程代码：disable_update=True、trust_remote_code=False。

三个模型使用版本化本地绝对路径。启动前必须验证模型目录、完整文件集、固定版本信息和 SHA-256；缺失或不匹配立即失败，不得回退为在线模型 ID。

## 离线与空间目标

日常运行不依赖 Hugging Face、ModelScope、PyPI、Token 或 VPN。零 socket 探针已验证选型原型在断网时没有网络尝试。

长期运行体目标为约 6.4 GB（复用已有 FFmpeg），包含精简 GPU Python 环境、FunASR/ModelScope 运行依赖、SenseVoiceSmall、FSMN-VAD、CAM++ 及少量程序和结果；不包含原始录音、任务数据、uv/wheel 缓存、Hub 下载缓存和离线重装包。若独立携带 FFmpeg，则上限约 6.9 GB。

旧模型、旧环境和缓存只能在 V2 完成验收、回退窗口结束后清理。

## 验收与回退

合并前必须完成：固定三场景回归、断网/零网络探针、无 HF_TOKEN 运行、CLI/重试/改名/渲染回归，以及最终空间实测。任一结果出现凭空完整内容、整段遗漏或连续重复失控，均不通过。

切换前保留 V1 环境、模型与任务产物。V2 不修改数据库表结构；如出现回归，可回退到 V1 代码和环境，无需迁移旧任务。
