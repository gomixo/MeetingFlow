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
related: [V2-tech-design-wayfinder-pipeline, V2-project-review-wayfinder-acceptance]
---

# MeetingFlow 文档索引

本目录汇集 MeetingFlow 项目的所有设计、审查、诊断与可视化资料。所有文档遵循统一的版次命名规范与 YAML front matter 元信息。

## 版次代号说明

- `Vn`：项目代次（`V0` 立项稿 → `V1` 实现稿 → `V2` ...）。
- `Rn`：第 n 轮修复（`R1` = `fix/bugs-and-perf` 那批修复）。

## 当前入口

当前架构为 [V2-tech-design-wayfinder-pipeline.md](V2-tech-design-wayfinder-pipeline.md)，验收结论见 [V2-project-review-wayfinder-acceptance.md](V2-project-review-wayfinder-acceptance.md)。V1 及其相关审查、诊断资料均为历史记录。

## 文档清单

| 版次 | doc_type | 状态 | 文件 | 标题 |
|---|---|---|---|---|
| V0 | project-plan | superseded | [`V0-project-plan-meetingflow.md`](V0-project-plan-meetingflow.md) | 项目方案（V0 立项稿） |
| V1 | tech-design | superseded | [V1-tech-design-meetingflow.md](V1-tech-design-meetingflow.md) | V1 详细开发方案（历史） |
| V2 | tech-design | final | [V2-tech-design-wayfinder-pipeline.md](V2-tech-design-wayfinder-pipeline.md) | V2 Wayfinder 会议纪要模式流水线 |
| V2 | project-review | final | [V2-project-review-wayfinder-acceptance.md](V2-project-review-wayfinder-acceptance.md) | V2 Wayfinder 验收证据与人工核听结论 |
| V1 | code-audit | final | [`V1-audit-chatgpt-static.md`](V1-audit-chatgpt-static.md) | ChatGPT 对 V1 的代码审计结果 |
| V1 | diagnostic | final | [`V1-diagnostic-transcription-quality.md`](V1-diagnostic-transcription-quality.md) | 转录质量问题诊断与改进建议 |
| V1 | diagnostic | final | [`V1-diagnostic-real-meeting-benchmark.md`](V1-diagnostic-real-meeting-benchmark.md) | 真实会议转写质量评测与优化建议 |
| R1 | changelog | final | [`R1-changelog-bugs-and-perf.md`](R1-changelog-bugs-and-perf.md) | BUG 修复与性能提升变更记录 |
| R1 | branch-review | final | [`R1-review-branch-bugs-and-perf.md`](R1-review-branch-bugs-and-perf.md) | `fix/bugs-and-perf` 分支复审意见 |
| V1 | project-review | final | [`V1-review-project-postfix.md`](V1-review-project-postfix.md) | 修复后项目审查报告 |
| V1 | visualization | final | [`V1-visualization-4-stage-pipeline.html`](V1-visualization-4-stage-pipeline.html) | 四阶段流水线可视化讲解 |

机器可读验收报告：[三场景回归](V2-project-review-wayfinder-three-scenario.json)、[零网络探针](V2-project-review-wayfinder-offline.json)。

> 公众号文章相关素材（发布版、初稿、大纲、配图与上传脚本）保存在本机 `docs/公众号文章/` 子目录，不纳入 Git 或本文档索引。

## 文档关联关系

```
V0-project-plan-meetingflow            ← 项目源头（已 superseded）
        │
        ▼
V1-tech-design-meetingflow             ← V1 工程级技术设计
        │
        ├─► V1-audit-chatgpt-static    ← 修复输入：ChatGPT 静态审计
        │
        └─► V1-diagnostic-transcription-quality  ← 修复输入：转录质量诊断
                    │
                    ▼
        R1-changelog-bugs-and-perf     ← 修复执行（R1 = fix/bugs-and-perf）
                    │
        ┌───────────┼───────────────────────────┐
        ▼           ▼                           ▼
R1-review-branch    V1-review-project            V1-visualization-4-stage
   -bugs-and-perf     -postfix                    -pipeline.html
        │           │
        └───────────┴─► 上下游均指向 R1 的修复执行
```

## YAML front matter 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` | string | 与正文一级标题一致的中文标题 |
| `slug` | string | 与文件名（不含扩展名）一致，纯 ASCII + 连字符 |
| `version` | string | 版次代号（`V0`/`V1`/`V2`... 或 `R1`/`R2`...） |
| `doc_type` | enum | `project-plan` / `tech-design` / `code-audit` / `diagnostic` / `changelog` / `branch-review` / `project-review` / `visualization` / `index` |
| `status` | enum | `draft` / `final` / `archived` / `superseded` |
| `scope` | enum | `project` / `branch` / `module` |
| `audited_commit` | string \| null | 审计/复审对象的 git 提交哈希，可空 |
| `branch` | string \| null | 审计/复审对象所在分支，可空 |
| `source` | enum | `human` / `chatgpt` / `codex-self-review` / `codex` |
| `created` | date | ISO 日期（YYYY-MM-DD） |
| `last_reviewed` | date | ISO 日期（YYYY-MM-DD） |
| `supersedes` | slug \| null | 本版替代的上版 slug，可空 |
| `superseded_by` | slug \| null | 替代本版的下版 slug，可空 |
| `related` | slug[] | 关联文档的 slug 列表，可空 |

## 命名规范

- 文件名格式：`<Vn\|Rn>-<doc_type>-<描述英文>.<ext>`
- 全部使用 ASCII 字符 + 连字符（`-`），避免空格与中文，方便跨平台与 Git 处理
- 新建文档前必须先确定 `version` 与 `status`，并在 front matter 中标注
- `.html` 文档不使用 YAML，而是在 `<head>` 内使用 `<meta name="doc:*">` 与 `application/ld+json` 等价表达
