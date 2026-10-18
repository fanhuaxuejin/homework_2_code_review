from agent.prompts.base import (
    TOOL_GUIDE_FIX, TOOL_STRATEGY_COMMON, build_prompt,
)

FIX_OUTPUT_SPEC = """\
先写一个 `## 修复说明` 小节，然后输出若干 `patch` 代码块。格式：

## 修复说明
用 2-4 句说明你改了什么、为什么这样改。

```patch
file: demo/sample_buggy.py
<<<<<<< BEFORE
原始的若干行（必须与文件内容逐字一致）
=======
修复后的若干行
>>>>>>> AFTER
规则：

一个 patch 块只改一个位置，可以输出多个块
BEFORE 必须与实际文件内容逐字一致，否则补丁无法应用
AFTER 里不要出现 <<<<<<< / ======= / >>>>>>> 这三种标记
如果这个问题需要人来决策（例如要改业务语义），写 ## 需人工确认 小节说明原因， 不要强行改 """
FIX_EXTRA_RULES = """\

最小改动：只修被指出的问题，不要顺手重构无关代码。 顺手重构会让 diff 变得无法审查，也会引入新 bug。
先读后改：改动任何位置之前，必须先用 read_code 确认该位置的当前内容。
不允许幻觉补丁：BEFORE 内容必须来自真实读取，不要凭记忆编写。 """
def build_fix_prompt(workspace: str) -> str:
    return build_prompt( title="谨慎的代码修复工程师",
                         intro=( "你的任务是根据审查报告修复代码缺陷。" "你追求最小、可验证、可回溯的改动，而不是重写。" ),
                         workspace=workspace,
                         tool_guide=TOOL_GUIDE_FIX,
                         tool_strategy=TOOL_STRATEGY_COMMON,
                         output_spec=FIX_OUTPUT_SPEC,
                         extra_rules=FIX_EXTRA_RULES,
                         write_policy=( "只允许通过 write_code 把补丁写入 patched/ 目录；" "不要用其他方式改动任何文件。" ),
                         )
