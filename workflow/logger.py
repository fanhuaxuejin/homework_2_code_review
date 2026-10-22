"""执行日志"""
import json
from pathlib import Path
from typing import Any

from workflow.state import WorkflowState

# 单条文本上限
_MAX_TEXT = 400

class RunLogger:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "run.jsonl"
        self._fh = self.path.open("w", encoding="utf-8")
        self._node: str | None = None

    def node_start(self, node: str, attempt: int) -> None:
        self._node = node
        self.write("node_start", node=node, attempt=attempt)

    def node_end(self, node: str, status: str, elapsed: float,
                 note: str = "", error: str | None = None) -> None:
        self.write("node_end", node=node, status=status,
                   elapsed=round(elapsed, 2), note=note, error=error)

    # agent事件
    def agent_event(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        etype = payload.pop("type", "unknown")

        if etype == "tool_call":
            args = payload.get("arguments") or {}
            payload = {
                "name": payload.get("name"),
                "arguments_preview": json.dumps(args, ensure_ascii=False)[:_MAX_TEXT],
            }
        elif etype == "step":
            payload.pop("text", None)          # 跳过"正在推理…"
        elif etype in {"final", "info", "error", "tool_error"}:
            payload["text"] = str(payload.get("text", ""))[:_MAX_TEXT]

        self.write(f"agent.{etype}", node=self._node, **payload)

    # 落盘
    def write(self, event: str, **payload: Any) -> None:
        if self._fh.closed:
            return
        try:
            line = json.dumps({"event": event, **payload}, ensure_ascii=False)
        except TypeError:
            line = json.dumps({"event": event, "note": "payload 无法序列化"},
                              ensure_ascii=False)
        self._fh.write(line + "\n")
        self._fh.flush()          # 崩溃时也能留下现场

    def snapshot(self, state: WorkflowState, name: str = "state") -> Path:
        p = self.run_dir / f"{name}.json"
        p.write_text(json.dumps(state.snapshot(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    def dump_issues(self, issues, name: str = "review") -> Path:
        p = self.run_dir / f"{name}.json"
        p.write_text(json.dumps([i.__dict__ for i in issues], ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()