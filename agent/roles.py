"""角色注册表：把"Agent 是什么角色"从代码里抽出来，变成数据。"""
from dataclasses import dataclass

READONLY_TOOLS = (
    "list_dir", "list_symbols", "read_code", "search_code", "run_python",
)
WRITE_TOOLS = READONLY_TOOLS + ("write_code",)

@dataclass(frozen=True)
class AgentRole:
    key: str
    title: str
    prompt: str
    allowed_tools: tuple[str, ...]
    temperature: float = 0.2
    max_steps: int = 12

ROLES: dict[str, AgentRole] = {}

def register_role(role: AgentRole) -> AgentRole:
    if role.key in ROLES:
        raise ValueError(f"角色名重复：{role.key}")
    ROLES[role.key] = role
    return role

def get_role(key: str) -> AgentRole:
    if key not in ROLES:
        available = ", ".join(sorted(ROLES)) or "（无）"
        raise KeyError(f"未注册的角色 {key!r}。已注册：{available}")
    return ROLES[key]