"""Workflow 的共享状态"""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# 严重度：顺序即排序权重，越小越严重
SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
VALID_SEVERITIES = tuple(SEVERITY_ORDER)

# 类别
VALID_CATEGORIES = ("bug", "security", "perf", "style", "maintainability", "test")

# 会被判为"必须修复"的严重度
BLOCKING_SEVERITIES = ("blocker", "major")

@dataclass
class Issue:
    """这是三个 Agent 之间流动的核心数据。"""
    id: str
    file: str
    line_start: int
    line_end: int
    severity: str           # blocker | major | minor | info
    category: str           # bug | security | perf | style | maintainability | test
    title: str
    reason: str
    suggestion: str = ""
    line_verified: bool = False
    status: str = "open"    # open | fixed | unresolved | wontfix

    def key(self) -> str:
        """跨轮次匹配同一问题"""
        return f"{self.file}::{self.category}::{self.title.strip()}"

    def short(self) -> str:
        """一行摘要，用于日志和给 Agent 的提示。"""
        return (
            f"[{self.id}] {self.severity}/{self.category} "
            f"{self.file}:{self.line_start} {self.title}"
        )

    def is_blocking(self) -> bool:
        return self.severity in BLOCKING_SEVERITIES

@dataclass
class NodeRecord:
    """一个节点的执行记录"""
    node: str
    status: str             # ok | failed | skipped
    started_at: str
    elapsed: float
    attempts: int = 1
    note: str = ""          # 说明
    error: str | None = None

@dataclass
class WorkflowState:
    """整个流程共享的唯一状态对象"""

    # 输入
    run_id: str
    targets: list[str]

    # 中间产物
    issues: list[Issue] = field(default_factory=list)
    original_issues: list[Issue] = field(default_factory=list)   # 第一轮的原始发现
    fixed_files: list[str] = field(default_factory=list)         # 产出的补丁路径
    patches: dict[str, str] = field(default_factory=dict)
    fix_round: int = 0

    # 流程控制
    current_node: str = "start"
    history: list[NodeRecord] = field(default_factory=list)
    max_fix_rounds: int = 2                                      # 熔断阈值

    # 输出
    unresolved: list[Issue] = field(default_factory=list)
    report_path: str | None = None
    finished: bool = False

    # 查询
    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.status == "open"]

    def has_blocking(self) -> bool:
        """还有没有必须修的blocker / major"""
        return any(i.is_blocking() for i in self.open_issues())

    def unresolved_count(self) -> int:
        """未解决总数"""
        return sum(1 for i in self.issues if i.status in ("open", "unresolved"))

    def open_count(self) -> int:
        return len(self.open_issues())

    def sorted_issues(self) -> list[Issue]:
        """按严重度、文件、行号排序"""
        return sorted(
            self.issues,
            key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.file, i.line_start),
        )

    def find(self, key: str) -> Issue | None:
        for i in self.issues:
            if i.key() == key:
                return i
        return None

    # 记录
    def record(self, node: str, status: str, started_at: str, elapsed: float,
               attempts: int = 1, note: str = "", error: str | None = None) -> None:
        self.history.append(NodeRecord(
            node=node, status=status, started_at=started_at,
            elapsed=round(elapsed, 2), attempts=attempts, note=note, error=error,
        ))

    # 序列化
    def snapshot(self) -> dict[str, Any]:
        """转成原始类型"""
        return {
            "run_id": self.run_id,
            "targets": self.targets,
            "current_node": self.current_node,
            "fix_round": self.fix_round,
            "issue_count": len(self.issues),
            "open_count": self.open_count(),
            "unresolved_count": self.unresolved_count(),
            "blocking": self.has_blocking(),
            "fixed_files": self.fixed_files,
            "history": [asdict(r) for r in self.history],
            "issues": [ asdict(i) for i in self.issues],
            "patches": self.patches,
            "max_fix_rounds": self.max_fix_rounds,
        }

    def summary(self) -> str:
        """摘要"""
        ok = sum(1 for r in self.history if r.status == "ok")
        bad = sum(1 for r in self.history if r.status == "failed")
        return (
            f"节点 {len(self.history)} 个（成功 {ok} / 失败 {bad}）| "
            f"问题 {len(self.issues)} 条（未解决 {self.unresolved_count()}）| "
            f"修复轮次 {self.fix_round}/{self.max_fix_rounds}"
        )

def new_run_id() -> str:
    return datetime.now().strftime("run-%Y%m%d-%H%M%S")