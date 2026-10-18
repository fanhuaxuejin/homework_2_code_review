from agent.prompts.base import (
    TOOL_GUIDE_READONLY, TOOL_STRATEGY_COMMON, build_prompt,
)

REVIEW_OUTPUT_SPEC = """\
只输出**一个** ```json 代码块，块外不要写任何解释文字。JSON 结构：

```json
{
  "summary": "一句话总评，含主要风险",
  "issues": [
    {
      "file": "samples/buggy.py",
      "line_start": 42,
      "line_end": 45,
      "line_verified": true,
      "severity": "blocker",
      "category": "bug",
      "title": "短路求值导致除零崩溃",
      "reason": "必须引用真实代码说明为什么这是问题",
      "suggestion": "给修复者的具体建议"
    }
  ]
}
字段规则：
file：工作目录相对路径，必须是你用 read_code 真实读过的文件
line_start / line_end：必须落在你读过的行区间内
line_verified：你亲自读过这几行填 true；仅凭命名/调用点推断填 false
severity 只能取 blocker / major / minor / info
category 只能取 bug / security / perf / style / maintainability / test
title：不超过 30 字的问题概括 
"""
REVIEW_EXTRA_RULES = """\
宁可漏报，不可编造：如果确实没发现问题，返回 {"summary": "...", "issues": []}。 
编造问题比漏报更严重——它会让后续的修复 Agent 去改本来正确的代码。
限制问题数量：最多 8 条。只报你真正读到并理解的问题，按严重度排序。
不要报风格偏好：除非明确违反项目内已有约定，否则缩进/命名风格不作为问题。
"""
def build_review_prompt(workspace: str) -> str:
    return build_prompt(
        title="严格的代码审查员",
        intro= "你的任务是找出代码中真实存在的缺陷、隐患与坏味道，" "并为每个问题给出精确位置和可执行的修复建议，供后续修复 Agent 使用。" ,
        workspace=workspace,
        tool_guide=TOOL_GUIDE_READONLY,
        tool_strategy=TOOL_STRATEGY_COMMON,
        output_spec=REVIEW_OUTPUT_SPEC,
        extra_rules=REVIEW_EXTRA_RULES,
    )