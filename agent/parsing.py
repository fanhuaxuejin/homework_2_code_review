"""把 LLM 的自由文本解析成结构化 Issue，并校验真实性"""
import json
import os
from pathlib import Path
from typing import Any,Iterator

from workflow.state import VALID_CATEGORIES, VALID_SEVERITIES, Issue

# 扫描源码行数时要跳过的目录
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".idea", ".vscode",
    "runs", "patched", ".agent_memory", ".deps", ".piptmp",
}

# 参与行号校验的源码后缀
_CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".ps1", ".sql",
}

def _candidate_chunks(text: str) -> list[str]:
    """先找合法JSON，找不到再全文"""
    chunks: list[str] = []
    for marker in ("```json", "```JSON", "```Json", "```"):
        idx = text.find(marker)
        if idx < 0:
            continue
        start = idx + len(marker)
        end = text.find("```", start)
        if end > start:
            chunks.append(text[start:end])
            break
    chunks.append(text)
    return chunks

def _balanced_from(text: str, start: int) -> str | None:
    """找花括号平衡的片段"""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None          # 没闭合

def iter_json_objects(text: str) -> Iterator[dict[str, Any]]:
    """依次找出文本里所有可解析的 JSON 对象。"""
    if not text:
        return
    for chunk in _candidate_chunks(text):
        pos = text.find("{")
        tries = 0
        while pos >= 0 and tries < 200:
            tries += 1
            block = _balanced_from(chunk, pos)
            if block is not None:
                try:
                    obj = json.loads(block)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    yield obj
            nxt = chunk.find("{", pos + 1)
            if nxt < 0:
                break
            pos = nxt

def extract_json_block(text: str) -> str | None:
    """返回第一个合法 JSON"""
    for obj in iter_json_objects(text):
        return json.dumps(obj, ensure_ascii=False)
    return None

def _as_int(value: Any) -> int | None:
    """容忍数字被写成字符串"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None

def _line_counts(workspace: Path) -> dict[str, int]:
    """总行数"""
    counts: dict[str, int] = {}
    workspace = workspace.resolve()
    for dirpath, dirnames, filenames in os.walk(workspace, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in _CODE_SUFFIXES:
                continue
            try:
                rel = path.relative_to(workspace).as_posix()
                counts[rel] = len(
                    path.read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except (OSError, ValueError):
                continue
    return counts

def parse_issues(text: str, workspace: Path) -> tuple[list[Issue], list[str]]:
    """解析，校验。返回 (issues, errors)。
    调用方决定是把 errors 记日志，还是回灌给模型重新输出。
    """
    errors: list[str] = []
    payload: dict[str, Any] | None = None
    for obj in iter_json_objects(text):
        if "issues" in obj:
            payload = obj
            break

    if payload is None:
        # 区分两种失败
        if extract_json_block(text) is None:
            return [], ["输出里找不到合法的 JSON 对象（可能被截断或未按格式输出）"]
        return [], ["找到了 JSON 对象，但缺少 issues 字段"]

    raw_issues = payload.get("issues")
    if not isinstance(raw_issues, list):
        return [], [f"issues 必须是数组，实际是 {type(raw_issues).__name__}"]
    if not raw_issues:
        return [], []

    line_counts = _line_counts(workspace)
    issues: list[Issue] = []
    for idx, raw in enumerate(raw_issues, start=1):
        prefix = f"第 {idx} 条"
        if not isinstance(raw, dict):
            errors.append(f"{prefix}不是对象，已跳过")
            continue

        # 文件
        file = str(raw.get("file") or "").strip().replace("\\", "/")
        if not file:
            errors.append(f"{prefix}缺少 file")
            continue
        if file not in line_counts:
            errors.append(f"{prefix}引用了无法读取的文件 {file!r}")
            continue

        # 行号
        ls, le = _as_int(raw.get("line_start")), _as_int(raw.get("line_end"))
        if ls is None or le is None:
            errors.append(f"{prefix}的 line_start/line_end 不是合法整数")
            continue
        if ls < 1 or le < ls:
            errors.append(f"{prefix}行号区间不合法：{ls}-{le}")
            continue

        # severity
        severity = str(raw.get("severity") or "").strip().lower()
        if severity not in VALID_SEVERITIES:
            errors.append(f"{prefix}的 severity {severity!r} 非法，降级为 minor")
            severity = "minor"

        # category
        category = str(raw.get("category") or "").strip().lower()
        if category not in VALID_CATEGORIES:
            errors.append(f"{prefix}的 category {category!r} 非法，降级为 maintainability")
            category = "maintainability"

        # title
        title = str(raw.get("title") or "").strip()
        if not title:
            errors.append(f"{prefix}缺少 title")
            continue

        # 真实性校验
        total = line_counts[file]
        if le > total:
            errors.append(
                f"{prefix}行号 {ls}-{le} 超出 {file} 的总行数 {total}，"
                "判定为幻觉并降级为 info"
            )
            severity = "info"
            ls = min(ls, total)
            le = min(le, total)
        issues.append(Issue(
            id=f"R-{idx:03d}",
            file=file,
            line_start=ls,
            line_end=le,
            severity=severity,
            category=category,
            title=title,
            reason=str(raw.get("reason") or "").strip(),
            suggestion=str(raw.get("suggestion") or "").strip(),
            line_verified=bool(raw.get("line_verified", False)),
        ))
    return issues, errors