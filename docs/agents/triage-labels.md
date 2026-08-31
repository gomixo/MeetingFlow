---
title: Triage 标签配置
slug: triage-labels
version: V3
doc_type: agent-config
status: final
scope: project
audited_commit: null
branch: feature/agent-interface
source: codex
created: 2026-08-31
last_reviewed: 2026-08-31
supersedes: null
superseded_by: null
related: []
---

# Triage labels

| Skill 角色 | GitHub 标签 | 含义 |
|---|---|---|
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待报告者补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格完整，可由 Agent 执行 |
| `ready-for-human` | `ready-for-human` | 需要人工处理 |
| `wontfix` | `wontfix` | 不计划处理 |

Skill 提及 triage 角色时，使用表中对应的 GitHub 标签。
