"""
Agent 核心：输入 → 推理 → 工具调用 → 输出 的循环。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.llm import LLMClient, LLMError
from agent.roles import AgentRole
from config import Settings
from tools import invoke as invoke_tool
from tools.loader import schemas as load_tool_schemas
from agent.memory import ConversationMemory

# 单次提问内最多允许多少轮"推理+工具调用"
DEFAULT_MAX_STEPS = 12
# 单轮里最多执行多少个工具调用
MAX_TOOL_CALLS_PER_STEP = 6

EventCallback = Callable[[dict[str, Any]], None]

@dataclass
class AgentResult:
    """一次提问的完整结果，便于上层做测试断言或写报告。"""

    answer: str
    steps: int
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    stopped_by_limit: bool = False
    error: str | None = None


class Agent:

    def __init__(
        self,
        settings: Settings,
        role: AgentRole,
        *,
        on_event: EventCallback | None = None,
        persist_memory:bool=False,
    ) -> None:
        self.settings = settings
        self.role=role
        self.max_steps = role.max_steps
        self.on_event = on_event or (lambda _event: None)
        self.client = LLMClient(settings)
        self.tools = load_tool_schemas(role.allowed_tools)
        store_dir = (settings.workspace / ".agent_memory") if persist_memory else None
        self.memory = ConversationMemory(role.prompt, store_dir=store_dir)

    # ------------------------------------------------------------- 对外接口
    def ask(self, question: str) -> AgentResult:
        """提一个问题，跑完整个 Agent 循环，返回结果。"""
        started = time.time()
        self.memory.add_user(question)
        invocations: list[dict[str, Any]] = []

        try:
            for step in range(1, self.max_steps + 1):
                if self.memory.trim():
                    self._emit(
                        "info",
                        text=f"上下文超出预算，已裁剪较早的对话（第 {self.memory.trim_events} 次）",
                    )

                self._emit("step", step=step, max_steps=self.max_steps, text="正在推理…")

                reply = self.client.chat(self.memory.messages, self.tools,temperature=self.role.temperature,)

                # ---- 分支 A：模型要调工具
                if reply.wants_tools:
                    self.memory.add(reply.assistant_message)

                    # 有时候模型会同时给出"我要调工具"+一句说明
                    if reply.content.strip():
                        self._emit("info", text=reply.content.strip())

                    calls = reply.tool_calls[:MAX_TOOL_CALLS_PER_STEP]
                    for call in calls:
                        invocations.append(self._execute_tool_call(call))
                    continue

                # ---- 分支 B：模型给出最终答案
                answer = reply.content.strip()
                if not answer:
                    # 极端情况：既没有内容也没有工具调用
                    answer = (
                        "[提示] 模型返回了空内容。可能是问题过于笼统，"
                        "请补充具体文件路径或函数名后重试。"
                    )
                self.memory.add({"role": "assistant", "content": answer})
                self._emit("final", text=answer)
                return AgentResult(
                    answer=answer,
                    steps=step,
                    tool_invocations=invocations,
                    elapsed_seconds=time.time() - started,
                )

            # ---- 循环用尽仍未收敛
            fallback = (
                f"已达到最大推理步数（{self.max_steps} 步）仍未得出结论。\n"
                "可能原因：问题需要的信息超出工作目录范围，或目标文件不存在。\n"
                "建议：把问题范围缩小到具体文件/函数，或检查路径是否正确。"
            )
            self.memory.add({"role": "assistant", "content": fallback})
            self._emit("final", text=fallback)
            return AgentResult(
                answer=fallback,
                steps=self.max_steps,
                tool_invocations=invocations,
                elapsed_seconds=time.time() - started,
                stopped_by_limit=True,
            )

        except LLMError as exc:
            # LLM 彻底失败：给出可操作的排查建议
            hint = str(exc)
            if "401" in hint or "authentication" in hint.lower():
                hint += "\n[排查建议] 检查 .env 中的 LLM_API_KEY 是否正确、是否已过期。"
            elif "402" in hint or "balance" in hint.lower():
                hint += "\n[排查建议] 账户余额不足，请前往平台充值。"
            elif "404" in hint or "model" in hint.lower():
                hint += (
                    f"\n[排查建议] 模型名 {self.settings.model!r} 可能已下线或拼写有误。"
                    "请用 .env 的 LLM_MODEL 指定当前可用模型。"
                )
            self._emit("error", text=hint)
            return AgentResult(
                answer="",
                steps=0,
                tool_invocations=invocations,
                elapsed_seconds=time.time() - started,
                error=hint,
            )

    def clear(self) -> None:
        """清空对话记忆"""
        self.memory.clear()

    def usage_text(self) -> str:
        return self.memory.summary()

    # -------------------------------------------------------------- 内部
    def _execute_tool_call(self, call) -> dict[str, Any]:
        """执行一个工具调用，把结果写入消息列表，并返回调用记录。
        这是"让模型自我纠正"的地方：
            模型调用 read_code(path="utils.py") → [错误] 文件不存在
            模型看到错误 → 改用 list_dir(".") → 重新读取成功
        """
        if call.parse_error:
            result = (
                f"[错误] {call.parse_error}\n"
                f"请重新调用 {call.name}，并确保参数是合法的 JSON 对象。"
            )
            self._emit("tool_error", name=call.name, text=result)
        else:
            self._emit("tool_call", name=call.name, arguments=call.arguments)
            result = invoke_tool(call.name, call.arguments)

        self.memory.add_tool_result(call.id, result)
        return {"name": call.name, "arguments": call.arguments, "result": result}

    def _emit(self, event_type: str, **payload: Any) -> None:
        """
        向外通报进度。
        """
        self.on_event({"type": event_type, **payload})