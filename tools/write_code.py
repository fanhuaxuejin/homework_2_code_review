"""把修复后的文件写入 patched目录。"""
import re
import time
from pathlib import Path

from tools import register, resolve_workspace_path

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff.-]+")

# 进程内序号
_seq = 0

@register({
    "type": "function",
    "function": {
        "name": "write_code",
        "description": (
            "把修复后的完整文件内容写入 patched/ 目录（不覆盖原文件）。"
            "当你要落地修复结果时使用。"
            "source_path 给原文件的相对路径，content 给修复后的**完整**文件内容。"
            "注意：content 必须是整份文件，不是 diff 片段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "原文件相对路径，如 demo/sample_buggy.py",
                },
                "content": {
                    "type": "string",
                    "description": "修复后的完整文件内容（整份文件，不是片段）",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话说明改了什么",
                },
            },
            "required": ["source_path", "content"],
            "additionalProperties": False,
        },
    },
})
def write_code(source_path: str, content: str, reason: str = "") -> str:
    global _seq
    from config import load_settings

    settings = load_settings()

    if not content or not content.strip():
        return "[错误] content 为空，没有可写入的内容。"

    # ★ 校验源文件必须存在
    try:
        src = resolve_workspace_path(source_path, settings.workspace)
    except Exception as exc:
        return f"[错误] source_path 不合法：{exc}"

    if not src.is_file():
        return (
            f"[错误] 源文件不存在：{source_path}\n"
            "请先用 list_dir 或 read_code 确认真实路径，不要凭空构造文件名。"
        )

    # ★ 校验文件名
    raw = Path(source_path.strip().replace("\\", "/"))
    stem = _SAFE_NAME.sub("_", raw.stem).strip("._") or "patched"
    suffix = raw.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,6}", raw.suffix) else ".txt"

    stamp = time.strftime("%Y%m%d-%H%M%S")
    _seq += 1
    rel_path = f"patched/{stem}-{stamp}-{_seq:02d}{suffix}"

    # ★ 校验路径
    try:
        target = resolve_workspace_path(rel_path, settings.workspace)
    except Exception as exc:
        return f"[错误] {exc}"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"[错误] 写入失败：{exc}"

    return (
        f"已写入补丁文件：{rel_path}（{len(content.splitlines())} 行）\n"
        f"说明：{reason or '（未提供）'}\n"
        f"下一步：用 read_code 读取 {rel_path} 做最终确认。"
    )