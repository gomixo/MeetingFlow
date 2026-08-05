---
title: MeetingFlow 文档索引
slug: README
version: V2
doc_type: index
status: final
scope: project
audited_commit: null
branch: null
source: codex
created: 2026-07-22
last_reviewed: 2026-08-05
supersedes: null
superseded_by: null
related: [V2-project-review-wayfinder-acceptance]
---

# MeetingFlow 文档索引

## 当前入口

- [V2-project-review-wayfinder-acceptance.md](V2-project-review-wayfinder-acceptance.md) — 当前 Wayfinder 链路的自动验收证据与人工核听清单，状态为 `draft`。
- [V2-project-review-wayfinder-three-scenario.json](V2-project-review-wayfinder-three-scenario.json) — 强制重跑三场景的机器可读报告。
- [V2-project-review-wayfinder-offline.json](V2-project-review-wayfinder-offline.json) — 零网络、未命中缓存的机器可读报告。

## 历史资料

- [V0-project-plan-meetingflow.md](V0-project-plan-meetingflow.md) — V0 初始项目方案，已被后续设计取代。
- [V1-tech-design-meetingflow.md](V1-tech-design-meetingflow.md) — V1 WhisperX/pyannote 链路设计，已被 V2 取代。
- [V1-audit-chatgpt-static.md](V1-audit-chatgpt-static.md) — ChatGPT 对 V1 的静态代码审计。
- [V1-diagnostic-transcription-quality.md](V1-diagnostic-transcription-quality.md) — V1 重复失控与说话人边界问题诊断。

## 命名与元数据

文档文件名采用 `<Vn|Rn>-<doc_type>-<description>.<ext>`，只使用 ASCII 与连字符。Markdown 文档开头必须包含：`title`、`slug`、`version`、`doc_type`、`status`、`scope`、`audited_commit`、`branch`、`source`、`created`、`last_reviewed`、`supersedes`、`superseded_by`、`related`。

`status` 只使用 `draft`、`final`、`archived`、`superseded`。人工核听未签字前，V2 验收文档保持 `draft`。
