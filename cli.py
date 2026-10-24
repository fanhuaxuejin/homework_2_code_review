"""代码审查 Workflow —— 命令行入口
用法：
    python cli.py                                  # 交互模式
    python cli.py run --target demo/sample_buggy_2.py [--graph] [--dry-run] [--full]
    python cli.py graph | roles | check
"""
import argparse
import sys
from pathlib import Path

from agent.prompts import build_fix_prompt, build_review_prompt
from agent.roles import READONLY_TOOLS, ROLES, WRITE_TOOLS, AgentRole, register_role
from config import describe, load_settings
from workflow.engine import WorkflowEngine

ROOT = Path(__file__).resolve().parent

# 着色
_C = {"d": "\033[2m", "b": "\033[1m", "c": "\033[36m", "g": "\033[32m",
      "y": "\033[33m", "e": "\033[31m", "m": "\033[35m", "r": "\033[0m"}


def c(text: object, k: str) -> str:
    """着色"""
    return f"{_C[k]}{text}{_C['r']}" if sys.stdout.isatty() else str(text)


# ---------------------------------------------------------------- 渲染器
class Renderer:
    """渲染到终端。"""

    def __init__(self, full: bool = False) -> None:
        self.full = full

    def __call__(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "node_start":
            print(f"\n{c('▶', 'c')} {c(ev.get('title') or ev.get('node'), 'b')} 开始")
        elif t == "node_end":
            ok = ev.get("status") == "ok"
            detail = str(ev.get("note") or ev.get("error") or "")[:70]
            print(f"{c('✓' if ok else '✗', 'g' if ok else 'e')} "
                  f"{c(str(ev.get('node')) + ' 完成', 'b')} "
                  f"{c('%.2fs' % float(ev.get('elapsed') or 0), 'd')}  {detail}")
        elif t == "step":
            print(c(f"   [{ev.get('step')}/{ev.get('max_steps')}] 推理中…", "d"))
        elif t == "tool_call":
            args = ", ".join(f"{k}={v!r}" for k, v in (ev.get("arguments") or {}).items())
            print(c(f"   ⚙ {ev.get('name')}({args[:90]})", "m"))
        elif t == "info":
            text = str(ev.get("text") or "").strip()
            if text:
                print(c(f"   ℹ {text[:150]}", "d"))
        elif t == "final":
            text = str(ev.get("text") or "")
            print(text if self.full else c(f"   ↳ 回答 {len(text)} 字", "d"))
        elif t in ("error", "tool_error"):
            print(c(f"   [错误] {str(ev.get('text') or '')[:150]}", "e"))


# 角色
def setup_roles(ws: Path) -> None:
    """注册三个角色"""
    if "reviewer" in ROLES:
        return
    rp = build_review_prompt(str(ws))
    register_role(AgentRole("reviewer", "代码审查员", rp, READONLY_TOOLS, 0.0, 14))
    register_role(AgentRole(
        "reviewer_verify", "复审员",
        rp + "\n\n# 本次是复审\n"
             "判断上一轮报告的问题在修复后是否真的消失，仍存在的问题照常报告。\n"
             "不要因为「应该已经修好了」就返回空数组。",
        READONLY_TOOLS, 0.0, 12))
    register_role(AgentRole("fixer", "代码修复工程师", build_fix_prompt(str(ws)),
                            WRITE_TOOLS, 0.0, 10))

# 找文件
def find_targets(ws: Path, text: str) -> list[str]:
    """整段优先（支持含空格的文件名如 `2026 online_1 L.cpp`），其次逐词。"""
    for cand in [text.strip().strip("\"'")] + text.split():
        p = ws / cand
        if p.is_file():
            try:
                return [p.resolve().relative_to(ws.resolve()).as_posix()]
            except ValueError:
                continue
    return []

#  跑流程
def run_flow(settings, ws: Path, raw, *, renderer=None,
             graph: bool = False, dry: bool = False) -> int:
    setup_roles(ws)
    targets = raw if isinstance(raw, list) else find_targets(ws, raw)
    if not targets:
        print(c("没找到文件。直接输入路径即可，例如 demo/sample_buggy_2.py", "y"))
        return 2

    engine = WorkflowEngine(settings, runs_root=ws / "runs", on_event=renderer)
    if graph or dry:
        print(engine.mermaid())
        print(f"\n节点顺序：{' → '.join(engine.describe_order())}")
        if dry:
            print(c("dry-run：未调用 LLM，未产生文件。", "d"))
            return 0
    if not settings.has_api_key:
        print(c("[错误] 未配置 API Key", "e"), file=sys.stderr)
        return 3

    print(f"\n[workflow] 目标：{', '.join(targets)}")
    st = engine.run(targets)

    print("\n" + c("─" * 62, "d"))
    for r in st.history:
        print(f"  {'✓' if r.status == 'ok' else '✗'} {r.node:<9} {r.elapsed:>7.2f}s "
              f"重试{r.attempts}  {(r.note or r.error or '')[:50]}")
    print(c("─" * 62, "d"))
    print(c(st.summary(), "b"))
    if st.unresolved:
        print(c(f"\n⚠  {len(st.unresolved)} 条未解决：", "y"))
        for i in st.unresolved:
            print(c(f"   - {i.short()}", "d"))
    print(f"\n报告：{st.report_path}\n日志：{ws / 'runs' / st.run_id}")
    return 0

# 自检
def self_check(settings) -> int:
    print(c("环境自检", "b"))
    print(f"  {describe(settings)}")
    print(f"  已注册角色 {len(ROLES)} 个：{', '.join(ROLES)}")
    ok = settings.workspace.exists() and settings.has_api_key
    print("  结果：" + (c("环境就绪", "g") if ok else c("缺 API Key 或工作目录", "y")))
    return 0 if ok else 1

# 交互
HELP = """命令：
  /run <文件>   运行审查流程（也可直接输入文件名）
  /files        列出可审查的源文件
  /report [id]  查看报告（省略则最新）
  /patched      列出补丁产物
  /graph        打印 Mermaid 流程图
  /roles        列出角色与工具白名单
  /check        离线自检（不调 LLM）
  /full         切换「显示 Agent 完整回答」
  /quit         退出
"""

def repl(settings, ws: Path) -> int:
    setup_roles(ws)
    renderer = Renderer()
    print(c("  代码审查 Workflow  ·  Code Review Workflow", "b"))
    print(c(f"  {describe(settings)}", "d"))
    print(HELP)

    while True:
        try:
            raw = input(c("\n审查 > ", "g")).strip()
        except (EOFError, KeyboardInterrupt):
            print(c("\n再见。", "d"))
            return 0
        if not raw:
            continue

        cmd, _, arg = raw.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()

        if cmd in ("/quit", "/exit", "/q"):
            print(c("再见。", "d"))
            return 0
        elif cmd in ("/help", "/h", "/?"):
            print(HELP)
        elif cmd == "/full":
            renderer.full = not renderer.full
            print(c(f"完整回答显示：{'开' if renderer.full else '关'}", "d"))
        elif cmd == "/graph":
            print(WorkflowEngine(settings, runs_root=ws / "runs").mermaid())
        elif cmd == "/roles":
            for r in ROLES.values():
                print(f"  {c(r.key.ljust(18), 'c')} {r.title}")
                print(c(f"      {', '.join(r.allowed_tools)}", "d"))
        elif cmd == "/files":
            exts = {".py", ".cpp", ".c", ".h", ".js", ".ts", ".java", ".go"}
            skip = {".venv", ".git", "__pycache__", "runs", "patched", ".idea"}
            for p in sorted(ws.rglob("*")):
                rel = p.relative_to(ws).as_posix()
                if not p.is_file() or p.suffix not in exts:
                    continue
                if set(rel.split("/")) & skip:
                    continue
                n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                print(f"  {c(rel, 'c')}  {c(f'{n} 行', 'd')}")
        elif cmd == "/report":
            runs = ws / "runs"
            dirs = sorted((x for x in runs.iterdir() if x.is_dir()),
                          reverse=True) if runs.exists() else []
            if not dirs:
                print(c("还没有运行记录。", "y"))
                continue
            f = (runs / arg / "report.md") if arg else (dirs[0] / "report.md")
            print(f.read_text(encoding="utf-8") if f.exists()
                  else c("没有报告。", "y"))
        elif cmd == "/patched":
            d = ws / "patched"
            for p in (sorted(d.iterdir(), reverse=True) if d.exists() else []):
                print(f"  {c(p.name, 'c')}")
        elif cmd == "/check":
            self_check(settings)
        elif cmd == "/run":
            if arg:
                run_flow(settings, ws, arg, renderer=renderer)
            else:
                print(c("用法：/run <文件路径>", "y"))
        else:
            run_flow(settings, ws, raw, renderer=renderer)

# 入口
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli", description="代码审查 Workflow")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="运行审查流程")
    r.add_argument("--target", nargs="+", required=True)
    r.add_argument("--graph", action="store_true", help="先打印流程图")
    r.add_argument("--dry-run", action="store_true", help="只看计划，不调 LLM")
    r.add_argument("--full", action="store_true", help="打印 Agent 完整回答")
    r.add_argument("--quiet", action="store_true", help="不显示过程事件")
    for name in ("graph", "roles", "check"):
        sub.add_parser(name)

    a = p.parse_args(argv)
    ws = ROOT
    settings = load_settings(str(ws))

    if a.cmd is None:
        return repl(settings, ws)
    if a.cmd == "graph":
        setup_roles(ws)
        print(WorkflowEngine(settings, runs_root=ws / "runs").mermaid())
        return 0
    if a.cmd == "roles":
        setup_roles(ws)
        for x in ROLES.values():
            print(f"  {c(x.key.ljust(18), 'c')} {x.title}\n"
                  f"      {c(', '.join(x.allowed_tools), 'd')}")
        return 0
    if a.cmd == "check":
        setup_roles(ws)
        return self_check(settings)

    print(c(f"[config] {describe(settings)}", "d"))
    renderer = None if a.quiet else Renderer(a.full)
    return run_flow(settings, ws, a.target, renderer=renderer,
                    graph=a.graph, dry=a.dry_run)

if __name__ == "__main__":
    raise SystemExit(main())