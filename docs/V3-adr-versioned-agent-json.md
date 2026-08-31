---
title: Agent 接口采用版本化 JSON 契约
slug: V3-adr-versioned-agent-json
version: V3
doc_type: adr
status: draft
scope: branch
audited_commit: null
branch: feature/agent-interface
source: codex
created: 2026-08-31
last_reviewed: 2026-08-31
supersedes: null
superseded_by: null
related: [V3-tech-design-agent-interface]
---

# Agent 接口采用版本化 JSON 契约

MeetingFlow 将 `meetingflow agent` 的 stdin、stdout 和 `result.json` 作为稳定接口，并为三者提供 JSON Schema。内部 Python 函数、SQLite、任务目录和模型产物不承诺兼容性。这样 Agent 可以自动校验数据，同时 MeetingFlow 可以继续调整本地流水线。
