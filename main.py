"""命令行入口。
用法：
    python main.py                                  # 交互模式
    python main.py "解释 demo/sample_buggy.py"       # 单次提问
    python main.py -w E:\\some\\repo "解释 main.py"  # 指定工作目录
    python main.py --verbose                        # 打印工具完整输出
    python main.py --check                          # 环境自检

"""

import argparse
import sys
from pathlib import Path

# 允许以 `python main.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.core import Agent
from agent.roles import ROLES,AgentRole, get_role, register_role
from agent.prompts import WELCOME,build_system_prompt
from config import describe, load_settings
from tools import invoke as invoke_tool
from tools.loader import load_all

# --------------------------------------------------------------- 终端着色
_COLORS = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "cyan": "\033[36m", "green": "\033[32m", "yellow": "\033[33m",
    "red": "\033[31m", "magenta": "\033[35m",
}

def _supports_color() -> bool:
    # 重定向输出时关闭颜色，避免日志文件里全是转义序列
    return sys.stdout.isatty()


def colorize(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


class EventRenderer:

    def __init__(self, verbose: bool = False, show_full_tool_output: bool = False) -> None:
        self.verbose = verbose
        self.show_full_tool_output = show_full_tool_output

    def __call__(self, event: dict) -> None:
        etype = event.get("type")

        if etype == "step":
            steps = colorize(f"[{event['step']}/{event['max_steps']}]", "dim")
            print(f"\n{steps} {colorize(event['text'], 'cyan')}")

        elif etype == "tool_call":
            args = event.get("arguments", {})
            args_text = ", ".join(f"{k}={v!r}" for k, v in args.items())
            if not self.verbose and len(args_text) > 160:
                args_text = args_text[:160] + " …"
            print(colorize(f"  ⚙ 调用工具 {event['name']}({args_text})", "magenta"))

        elif etype == "tool_result":
            text = event.get("text", "")
            lines = text.splitlines()
            print(colorize("  ↳ 结果：", "dim"))
            if self.show_full_tool_output or self.verbose:
                for line in lines:
                    print(colorize(f"     {line}", "dim"))
            else:
                for line in lines[:6]:
                    print(colorize(f"     {line}", "dim"))
                if len(lines) > 6:
                    print(colorize(f"     …（共 {len(lines)} 行，用 --verbose 或 /full 看全部）", "dim"))

        elif etype == "tool_error":
            print(colorize(f"  ✗ 工具调用失败 [{event['name']}]", "red"))
            print(colorize(f"     {event.get('text', '')[:300]}", "red"))

        elif etype == "retry":
            print(colorize(f"  ⟳ {event['text']}", "yellow"))

        elif etype == "info":
            print(colorize(f"  ℹ {event['text']}", "dim"))

        elif etype == "final":
            print()
            print(colorize("─" * 68, "dim"))
            print(event["text"])
            print(colorize("─" * 68, "dim"))

        elif etype == "error":
            print(colorize(f"\n[错误] {event['text']}", "red"), file=sys.stderr)

        elif self.verbose:
            print(colorize(f"  [event] {event}", "dim"))


# --------------------------------------------------------------- 交互模式
def interactive(agent: Agent, renderer: EventRenderer) -> int:
    settings = agent.settings
    print(colorize("=" * 68, "dim"))
    print(colorize("  代码解释 Agent  ·  Code Explanation Agent", "bold"))
    print(colorize("=" * 68, "dim"))
    print(colorize(f"  {describe(settings)}", "dim"))

    if not settings.has_api_key:
        print(colorize(
            "\n  [警告] 未检测到有效的 LLM_API_KEY。\n"
            "         请复制 .env.example 为 .env 并填入 Key。\n"
            "         获取地址：https://platform.deepseek.com/api_keys\n"
            "         （仍可使用 /symbols、/files、/tools 等不发请求的本地命令）",
            "yellow",
        ))

    print(WELCOME)

    while True:
        try:
            raw = input(colorize("\n你 > ", "green")).strip()
        except (EOFError, KeyboardInterrupt):
            print(colorize("\n再见。", "dim"))
            return 0

        if not raw:
            continue

        # ---- 本地命令
        if raw.startswith("/"):
            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in {"/quit", "/exit", "/q"}:
                path = agent.memory.save()
                if path:
                    print(colorize(f"会话已保存：{path}", "dim"))
                print(colorize("再见。", "dim"))
                return 0

            if cmd == "/help":
                print(WELCOME)
                continue

            if cmd == "/clear":
                agent.clear()
                print(colorize("已清空对话记忆。", "dim"))
                continue

            if cmd == "/usage":
                print(colorize(agent.usage_text(), "dim"))
                continue

            if cmd == "/tools":
                load_all()
                for schema in agent.tools:
                    fn = schema["function"]
                    print(colorize(f"  · {fn['name']}: {fn['description'][:70]}…", "dim"))
                continue

            if cmd == "/files":
                # 直接调工具，不经过 LLM —— 开发调试时非常省
                print(invoke_tool("list_dir", {"path": arg or "."}))
                continue

            if cmd == "/symbols":
                if not arg:
                    print(colorize("用法：/symbols <文件路径>", "yellow"))
                    continue
                print(invoke_tool("list_symbols", {"path": arg}))
                continue

            if cmd == "/full":
                renderer.show_full_tool_output = not renderer.show_full_tool_output
                state = "开启" if renderer.show_full_tool_output else "关闭"
                print(colorize(f"工具完整输出已{state}。", "dim"))
                continue

            if cmd == "/load":
                ok = agent.memory.load()
                print(colorize("已载入上次会话。" if ok else "没有可载入的会话。", "dim"))
                continue

            print(colorize(f"未知命令：{cmd}，输入 /help 查看可用命令。", "yellow"))
            continue

        # ---- 正常提问
        result = agent.ask(raw)
        if result.error is None:
            print(colorize(
                f"  · 用时 {result.elapsed_seconds:.1f}s | "
                f"{result.steps} 步 | {len(result.tool_invocations)} 次工具调用",
                "dim",
            ))


# --------------------------------------------------------------- 自检
def self_check(settings, agent: Agent) -> int:
    """离线自检：不需要 API Key 也能验证环境是否正确。"""
    print(colorize("环境自检", "bold"))
    print(f"  {describe(settings)}")
    print(f"  .env 加载：{'成功' if settings.dotenv_loaded else '未找到（只读系统环境变量）'}")
    print(f"  工作目录存在：{settings.workspace.exists()}")
    print(f"  已装载工具 {len(agent.tools)} 个：")
    for schema in agent.tools:
        print(f"    · {schema['function']['name']}")

    print("\n  工具冒烟测试（不发 API 请求）：")
    out = invoke_tool("list_dir", {"path": "."})
    print("    list_dir('.') ->", out.splitlines()[0])

    print("    路径越权防护 ->", end=" ")
    attack = invoke_tool("read_code", {"path": "../../../../Windows/System32/drivers/etc/hosts"})
    print(attack.splitlines()[0])

    # 判断防护是否真的生效
    blocked = "拒绝访问" in attack

    print("\n  结果：", end="")
    if not settings.has_api_key:
        print(colorize("工具层就绪，但缺少 API Key，无法调用模型。", "yellow"))
    elif not blocked:
        print(colorize("路径越权防护未生效！检查 resolve_workspace_path", "red"))
    else:
        print(colorize("环境就绪，可以开始使用。", "green"))

    return 0 if (settings.has_api_key and blocked) else 2


# --------------------------------------------------------------- 主函数
EXPLAINER_ROLE = "explainer"

def ensure_explainer_role(settings, max_steps: int) -> None:
    """注册 explainer 角色。必须在这里注册，因为 prompt 依赖真实工作目录。

    ★ 幂等保护：main() 可能被调用两次（测试、REPL），而 register_role
      对重名是直接抛错的。已经注册过就只更新 max_steps。
    """
    prompt = build_system_prompt(settings.workspace)
    tools = ("list_dir", "list_symbols", "read_code",
             "search_code", "run_python", "write_report")
    if EXPLAINER_ROLE in ROLES:
        existing = ROLES[EXPLAINER_ROLE]
        if existing.prompt == prompt and existing.max_steps == max_steps:
            return
        ROLES[EXPLAINER_ROLE] = AgentRole(
            key=EXPLAINER_ROLE, title=existing.title, prompt=prompt,
            allowed_tools=existing.allowed_tools,
            temperature=existing.temperature, max_steps=max_steps,
        )
        return
    register_role(AgentRole(
        key=EXPLAINER_ROLE,
        title="代码解释助手",
        prompt=prompt,
        allowed_tools=tools,
        temperature=0.2,
        max_steps=max_steps,
    ))
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-agent",
        description="代码解释 Agent —— 基于 DeepSeek 的工具调用型代码理解助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python main.py\n"
            '  python main.py "解释 demo/sample_buggy.py"\n'
            '  python main.py --workspace ../myproject "解释 src/main.py"\n'
        ),
    )
    parser.add_argument("question", nargs="*", help="要提问的问题；省略则进入交互模式")
    parser.add_argument("-w", "--workspace", default=None,
                        help="Agent 可访问的工作目录（默认当前目录），也是路径安全边界")
    parser.add_argument("--max-steps", type=int, default=12,
                        help="单次提问最多推理步数，防止死循环（默认 12）")
    parser.add_argument("--verbose", action="store_true", help="打印工具调用的完整结果")
    parser.add_argument("--no-persist", action="store_true", help="不把会话保存到 .agent_memory/")
    parser.add_argument("--check", action="store_true", help="仅做环境自检后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.workspace and not Path(args.workspace).exists():
        print(colorize(f"[错误] 工作目录不存在：{args.workspace}", "red"), file=sys.stderr)
        return 2

    settings = load_settings(args.workspace)
    if not settings.workspace.exists():
        print(colorize(f"[错误] 工作目录不存在：{settings.workspace}", "red"), file=sys.stderr)
        return 2

    load_all()
    renderer = EventRenderer(verbose=args.verbose)
    steps = max(1, args.max_steps)
    ensure_explainer_role(settings, steps)
    agent = Agent(
        settings,
        get_role(EXPLAINER_ROLE),
        on_event=renderer,
        persist_memory=not args.no_persist,
    )
    if args.check:
        return self_check(settings, agent)

    question = " ".join(args.question).strip()
    if not question:
        return interactive(agent, renderer)

    result = agent.ask(question)
    if result.error:
        return 1
    print(colorize(
        f"\n  · 用时 {result.elapsed_seconds:.1f}s | "
        f"{result.steps} 步 | {len(result.tool_invocations)} 次工具调用",
        "dim",
    ))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())