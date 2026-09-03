# AGENTS.md

## 目标

构建 Windows 和 Apple Silicon macOS 本地会议音频处理流水线。OBS 只负责录音；本项目从已完成写入的音频文件开始处理。

## 第一版边界

- 先完成单文件 CLI，再做拖拽入口和目录监听。
- 不做会议纪要、GUI、实时字幕、分布式队列、Web 服务或自研录音。
- V1 全部本地处理，不上传音频或转写内容，也不引入总结模型依赖。

## 技术约束

- Python 3.12，使用 `uv` 管理环境和锁文件。
- Python 函数和方法必须写完整的参数与返回值类型；避免无理由使用 `Any`。
- 优先标准库：`argparse`、`pathlib`、`sqlite3`、`tomllib`、`logging`。
- 音频探测与转换调用 FFmpeg/ffprobe；转写与说话人识别使用 FunASR（SenseVoiceSmall + FSMN-VAD + CAM++）本地模型，单次综合分析。
- Windows 启动时先将 FFmpeg `bin` 和 PyTorch `lib` 注册为 DLL 搜索目录，再导入 Torch/FunASR。
- Windows + RTX 4060 8GB 使用 `cuda:0`；无 CUDA 的 Apple Silicon macOS 使用 CPU。设备必须记入转写指纹与日志。
- 模型串行加载；不要引入并行 GPU 任务。
- 新依赖必须解决当前需求；不得为未来功能预建抽象层。

## 编码与语言

- 自有文本文件必须显式使用 `encoding="utf-8"` 读写；二进制文件和第三方进程输出按实际格式处理。
- Python 标识符、模块名、配置字段和结构化日志字段使用英文。
- 用户可见的 CLI 提示、错误信息和项目文档使用中文；注释仅在代码无法自解释时添加，并使用中文。
- `docs/` 下的文档必须使用版次化命名 `<Vn|Rn>-<doc_type>-<描述>.<ext>`（纯 ASCII + 连字符），并在 `.md` 文档开头添加 YAML front matter；`.html` 文档在 `<head>` 内使用 `<meta name="doc:*">` 与 `application/ld+json` 等价表达。新建或重大修订文档前须确认版次与状态（`draft`/`final`/`archived`/`superseded`），字段定义见 [docs/README.md](docs/README.md)。

## 运行与测试

- Python 程序和测试统一通过 `uv run ...` 执行；环境同步使用 `uv sync`。
- 使用 pytest，测试文件统一放在 `tests/`，命名为 `test_*.py`。
- 运行相关测试的标准命令为 `uv run pytest`。
- 非平凡逻辑至少包含一个能够防止回归的测试；优先短测试和合成音频。

## 异常与日志

- 业务逻辑只捕获能够处理的具体异常，禁止静默吞掉异常。
- 仅允许在 CLI 或任务执行的最外层捕获 `Exception`，且必须使用 `logger.exception` 记录堆栈并返回失败状态。
- 正常进度和最终结果输出到 stdout；可操作的错误摘要输出到 stderr。
- 模型参数、阶段耗时、异常堆栈和排错信息写入任务目录的 `run.jsonl`。
- 日志不得包含访问令牌、完整环境变量或敏感会议内容。

## 数据与可靠性

- 原始录音只读，任何阶段不得覆盖或删除源文件。
- 每个任务以源文件 SHA-256 唯一标识，阶段产物可复用，重复执行保持幂等。
- 写输出先写临时文件，再原子重命名；失败必须保留日志和可恢复状态。
- 密钥只从环境变量读取，不写入仓库、日志或配置样例。
- 不提交真实会议音频、模型文件、缓存、`.venv` 或本机 OBS 配置。

## 变更要求

- 修复问题前追踪所有调用方，在共享根因处修复。
- 完成变更后运行相关检查，并同步 README/开发方案中已经失效的说明。
- 执行 `uv add`、更换核心模型、修改任务数据库结构、破坏 CLI 兼容性或进行跨模块重大重构前，必须先说明原因、影响和最小方案，等待用户确认。
- 用户在当前请求中已经明确授权的依赖或重构，无需重复确认。

## Agent skills

### Issue tracker

项目工作项使用 GitHub Issues，通过 `gh` CLI 管理。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用默认的五类 triage 标签。详见 `docs/agents/triage-labels.md`。

### Domain docs

项目采用 single-context 布局，领域词汇表位于根目录 `CONTEXT.md`。详见 `docs/agents/domain.md`。

### MeetingFlow Agent 调用

当任务需要在 Windows 本机转写已完成写入的会议音视频、轮询转写任务或读取结构化转录结果时，使用 `.agents/skills/meetingflow-agent/SKILL.md`。会议总结、实时字幕、录音控制和内部产物排错不使用该 Skill。
