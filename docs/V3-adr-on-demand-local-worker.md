---
title: Agent 任务采用按需本机 worker
slug: V3-adr-on-demand-local-worker
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

# Agent 任务采用按需本机 worker

Agent 提交任务后，MeetingFlow 启动一个隐藏的本机 worker。worker 按提交顺序处理队列，清空后退出，并继续使用全局 GPU 锁串行运行模型。该选择让 Agent 无需持有长时间运行的子进程，也避免引入常驻服务、Web API或外部队列。
