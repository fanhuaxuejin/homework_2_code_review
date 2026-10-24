# Code Review Workflow

使用 Python 实现的代码解释、审查与修复原型。通过 OpenAI 兼容协议调用大语言模型，由自建 Agent 循环和工具注册表完成文件阅读、问题发现、补丁生成与复审。设计框架详见[设计文档](Design.md)。

## 功能

| 能力 | 实现情况 |
| --- | --- |
| 代码解释与追问 | `main.py` 支持单次提问及交互会话 |
| 代码审查工作流 | `cli.py` 按 review → fix → verify → publish 编排 |
| 结构分析 | Python 使用 AST；其他语言使用有限的正则规则粗提取 |
| 文件阅读与检索 | 支持带行号阅读、分段读取、跨文件正则搜索及多种文本编码 |
| 问题结构化 | 解析 JSON，校验文件、行号区间、严重度与类别 |
| 补丁生成 | `write_code` 将完整候选文件写入 `patched/`，保留原文件 |
| 多轮修复 | 当前默认最多 2 轮；后续轮次以最近补丁为基础 |
| 复审 | 模型逐条给出判定，缺少判定时使用问题标题与行号启发式匹配 |
| 运行产物 | Markdown 报告、JSONL 日志、问题记录和状态快照 |

代码读取支持 Python、JavaScript、TypeScript、C/C++、Java、Go 等后缀。

## 环境与安装

- Python 3.10 或以上；本次实际验证使用 Python 3.12.6。
- 依赖：`openai`、`python-dotenv`、`pytest`，版本约束见 `requirements.txt`。
- 发起模型请求需要 API Key 和网络

以下命令在 Windows PowerShell 中执行。**先进入 `code_review` 目录，并在该目录运行程序**：工具目前根据进程当前目录加载工作区，从其他目录启动可能与入口设置不一致。

```powershell
cd "你的路径\code_review"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```
复制env模板
```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，至少填写：

```dotenv
LLM_API_KEY=替换为你的真实密钥
LLM_PROVIDER=deepseek
LLM_MODEL=替换为你的服务端实际可用模型名
LLM_THINKING=1
```

目前 `PROVIDERS` 仅内置 `deepseek`，地址和默认模型由 `config.py` 定义；默认模型字符串为 `deepseek-flash`。请按你的服务配置 `LLM_MODEL`，并确认模型支持工具调用及所选思考参数。

## 快速开始

### 1. 离线查看流程

```powershell
python cli.py --help
python cli.py graph
python cli.py roles
python cli.py run --target demo/sample_buggy_2.py --dry-run
```

`--dry-run` 打印流程图与节点顺序，不调用模型，也不创建本次运行报告。对于 `run --target` 列表形式的输入，当前 dry-run 不逐个校验文件存在性。

### 2. 运行审查与修复

```powershell
python cli.py check
python cli.py run --target demo/sample_buggy_2.py
python cli.py run --target demo/sample_buggy.py demo/utils.py --graph
python cli.py run --target "demo/2026 online_1 L.cpp" --full
```

参数说明：

| 参数 | 含义 |
| --- | --- |
| `--target` | 一个或多个目标文件，相对于 `code_review`；含空格路径需加引号 |
| `--graph` | 执行前显示 Mermaid 流程图 |
| `--dry-run` | 仅查看计划 |
| `--full` | 显示 Agent 完整回答 |
| `--quiet` | 关闭过程事件显示，仍输出运行摘要 |

流程会自动尝试修复 `blocker` 和 `major`，没有单独的“只审查、不修复”命令开关。`minor` 与 `info` 默认保留供人工处理。生成的补丁需要人工比较、测试后再应用。

不带子命令进入交互模式：

```powershell
python cli.py
```

常用命令：

| 命令 | 作用 |
| --- | --- |
| `/run demo/sample_buggy_2.py` | 运行工作流；也可直接输入文件路径 |
| `/files` | 列出部分常见后缀的源文件 |
| `/report`、`/report <run-id>` | 查看最新报告或指定报告 |
| `/patched` | 列出补丁文件 |
| `/graph`、`/roles`、`/check` | 查看流程、角色与配置状态 |
| `/full` | 切换完整回答显示 |
| `/quit` | 退出 |

### 3. 解释代码

```powershell
python main.py "解释 demo/sample_buggy.py"
python main.py --verbose "normalize_record 在哪里被调用？"
python main.py
```

交互模式支持 `/files`、`/symbols <文件>`、`/tools`、`/usage`、`/clear`、`/load`、`/full`、`/quit`。解释模式默认在交互退出时保存会话到 `.agent_memory/last_session.json`，通过 `/load` 手动恢复；`--no-persist` 关闭保存。

`main.py` 还提供 `--max-steps`、`--check` 和 `-w/--workspace`。当前工具没有继承 `--workspace`，请让进程当前目录与指定工作区保持一致；暂不要依赖该选项隔离另一个仓库。`main.py --check` 虽不请求模型，但会构造模型客户端；缺少密钥时可能在检查前初始化失败。

## 配置说明

`config.py` 从项目根目录的 `.env` 加载配置，已有进程环境变量优先。

| 配置项 | 默认值 | 当前行为 |
| --- | --- | --- |
| `LLM_API_KEY` | 空 | 模型请求密钥；未设置时回退到 `<PROVIDER>_API_KEY`，当前为 `DEEPSEEK_API_KEY` |
| `LLM_PROVIDER` | `deepseek` | 未知值打印提示并回退到默认 provider |
| `LLM_MODEL` | provider 的默认模型 | 空值也使用默认值 |
| `LLM_THINKING` | provider 的能力默认值 | 接受 `1/0`、`true/false`、`yes/no`、`on/off` |
| `LLM_TIMEOUT` | `120` | 单次 API 请求超时秒数 |
| `LLM_MAX_RETRIES` | `4` | 实际是总尝试次数，包含首次请求 |
| `RUN_PYTHON_TIMEOUT` | `10` | Python 片段执行秒数，工具会限制到 1–30 秒 |

修复上限和节点总尝试次数均使用 `Settings` 的默认值 2。修改 Python 代码构造的 `Settings` 对象可以调整这两个值。

角色步数由 `cli.py` 注册角色时指定：初审 14、复审 12、修复 10；解释模式默认 12，可用 `--max-steps` 调整。当前没有总 token、总耗时或总费用预算。

## 输出文件

```text
code_review/
├── patched/                     # 候选完整文件
├── runs/
│   └── run-YYYYMMDD-HHMMSS/
│       ├── report.md            # 工作流报告
│       ├── run.jsonl            # 过程事件
│       ├── review.json          # 初审问题对象的最终序列化结果
│       └── state.json           # 运行结束后的状态快照
├── reports/                     # 解释助手调用 write_report 产生的文档
├── .agent_memory/               # 可选的解释会话记录
└── .agent_tmp/                  # 执行 Python 片段的临时脚本目录
```

`review.json` 当前受共享对象修改影响，不是不可变的初审快照。`state.json` 用于检查结果，尚未实现从快照恢复执行。

## 目录结构

```text
agent/
  core.py              # 推理与工具调用循环
  llm.py               # 模型适配、请求重试、响应解析
  memory.py            # 会话记录、长度估算、裁剪及保存
  parsing.py           # 问题与复审判定解析
  roles.py             # 角色定义和工具 schema 白名单
  prompts/             # 解释、审查、修复提示词
tools/                 # 工具装载、注册、路径检查与具体实现
workflow/
  state.py             # Issue、NodeRecord、WorkflowState
  node.py              # 节点注册、任务构造与节点逻辑
  engine.py            # 执行、分支、节点重试和兜底发布
  logger.py            # JSONL 日志与 JSON 快照
demo/                  # 演示样例，包含故意错误代码
tests/offline_test.py  # 现有离线冒烟脚本
cli.py                 # 审查工作流入口
main.py                # 解释助手入口
config.py              # provider 和运行设置
```

## 当前限制

使用时需要了解的边界：

- 审查失败可能被记录为成功节点，并生成零问题的成功结论。
- 复审失败、格式错误或缺少判定时，候选修复可能仍保持 `fixed`；复审新增问题没有合并入最终清单。
- `cli.py run` 在工作流有失败或未解决问题时仍可能返回 0，不可据此判断审查通过。
- 角色白名单目前只过滤传给模型的工具 schema，执行入口没有再次鉴权。
- `run_python` 是继承当前用户权限的子进程，超时和环境变量过滤不构成操作系统沙箱；只能在可信代码和受控运行环境中使用。
- 没有自动测试补丁、跨文件构建验证或自动应用补丁功能。
