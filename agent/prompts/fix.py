from agent.prompts.base import (
    TOOL_GUIDE_FIX, TOOL_STRATEGY_COMMON, build_prompt,
)

FIX_OUTPUT_SPEC = """\
## 工作流程（必须严格按此顺序）

1. 用 `read_code` 读取目标文件的**完整内容**
2. 确定最小改动方案
3. 调用 `write_code`：
   - `source_path`：原文件相对路径（与审查报告里的 file 字段一致）
   - `content`：**修复后的完整文件内容**（从第一行到最后一行，整份文件）
   - `reason`：一句话说明改了什么
4. 写完后，用 `read_code` 读回补丁文件，确认改动正确
5. 最终回答里用 3-5 个 bullet 列出改动点和原因

## 输出规范
```markdown
## 修复说明
- `L18` 把 xxx 改成 yyy，因为 ...
- `L25` 增加 zzz 校验，避免 ...
## 注意
content 必须是完整文件，不是片段，不是 diff
不要输出 <<<<<<< / ======= / >>>>>>> 这类 diff 标记
只修被指出的问题，不要顺手重构无关代码 """

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
