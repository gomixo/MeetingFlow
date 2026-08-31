---
title: Domain Docs 配置
slug: domain
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

# Domain docs

工程 Skill 探索代码前应读取：

- 根目录 `CONTEXT.md`
- `docs/` 下与工作范围相关的 `V*-adr-*.md` 或 `R*-adr-*.md`

文件不存在时继续执行，不需要预先创建。

本仓库采用 single-context 布局。输出中的领域概念应使用 `CONTEXT.md` 定义的词汇，避免使用其中明确排除的同义词。

如果方案与现有 ADR 冲突，应指出具体 ADR 和冲突原因，不得静默覆盖已有决策。
