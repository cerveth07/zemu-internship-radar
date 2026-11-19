# P1｜JD 结构化要求提取与审核

## 使用流程

1. 从「岗位库」一键导出当前筛选结果，或从「AI 分析工作台」生成原有分析包。两种岗位包都有 `package_id` 和独立 JD 快照。
2. 上传单个岗位 JSON 给 AI，并使用分析工作台「下载 AI 提取说明」得到的提示词。**对每个分批文件单独提取，不要跨批混用 package_id**。
3. AI 返回严格 JSON 后，复制到「AI 分析工作台 → 04 · JD 任职要求提取与人工核验」，先预览，再保存为待审核要求。
4. 在「05 · 要求核验记录」逐条确认/拒绝，或者选择本页多条进行批量确认/拒绝。必要时先点击「修订」更正名称、程度、原文参考资料或组合逻辑。
5. 只有用户明确点击「确认并同步技能」，单项且明确为必需/加分的技能才会新增到 `job_skills`。原有人工技能不覆盖；批量确认也不会修改技能标签。

## 返回 JSON 格式

```json
{
  "package_id": "原数据包内的 UUID",
  "requirements": [
    {
      "job_id": 42,
      "requirement_type": "technical_skill",
      "name": "Python",
      "strength": "must",
      "evidence": "熟悉 Python 语言",
      "source_field": "jd_text",
      "option_logic": "single",
      "alternatives": []
    },
    {
      "job_id": 42,
      "requirement_type": "technical_skill",
      "name": "Python / Java / Go 至少一种",
      "strength": "must",
      "evidence": "熟悉 Python、Java、Go 中至少一种",
      "source_field": "jd_text",
      "option_logic": "any_of",
      "alternatives": ["Python", "Java", "Go"]
    }
  ]
}
```

注意上面的字段值都是示例，正式输出必须逐字引用真实 JD，不能把示例文本带入分析结果。

### 字段说明

| 字段 | 要求 |
|---|---|
| `job_id` | 原岗位 ID，必须属于当前 `package_id` 的原始数据集合 |
| `requirement_type` | `technical_skill`、`product_skill`、`research_skill`、`education`、`graduation_year`、`internship_schedule`、`experience`、`responsibility`、`other` |
| `name` | 人工可读的要求名称，不能无依据补写 |
| `strength` | `must` 明确要求、`bonus` 明确加分、`mentioned` 仅在职责中提及、`unknown` 不足以判断 |
| `evidence` | 必须是所选来源字段中的连续逐字子串，长度 1–700 字符 |
| `source_field` | `jd_text` 或 `eligibility` |
| `option_logic` | `single` 单项、`any_of` 任选至少一项、`all_of` 全部满足 |
| `alternatives` | 对 `any_of` 和 `all_of` 至少填写两个不重复条件；`single` 必须是 `[]` |

## 不可跳过的核对

- 导入和编辑会核对参考资料是否逐字出现在 JD 原文，参考资料不匹配返回 422，整个请求不写入。
- 导回时核对分析包里的岗位 ID 和快照，资料已改变则禁止新增候选（409），要求重新导出并分析。
- 对单条要求进行人工确认时，再检查 JD/资格条件是否在导入后发生变化。如果已变动则拒绝确认，允许拒绝旧候选；需重新分析新 JD。
- 相同岗位、相同结构化要求跨不同包重复导入时不会新增重复行。不同包的独立原始数据仍按其 `job_id` 区分。
- 所有新结果先以 `pending` 存储；原始 JD、招聘核验状态、优先级、申请进度保持不变。
- 已确认或拒绝的要求可以重新打开为待审核，但曾经同步到 `job_skills` 的技能**不会自动撤销**；必须在岗位技能核验界面单独处理。
- 批量审核是数据库事务，任何一条过期、异常或审核状态冲突，整批不写入。

## 数据库

新增 `job_requirements` 独立表，版本从 11 升为 12；不修改既有 `jobs`、`job_skills`、`analysis_findings` 原记录。表内保留来源 `package_id`、`source_jd_hash`、参考资料原文、结构化字段、状态、审核备注和可追溯时间。

## 目前没有实现

- 不会自动调用 AI 或自动解析本地 JD；用户必须自行将 JSON 上传模型。
- 不提供未经审核的 AI 结果自动影响岗位评级、招聘状态或学习优先级。
- 尚无 P2 的跨岗位统计与词汇归一化，也没有 P3 的个人能力匹配。
