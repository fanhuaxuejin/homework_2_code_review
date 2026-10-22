"""命令行入口。
用法：
    python cli.py run --target demo/sample_buggy_2.py
    python cli.py run --target a.py b.py --show-graph
    python cli.py run --target demo/sample_buggy_2.py --dry-run
    python cli.py graph
    python cli.py roles
"""
import argparse
import sys
from pathlib import Path

from agent.prompts import build_fix_prompt, build_review_prompt
from agent.roles import READONLY_TOOLS, WRITE_TOOLS, AgentRole, ROLES, register_role
from config import describe, load_settings
from workflow.engine import WorkflowEngine

PROJECT_ROOT = Path(__file__).resolve().parent

def setup_roles(workspace: Path) -> None:
    """注册全部角色"""
    if ROLES:
        return                                  # 幂等
    review_prompt = build_review_prompt(str(workspace))
    register_role(AgentRole(
        key="reviewer", title="代码审查员",
        prompt=review_prompt,
        allowed_tools=READONLY_TOOLS, temperature=0.0, max_steps=14,
    ))
    register_role(AgentRole(
        key="reviewer_verify", title="复审员",
        prompt=review_prompt + "\n\n# 本次是复审\n"
               "你要判断上一轮报告的问题在修复后是否真的消失；"
               "仍然存在的问题照常按同样格式报告。\n"
               "不要因为「应该已经修好了」就返回空数组——只报告你真实读到的问题。",
        allowed_tools=READONLY_TOOLS, temperature=0.0, max_steps=12,
    ))
    register_role(AgentRole(
        key="fixer", title="代码修复工程师",
        prompt=build_fix_prompt(str(workspace)),
        allowed_tools=WRITE_TOOLS, temperature=0.0, max_steps=10,
    ))

def _resolve_targets(workspace: Path, raw: list[str]) -> list[str]:
    out: list[str] = []
    for item in raw:
        p = Path(item)
        abs_path = p if p.is_absolute() else (workspace / p)
        if not abs_path.exists():
            print(f"[错误] 文件不存在：{abs_path}", file=sys.stderr)
            raise SystemExit(2)
        try:
            out.append(abs_path.resolve().relative_to(workspace.resolve()).as_posix())
        except ValueError:
            print(f"[错误] {abs_path} 不在工作目录内", file=sys.stderr)
            raise SystemExit(2)
    return out

def cmd_run(args) -> int:
    workspace = Path(args.workspace).resolve() if args.workspace else PROJECT_ROOT
    settings = load_settings(str(workspace))
    print(f"[config] {describe(settings)}")

    setup_roles(workspace)
    targets = _resolve_targets(workspace, args.target)
    engine = WorkflowEngine(settings, runs_root=workspace / "runs")

    if args.dry_run:
        print("\n[dry-run] 执行计划：")
        print(engine.mermaid())
        print("\n节点顺序：", " → ".join(engine.describe_order()))
        print("未调用 LLM，未产生任何文件。")
        return 0

    if not settings.has_api_key:
        print("[错误] 未配置 API Key", file=sys.stderr)
        return 3

    if args.show_graph:
        print("\n[workflow] 流程图：")
        print(engine.mermaid())

    print(f"\n[workflow] 开始运行，目标：{targets}")
    state = engine.run(targets)

    print("\n" + "=" * 62)
    for r in state.history:
        flag = "✓" if r.status == "ok" else "✗"
        print(f"  {flag} {r.node:<9} {r.elapsed:>6.2f}s  重试{r.attempts}  "
              f"{(r.note or r.error or '')[:52]}")
    print("=" * 62)
    print(state.summary())
    if state.unresolved:
        print(f"\n⚠️  {len(state.unresolved)} 条问题未解决：")
        for i in state.unresolved:
            print(f"   - {i.short()}")
    print(f"\n报告：{state.report_path}")
    print(f"日志：{workspace / 'runs' / state.run_id}")
    return 0

def cmd_graph(args) -> int:
    setup_roles(PROJECT_ROOT)
    print(WorkflowEngine(load_settings(str(PROJECT_ROOT))).mermaid())
    return 0

def cmd_roles(args) -> int:
    setup_roles(PROJECT_ROOT)
    print("已注册角色：\n")
    for role in ROLES.values():
        print(f"  {role.key:<18} {role.title}")
        print(f"      tools = {', '.join(role.allowed_tools)}")
        print(f"      temperature={role.temperature}  max_steps={role.max_steps}\n")
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli", description="代码审查 Workflow MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行审查流程")
    run.add_argument("--target", nargs="+", required=True)
    run.add_argument("--workspace", default=None)
    run.add_argument("--show-graph", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)

    sub.add_parser("graph", help="输出 Mermaid 流程图").set_defaults(func=cmd_graph)
    sub.add_parser("roles", help="列出角色").set_defaults(func=cmd_roles)
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())