"""节点定义与注册表"""
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from agent.core import Agent
from agent.parsing import parse_issues, parse_verdicts
from workflow.state import Issue, WorkflowState

RunFn = Callable[[WorkflowState, "Runtime"], str]
Condition = Callable[[WorkflowState], bool]

# 一轮最多修几条，防止上下文爆炸
MAX_ISSUES_PER_ROUND = 6

@dataclass(frozen=True)
class Node:
    key: str
    title: str
    run: RunFn
    role: str | None = None
    reads: tuple[str, ...] = ()                    # 从state读什么
    writes: tuple[str, ...] = ()                   # 往state写什么
    conditions: dict[str, Condition] = field(default_factory=dict)
    next_default: str | None = None

NODES: dict[str, Node] = {}

def register(node: Node) -> Node:
    if node.key in NODES:
        raise ValueError(f"节点名重复：{node.key}")
    NODES[node.key] = node
    return node

def get_node(key: str) -> Node:
    if key not in NODES:
        available = ", ".join(sorted(NODES)) or "（无）"
        raise KeyError(f"未注册的节点 {key!r}。已注册：{available}")
    return NODES[key]

class Runtime:
    """运行时提供Agent获取、补丁目录、失败记录。"""

    def __init__(self, settings, agent_factory, run_dir: Path,logger=None) -> None:
        self.settings = settings
        self.agent_factory = agent_factory
        self.run_dir = run_dir
        self.read_files: set[str] = set()

        if logger is None:
            class _NullLogger:
                def write(self, *a, **k): pass
                def snapshot(self, *a, **k): pass
                def dump_issues(self, *a, **k): pass
                def node_start(self, *a, **k): pass
                def node_end(self, *a, **k): pass
                def agent_event(self, *a, **k): pass
                def close(self): pass
            logger = _NullLogger()
        self.logger = logger

    def agent(self, role_key: str):
        return self.agent_factory(role_key)

    def note_failure(self, issue: Issue, why: str) -> None:
        """把失败原因挂到 issue上"""
        issue.suggestion = (issue.suggestion + f"\n[流程记录] {why}").strip()
        print(f"[workflow] {issue.id} 处理失败：{why}", file=sys.stderr)

# 实现
def run_review(state: WorkflowState, rt: Runtime) -> str:
    agent = rt.agent("reviewer")
    prompt = _task_review(state)
    result = agent.ask(prompt)

    if result.error:
        state.issues = []
        return f"审查失败，已降级：{result.error[:120]}"

    issues, errors = parse_issues(result.answer, rt.settings.workspace)

    # 自纠正，把校验错误反馈给模型
    if errors and not issues:
        retry = agent.ask(
            "你上一条回复的格式有问题，请只重新输出一个合法的 ```json 代码块。\n"
            "具体问题：\n" + "\n".join(f"- {e}" for e in errors[:6])
        )
        issues, errors = parse_issues(retry.answer, rt.settings.workspace)

    if state.fix_round == 0:
        state.original_issues = list(issues)
    state.issues = issues
    rt.read_files.update(state.targets)

    note = f"发现 {len(issues)} 条问题"
    if errors:
        note += f"（{len(errors)} 条格式告警）"
    return note

def run_fix(state: WorkflowState, rt: Runtime) -> str:
    agent = rt.agent("fixer")
    agent.clear()
    pending = [i for i in state.sorted_issues()
               if i.status == "open" and i.is_blocking()]

    if not pending:
        return "无可修复项（无 blocker/major）"

    batch = pending[:MAX_ISSUES_PER_ROUND]
    by_file: dict[str, list[Issue]] = {}
    for issue in batch:
        by_file.setdefault(issue.file, []).append(issue)

    produced = 0
    failed = 0

    for file, group in by_file.items():
        base=state.patches.get(file,file)
        try:
            result = agent.ask(_task_fix_group(file, group, rt,base))
        except Exception as exc:                       # noqa: BLE001
            for i in group:
                i.status = "unresolved"
                rt.note_failure(i, f"修复调用异常：{type(exc).__name__}: {exc}")
            failed += len(group)
            continue

        if result.error:
            for i in group:
                i.status = "unresolved"
                rt.note_failure(i, f"修复失败：{result.error[:120]}")
            failed += len(group)
            continue

        patched = _extract_patched_path(result.tool_invocations)
        if patched:
            state.patches[file] = patched      # 每个文件一条
            produced += 1
            for i in group:
                i.status = "fixed"
        else:
            for i in group:
                i.status = "unresolved"
                rt.note_failure(i, "模型未调用 write_code，未产出补丁")
            failed += len(group)

    state.fixed_files = sorted(state.patches.values())
    state.fix_round += 1
    return (f"{len(batch)} 条分 {len(by_file)} 个文件修复，"
            f"产出 {produced} 个补丁，失败 {failed}，第 {state.fix_round} 轮")

def run_verify(state: WorkflowState, rt: Runtime) -> str:
    """复审：重新审查 patched/ 里的补丁文件"""
    if not state.patches:
        return "无补丁可复审，跳过"

    agent = rt.agent("reviewer_verify")
    confirmed = 0
    reopened = 0
    unjudged = 0

    for source, patched in state.patches.items():
        result = agent.ask(_task_verify(patched, source, state))
        if result.error:
            continue
        new_issues, _ = parse_issues(result.answer, rt.settings.workspace)
        verdicts = parse_verdicts(result.answer)
        for issue in state.issues:
            if issue.file != source or issue.status != "fixed":
                continue
            if issue.id in verdicts:
                resolved, evidence = verdicts[issue.id]
                issue.verify_note = evidence
                if resolved:
                    confirmed += 1
                else:
                    issue.status = "open"
                    reopened += 1
                continue
            unjudged += 1
            if _still_present(issue, new_issues):
                issue.status = "open"        # 修了但没修好，退回下一轮
                reopened += 1
            else:
                confirmed += 1
        rt.logger.write("verify_verdicts", source=source, patched=patched,
                        verdicts={k: {"resolved": v[0], "evidence": v[1][:200]}
                                  for k, v in verdicts.items()})
    note = (f"复审 {len(state.patches)} 个补丁：本轮确认 {confirmed} 条，"
            f"本轮遗留 {reopened} 条")
    if unjudged:
        note += f"（{unjudged} 条模型未给判定，用启发式兜底）"
    return note


def run_publish(state: WorkflowState, rt: Runtime) -> str:
    """生成 Markdown 报告。"""
    state.unresolved = [i for i in state.issues
                        if i.status in ("open", "unresolved")]
    for i in state.issues:
        if i.status == "open":
            i.status = "unresolved"

    lines = [
        f"# 代码审查报告 {state.run_id}",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 审查目标：{', '.join(state.targets)}",
        f"- 问题总数：{len(state.issues)}（未解决 {len(state.unresolved)}）",
        f"- 修复轮次：{state.fix_round} / 上限 {state.max_fix_rounds}",
        "",
        "## 节点执行记录",
        "",
        "| 节点 | 状态 | 耗时(s) | 重试 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for r in state.history:
        note = (r.note or r.error or "").replace("|", "\\|")[:70]
        lines.append(f"| {r.node} | {r.status} | {r.elapsed} | {r.attempts} | {note} |")

    lines += ["", "## 问题清单", "",
              "| ID | 严重度 | 类别 | 位置 | 标题 | 状态 | 行号可信 |",
              "|---|---|---|---|---|---|---|"]
    for i in state.sorted_issues():
        lines.append(
            f"| {i.id} | {i.severity} | {i.category} | {i.file}:{i.line_start}-{i.line_end} "
            f"| {i.title} | {i.status} | {'是' if i.line_verified else '否'} |"
        )

    if state.patches:
        lines += ["", "## 修复产物", ""]
        for src, dst in state.patches.items():
            lines.append(f"- `{src}` → `{dst}`")

    if state.unresolved:
        lines += ["", "## ⚠️ 未解决的问题", "",
                  "以下问题在本轮流程中未被确认修复，需要人工介入：", ""]
        for i in state.unresolved:
            lines.append(f"- **{i.title}**（{i.file}:{i.line_start}，{i.severity}）")
            lines.append(f"  - 原因：{i.reason}")
            if i.verify_note:
                lines.append(f"  - **复审判定**：{i.verify_note}")
    else:
        lines += ["", "## 结论", "", "所有已报告问题均已完成修复或复审通过。"]

    report = rt.run_dir / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    state.report_path = str(report)
    state.finished = True
    return f"报告已生成：{report.name}"


# 条件谓词
def has_blocking_issues(state: WorkflowState) -> bool:
    return state.has_blocking()

def always_true(state: WorkflowState) -> bool:
    return True

def still_has_blocking(state: WorkflowState) -> bool:
    """复审后仍有 blocker/major，且未达熔断阈值 → 回到 fix"""
    return state.fix_round < state.max_fix_rounds and state.has_blocking()

# 辅助
def _extract_patched_path(invocations: list[dict]) -> str | None:
    """解析补丁路径"""
    for inv in invocations:
        if inv.get("name") != "write_code":
            continue
        text = str(inv.get("result") or "")
        marker = "已写入补丁文件："
        if marker not in text:
            continue
        first = text.split(marker, 1)[1].splitlines()[0]
        path = first.split("（")[0].strip()
        if path.startswith("patched/"):
            return path
    return None

def _still_present(issue: Issue, new_issues: list[Issue]) -> bool:
    """判断原问题在复审结果里是否仍然存在"""
    import difflib

    for i in new_issues:
        if i.category != issue.category:
            continue
        ratio = difflib.SequenceMatcher(None, issue.title, i.title).ratio()
        if ratio >= 0.5:
            return True
        if abs(i.line_start - issue.line_start) <= 3:
            return True
    return False

# 任务描述构造
def _task_review(state: WorkflowState) -> str:
    lines = [
        f"请审查以下文件的代码质量：{', '.join(state.targets)}",
        f"（这是第 {state.fix_round + 1} 轮审查。）",
        "",
        "步骤：",
        "1. 用 list_symbols 了解文件结构",
        "2. 用 read_code 精读关键函数；文件较大时只读相关行区间",
        "3. 必要时用 run_python 验证你的怀疑（例如除零、边界条件）",
        "4. 严格按输出规范给出 JSON",
    ]
    if state.original_issues:
        lines += ["", "上一轮已报告的问题（若在代码里依然存在，请再次报告）："]
        lines += [f"- {i.short()}" for i in state.original_issues[:8]]
    return "\n".join(lines)

def _task_fix_group(file: str, group: list[Issue], rt: Runtime, base: str) -> str:
    lines = [f"请修复 `{file}` 中的以下 {len(group)} 个问题：", ""]
    for idx, issue in enumerate(group, 1):
        lines.append(
            f"{idx}. [{issue.severity}/{issue.category}] "
            f"L{issue.line_start}-{issue.line_end} {issue.title}"
        )
        lines.append(f"   原因：{issue.reason}")
        if issue.suggestion:
            lines.append(f"   建议：{issue.suggestion}")
        lines.append("")

    lines.append(f"★ 当前最新版本：`{base}`")
    if base != file:
        lines.append(
            f"  该文件**已经包含前几轮已确认的修复**。你必须保留它们，"
            f"只在它基础上追加本轮修复。"
        )
        lines.append(f"  绝对不要回到原始文件 `{file}` 重新开始——那会丢掉已修好的改动。")
    lines.append("")

    lines += [
        "要求：",
        f"1. 先用 read_code 读取 `{base}` 的完整内容（这是最新版本）",
        "2. 一次性修复以上全部问题，不要只修一部分、也不要漏掉任何一条",
        f"3. **保留 `{base}` 中已有的全部修复**，不要把已修好的地方改回原样",
        "4. 只改这些问题涉及的位置，不要顺手重构无关代码",
        f"5. 调用 write_code：source_path 用 `{file}`，content 传修复后的完整文件",
        f"6. 以上问题编号：{', '.join(i.id for i in group)}",
    ]
    if file in rt.read_files:
        lines.append("（该文件本轮已读过，但改之前请再确认当前内容）")
    return "\n".join(lines)

def _task_verify(patched: str, source: str, state: WorkflowState) -> str:
    targets = [i for i in state.issues if i.file == source
               and i.status == "fixed"]
    lines = [
        f"这是修复后的文件：{patched}",
        f"它对应原文件：{source}",
        "",
        f"请逐一判断下列 {len(targets)} 个原问题在修复后的文件里**是否真的解决了**：",
        "",
    ]
    for i in targets:
        lines.append(f"- **{i.id}**（L{i.line_start}-{i.line_end}，{i.severity}）{i.title}")
        lines.append(f"  原问题：{i.reason}")
    lines += [
        "",
        "同时按审查规范报告你在这个修复后文件里发现的其他问题。",
        "",
        "只输出一个 ```json 代码块，结构如下：",
        "",
        "```json",
        "{",
        '  "verdicts": [',
        '    {"id": "R-001", "resolved": true,',
        '     "evidence": "L12 已改为 with open(...) as f，句柄会正确关闭"},',
        '    {"id": "R-002", "resolved": false,',
        '     "evidence": "L28 仍直接对 created_at 做 timedelta 相加，未见类型转换"}',
        "  ],",
        '  "issues": [ ...仍然存在的问题，字段同审查规范... ]',
        "}",
        "```",
        "",
        "判定规则：",
        "- 必须对你读到的代码作出判断，`evidence` 要引用具体行号和改动",
        "- `resolved: true` 表示原问题描述的情形已不存在",
        "- **不要因为「可能已经修好了」就填 true**；也不要因为「代码风格不完全满意」就填 false",
        "- 如果原问题只是被部分修复（例如加了日志但仍吞异常），填 false 并说明",
        "- `issues` 里应该只放**新发现或仍存在**的问题，不要把已解决的问题重复列一遍",
    ]
    return "\n".join(lines)

# workflow定义
def build_workflow(entry: str = "review") -> str:
    """注册全部节点并返回入口。幂等：可重复调用（测试需要）。"""
    NODES.clear()
    register(Node(
        "review", "代码审查", run_review, role="reviewer",
        reads=("targets", "fix_round", "original_issues"),
        writes=("issues", "original_issues"),
        conditions={"fix": has_blocking_issues, "publish": always_true},
        next_default="publish",
    ))
    register(Node(
        "fix", "生成修复", run_fix, role="fixer",
        reads=("issues", "fix_round"),
        writes=("patches", "fixed_files", "fix_round"),
        next_default="verify",
    ))
    register(Node(
        "verify", "复审确认", run_verify, role="reviewer_verify",
        reads=("patches", "issues"),
        writes=("issues",),
        conditions={"fix": still_has_blocking},
        next_default="publish",
    ))
    register(Node(
        "publish", "输出报告", run_publish,
        reads=("issues", "history"),
        writes=("report_path", "unresolved", "finished"),
    ))
    return entry

def graph_mermaid() -> str:
    """把注册表渲染成流程图"""
    out = ["```mermaid", "flowchart TD"]
    for node in NODES.values():
        out.append(f'    {node.key}["{node.key}<br/>{node.title}"]')
    for node in NODES.values():
        for target in node.conditions:
            out.append(f"    {node.key} -->|{target}| {target}")
        if node.next_default:
            out.append(f"    {node.key} --> {node.next_default}")
    out.append("```")
    return "\n".join(out)