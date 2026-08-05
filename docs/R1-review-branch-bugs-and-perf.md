---
title: fix/bugs-and-perf 分支复审意见（R1）
slug: R1-review-branch-bugs-and-perf
version: R1
doc_type: branch-review
status: final
scope: branch
audited_commit: a0fa9c2
branch: fix/bugs-and-perf
source: codex-self
created: 2026-07-21
last_reviewed: 2026-07-22
supersedes: null
superseded_by: null
related:
  - R1-changelog-bugs-and-perf
---

# BUG 修复与性能提升分支复审意见

复审对象：`fix/bugs-and-perf`，HEAD `a0fa9c2`，相对首次审查的 `93792c3`。

## 结论

**代码层面可以合并。** 首次审查提出的 1 个 P0、3 个 P1、2 个 P2、1 个 P3 以及任务锁冗余均已修复，本次未发现新的阻断性问题。

## 首次审查问题复核

| 原问题 | 状态 | 复核结果 |
|---|---|---|
| P0：`asr_options` 传给错误方法 | 已修复 | 参数已移至 `whisperx.load_model(...)`，不再传给 `model.transcribe(...)`；新增调用契约测试。 |
| P1：新增转写参数未进入指纹 | 已修复 | `repetition_penalty`、`no_repeat_ngram_size`、`chunk_size` 均进入转写指纹；逐项变更测试通过。 |
| P1：缺失指纹被视为匹配 | 已修复 | 缺失指纹现在视为未验证，旧任务会从首个未验证阶段重跑。 |
| P1：缺少 aligned 时静默退回段级渲染 | 已修复 | 整体复用前会用 raw + speakers 重建 `transcript.aligned.json`。 |
| P2：FFmpeg 检查阻塞纯渲染 | 已修复 | 检查已移入 `process(...)`，`render(...)` 不再依赖 FFmpeg。 |
| P2：新增配置项缺少边界校验 | 已修复 | 三个新增参数均在模型加载前校验，并聚合输出错误。 |
| P3：空锁文件被立即判为残留 | 已修复 | 新空锁在 5 秒窗口内视为占用，旧空锁才回收；边界测试通过。 |
| 复杂度：全局锁与任务锁重复 | 已修复 | 已删除任务级锁，仅保留全局 GPU 锁。 |

## 验证结果

- `uv run --no-sync pytest`：**45 passed**。
- `uv run --no-sync ruff check .`：通过。
- `uv run --no-sync ruff format --check .`：13 files already formatted。
- `uv run pytest`：未进入测试，环境同步仍因 `.venv/Lib/site-packages/torchgen/selective_build` 被占用而失败。这是本机环境问题，不是本次代码回归；合并前建议释放占用后再执行一次标准命令。

## 复杂度复审（Ponytail）

Lean already. Ship.
