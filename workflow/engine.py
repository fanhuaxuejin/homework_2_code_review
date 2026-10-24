"""编排引擎：顺序执行 + 条件分支 + 循环 + 失败降级"""
import time
from datetime import datetime
from pathlib import Path

from agent.core import Agent
from agent.roles import get_role
from workflow.logger import RunLogger
from workflow.node import (
    NODES, Runtime, build_workflow, get_node, graph_mermaid, run_publish,
)
from workflow.state import WorkflowState, new_run_id

MAX_HOPS = 20          # 最多循环 20 次

class WorkflowEngine:
    def __init__(self, settings, *, entry: str = "review",
                 runs_root: Path | None = None) -> None:
        self.settings = settings
        self.entry = build_workflow(entry)
        self.runs_root = runs_root or (settings.workspace / "runs")
        self._agents: dict[str, Agent] = {}
        self._logger: RunLogger | None = None

    # Agent 管理
    def _agent(self, role_key: str) -> Agent:
        """按role加载"""
        if role_key not in self._agents:
            role = get_role(role_key)
            self._agents[role_key] = Agent(
                self.settings, role,
                on_event=lambda e: (self._logger.agent_event(e)
                                    if self._logger else None),
            )
        return self._agents[role_key]

    # 主循环
    def run(self, targets: list[str]) -> WorkflowState:
        run_id = new_run_id()
        run_dir = self.runs_root / run_id
        self._logger = RunLogger(run_dir)
        rt = Runtime(self.settings, self._agent, run_dir,self._logger)

        state = WorkflowState(
            run_id=run_id,
            targets=[t.replace("\\", "/") for t in targets],
            max_fix_rounds=self.settings.max_fix_rounds,
        )
        self._logger.write("run_start", run_id=run_id, targets=state.targets)

        node_key: str | None = self.entry
        hops = 0
        try:
            while node_key and hops < MAX_HOPS:
                hops += 1
                node = get_node(node_key)
                state.current_node = node.key
                self._run_node(node, state, rt)
                node_key = self._next(node, state)
        finally:
            if hops >= MAX_HOPS:
                self._logger.write("run_warn", text=f"达到最大跳转次数 {MAX_HOPS}")
            # 保证无论如何都有一份报告
            if not state.finished:
                try:
                    run_publish(state, rt)
                except Exception as exc:
                    self._logger.write("run_error", text=f"publish 失败：{exc}")
            self._logger.write("run_end", summary=state.summary())
            self._logger.dump_issues(state.original_issues, "review")
            self._logger.snapshot(state)
            self._logger.close()

        return state

    # 单节点执行
    def _run_node(self, node, state: WorkflowState, rt: Runtime) -> None:
        for attempt in range(1, self.settings.node_retries + 1):
            started_at = datetime.now().strftime("%H:%M:%S")
            t0 = time.time()
            self._logger.node_start(node.key, attempt)
            try:
                note = node.run(state, rt) or ""
            except Exception as exc:
                elapsed = time.time() - t0
                err = f"{type(exc).__name__}: {exc}"
                self._logger.node_end(node.key, "failed", elapsed, error=err)
                if attempt < self.settings.node_retries:
                    time.sleep(2 ** attempt)           # 重试
                    continue
                # 重试耗尽 → 记录失败
                state.record(node.key, "failed", started_at, elapsed,
                             attempts=attempt, error=err)
                return
            else:
                elapsed = time.time() - t0
                self._logger.node_end(node.key, "ok", elapsed, note=note)
                state.record(node.key, "ok", started_at, elapsed,
                             attempts=attempt, note=note)
                return

    # 分支决策
    def _next(self, node, state: WorkflowState) -> str | None:
        """条件按声明顺序求值"""
        for target, predicate in node.conditions.items():
            try:
                if predicate(state):
                    return target
            except Exception as exc:                   # noqa: BLE001
                self._logger.write("condition_error", node=node.key,
                                   target=target, error=str(exc))
        return node.next_default

    # 图形输出
    @staticmethod
    def mermaid() -> str:
        return graph_mermaid()

    @staticmethod
    def describe_order() -> list[str]:
        return list(NODES)