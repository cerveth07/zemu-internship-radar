# 交给任意 AI 助手：数据包、提示词与结果格式

择木不内置也不指定模型。分析工作由你选择的 AI 助手完成：DeepSeek、Kimi、通义千问、Claude、ChatGPT 的网页或客户端，或者你自己的 agent。应用负责准备数据、给出提示词，并在结果导回时严格校验。

本文说明手动交接的格式。用脚本或 agent 自动完成同样的流程，见 [本地接口与 MCP](agent-api.md)。

## 三类任务

| 任务 | 在应用中导出 | 提示词 | AI 返回 | 结果格式 |
| --- | --- | --- | --- | --- |
| JD 要求提取 | AI 分析 → JD 提取与审核 → 导出待处理岗位 | 数据包自带 `task_instructions`；也可点“复制提示词” | `{package_id, requirements[]}` | [requirements-result](agent/schemas/requirements-result.schema.json) |
| 求职分析 | AI 分析 → 求职分析与学习规划 → 生成求职分析 JSON | 点“复制提示词”（按分析目标不同） | `{package_id, findings[]}` | [findings-result](agent/schemas/findings-result.schema.json) |
| 岗位优先级 | 岗位库导出的 JSON 包 | 包内 `priority_result_hint` | `{package_id, priorities[]}` | [priorities-result](agent/schemas/priorities-result.schema.json) |

要求复核（`{batch_id, decisions[]}`，见 [review-result](agent/schemas/review-result.schema.json)）用于已导回候选要求之后的批量复核，复核包内附 `review-instructions.md`。

提示词原文同时保存在 [`static/prompts/`](../static/prompts/)，与应用中“复制提示词”的内容一致。

## 操作步骤

1. 在应用中导出 JSON。较多岗位会拆成多个文件（ZIP），**每个文件单独交给 AI**，不要合并。
2. 点“复制提示词”，粘贴给 AI 助手，再上传或粘贴导出的 JSON。
3. 要求 AI **只返回一个 JSON 对象**，不要 Markdown 代码围栏或解释。
4. 把返回的 JSON 保存为文件，或直接粘贴，用顶栏“导入 AI 结果”导回。应用会自动识别类型，先显示预览，你确认后才保存。

## 导回时的校验

格式之外，应用还会检查：

- `package_id` 必须是本机导出过的数据包；岗位原文在导出后被修改过的，会提示重新导出。
- 要求提取的 `evidence` 必须**逐字**出现在对应岗位的 `jd_text` 或 `eligibility` 中；组合要求（`any_of` / `all_of`）的每个选项都要出现在参考资料里。
- 求职分析引用的 `job_ids`、`requirement_ids`、`personal_evidence_ids` 必须属于这个数据包。
- 导回的要求是“待审核”的候选，导回的分析是“建议”，都不会自动变成人工确认，也不会修改岗位原文、招聘状态或个人记录。

不符合规则的条目不会写入，预览中会说明原因。

## 示例

- [要求提取结果](agent/examples/requirements-result.json)：包括一条 `any_of` 组合要求。
- [求职分析结论](agent/examples/findings-result.json)
- [岗位优先级](agent/examples/priorities-result.json)
- [会被拒绝的结果](agent/examples/invalid-requirements-result.json)：组合要求的选项 Python、Java 没有出现在参考资料原文中。

示例里的 `package_id` 与 ID 只用于说明格式，导回时必须使用你自己导出的数据包。

## 隐私

导出文件默认不含我的能力与经历、投递备注和联系人；求职分析中的我的能力与经历只在你逐条勾选后才会加入。把文件交给哪家 AI 服务、是否允许其保存，由你决定。

## 维护者

- 结果 Schema 由后端导入模型生成：`python tools/export_agent_schemas.py`。
- 提示词文件由前端提示词导出：`node tools/export_agent_prompts.cjs`。
- `tests/test_agent_api.py` 会检查两者与代码保持一致，以及示例能通过校验。
