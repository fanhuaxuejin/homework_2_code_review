"""工具注册表：统一管理 Agent 能调用的工具。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from tools.loader import checked, load_all

_REGISTRY: dict[str, "Tool"] = {}

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    schema: dict[str, Any]

def register(schema: dict[str, Any]) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """把 handler 注册进工具表。

    工具名重复时直接报错，而不是静默覆盖 ——
    静默覆盖会让你以为工具已更新，实际跑的还是旧的。
    """
    def decorator(handler):
        fn = schema["function"]
        name = fn["name"]
        if name in _REGISTRY:
            raise ValueError(f"工具名重复：{name}")
        _REGISTRY[name] = Tool(
            name=name,
            description=fn.get("description", ""),
            parameters=fn.get("parameters", {}),
            handler=handler,
            schema=schema,
        )
        return handler
    return decorator

def all_schemas() -> list[dict[str, Any]]:
    """返回全部工具 schema，直接喂给 LLM 的 tools 参数。"""
    return [t.schema for t in _REGISTRY.values()]

def get(name: str) -> Tool | None:
    return _REGISTRY.get(name)

def invoke(name: str, arguments: dict[str, Any]) -> str:
    """按名字执行工具。

    这是"让模型自我纠正"的关键：**任何异常都转为可读文本返回，绝不抛出**。
    工具参数是模型生成的字符串，出错是常态。抛异常会直接打断 Agent 循环，
    返回文本则让模型有机会换个参数重试。
    """

    if not checked():
        load_all()

    tool = _REGISTRY.get(name)
    if tool is None:
        if not _REGISTRY:
            return (
                "[错误] 工具注册表为空 —— 这不是『工具不存在』，"
                "而是从未加载过任何工具模块。请先调用 tools.loader.load_all()。"
            )
        available = ", ".join(sorted(_REGISTRY)) or "（无）"
        return f"[错误] 不存在名为 {name!r} 的工具。可用工具：{available}"

    try:
        result = tool.handler(**arguments)
    except TypeError as exc:
        # 参数名/个数不对 —— 模型最常犯的错，把正确参数列出来提示它
        params = ", ".join(tool.parameters.get("properties", {})) or "（无）"
        return f"[错误] 调用 {name} 的参数不合法：{exc}。该工具接受的参数：{params}"
    except Exception as exc:
        return f"[错误] 执行 {name} 失败：{type(exc).__name__}: {exc}"

    return result if isinstance(result, str) else str(result)

class UnsafePathError(ValueError):
    """请求的路径越出了允许访问的工作目录。"""

def resolve_workspace_path(raw: str, workspace: Path) -> Path:
    """把模型给的路径解析为绝对路径，并校验未越权。

    **这是整个项目的安全命门**：工具参数是模型生成的字符串。
    不校验的话，一句 read_code(path="../../../../Windows/win.ini")
    就变成了任意文件读取漏洞。
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise UnsafePathError(f"路径无法解析：{raw}（{exc}）") from exc

    ws = workspace.resolve()
    # 注意用 parents 而不是字符串前缀比较：
    # 前缀比较在 Windows 上会被大小写和路径分隔符坑到
    if resolved != ws and ws not in resolved.parents:
        raise UnsafePathError(f"拒绝访问工作目录之外的路径：{resolved}（工作目录：{ws}）")
    return resolved