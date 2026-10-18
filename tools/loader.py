from typing import Any

_LOADED: set[str] = set()
check=False

_MODULES = {
    "list_dir": "tools.list_dir",
    "list_symbols": "tools.list_symbols",
    "read_code": "tools.read_code",
    "search_code": "tools.search_code",
    "run_python": "tools.run_python",
    "write_report": "tools.write_report",
    "write_code": "tools.write_code",
}

def load_all(allowed:tuple[str,]|None=None) -> None:
    """导入全部工具模块（幂等）。"""
    global check
    targets = _MODULES if allowed is None else {
        k: v for k, v in _MODULES.items() if k in allowed
    }
    for name, module_path in targets.items():
        if name in _LOADED:
            continue
        __import__(module_path)
        _LOADED.add(name)
    check=True

def checked()->bool:
    return check

def schemas(allowed:tuple[str,]|None=None) -> list[dict[str, Any]]:
    """返回所有已注册工具的 schema。"""
    load_all(allowed)
    from tools import all_schemas

    all_of = all_schemas()
    if allowed is None:
        return all_of

    allow = set(allowed)
    unknown = allow - {s["function"]["name"] for s in all_of}
    if unknown:
        raise ValueError(f"白名单里有未注册的工具：{sorted(unknown)}")
    return [s for s in all_of if s["function"]["name"] in allow]
