---
title: V3 Agent 接口开发方案
slug: V3-tech-design-agent-interface
version: V3
doc_type: tech-design
status: draft
scope: branch
audited_commit: null
branch: feature/agent-interface
source: codex
created: 2026-08-31
last_reviewed: 2026-08-31
supersedes: null
superseded_by: null
related: [V3-adr-versioned-agent-json, V3-adr-on-demand-local-worker]
---

# V3 Agent 接口开发方案

## 目标

V3 为本机 Agent 提供稳定、可校验的 JSON 边界。MeetingFlow 仍只负责从已完成写入的录音生成结构化转录，不公开模型参数、内部阶段、SQLite 或 `Work/jobs` 布局。

## 接口

`uv run meetingflow --config <path> agent` 从 stdin 读取一个 `schema_version: 1` 请求，并向 stdout 写一个响应。支持 `submit`、`status` 和整任务 `retry`。任务状态只公开 `queued`、`running`、`succeeded`、`failed`，完整 job ID 固定为源文件 SHA-256。

请求、响应和结果 Schema 位于 `schemas/`。合法协议响应退出码为 0，协议或配置错误为 2。失败任务通过稳定英文错误码和中文摘要报告，异常堆栈仍只进入内部 `run.jsonl`。

## 执行与恢复

`submit` 确认文件稳定后登记任务，并启动隐藏 worker。worker 使用单例锁，按提交时间和 job ID 排序处理队列，继续等待现有 GPU 锁来保证模型串行运行。任意 Agent 操作发现 worker 与 GPU 锁均无有效持有者时，只把 Agent 管理的遗留 `running` 任务恢复为 `queued`。

现有 `jobs` 表原地增加 `submitted_at`、`updated_at`、`error_code`、`error_message`。迁移只增列，不重建数据库。提交后处理前再次校验 SHA-256，源文件变化时以 `SOURCE_CHANGED` 失败。

## 公开结果

成功任务原子写入 `Output/<任务>/result.json`。文件包含来源绝对路径、媒体摘要、语言、复核标记、主要发言轮次和已生成的 Markdown/SRT 路径。每个轮次含开始时间、结束时间、说话人 ID、当前姓名和文字。

V3 任务在处理成功、结果复用、人工改名和重新渲染后都会刷新 `result.json`。旧版平铺任务缺少公开结果所需的媒体摘要，因此继续支持原有人工渲染但不伪造 `result.json`；Agent 首次提交时会按当前指纹补齐公开结果。

## 验收

- `uv run pytest`
- `uv run ruff check .`
- 非法请求、数据库迁移、失败查询、崩溃恢复、源文件变化和改名同步均有回归测试。
- 原有菜单及 `process`、`render`、`retry` CLI 保持兼容。
