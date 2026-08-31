---
title: GitHub Issue Tracker 配置
slug: issue-tracker
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

# Issue tracker: GitHub

本仓库的 issue 和规格记录在 GitHub Issues，所有操作使用 `gh` CLI。

## 常用操作

- 创建：`gh issue create --title "..." --body "..."`
- 查看：`gh issue view <number> --comments`
- 列表：`gh issue list --state open`
- 评论：`gh issue comment <number> --body "..."`
- 标签：`gh issue edit <number> --add-label "..."` 或 `--remove-label "..."`
- 关闭：`gh issue close <number> --comment "..."`

在仓库目录中执行命令，由 `gh` 根据 Git 远端确定 `gomixo/MeetingFlow`。

## Pull request 是否进入 triage

PRs as a request surface: no.

## Skill 约定

- “publish to the issue tracker”表示创建 GitHub issue。
- “fetch the relevant ticket”表示执行 `gh issue view <number> --comments`。
