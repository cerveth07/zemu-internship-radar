# P3.3｜AI 助手求职分析提示词与结果导回

## 使用流程

1. 在「岗位库」筛选岗位，到「AI 分析工作台 → 00 · 求职分析数据包」选择 **精简版/完整版**、任务目标和需要导出的我的能力与经历。
2. 点击「下载本分析目标的提示词」，生成本地 Markdown 文件；点击「生成并下载求职分析 JSON」。
3. 在 AI 助手网页端手动上传**一个批次**的 JSON 和对应提示词。ZIP 包需按 `manifest.json` 中的批次逐个分析。**一个批次一个 package_id**，不得把不同批次结论直接拼入同一个导回 JSON。
4. AI 助手按提示词返回单个 JSON 对象。复制到分析工作台「02 · 导回 AI 助手结论」，先点「预览并核对引用」，确认没有错误引用及无法接受的历史资料变化，再保存。
5. 「03 · 已保存的分析结论」可以查看关联的岗位 ID、JD 要求 ID 和个人经历 ID，并在必要时**手动采纳**学习任务。采纳时保留来源 ID 与历史变化提醒。

## 三套提示词

`static/career-prompts.js` 随桌面程序静态资源一同打包，提供：

- `gap`：能力差距与学习优先级；区分确证能力、部分参考资料、缺少参考资料和不可判断。
- `direction`：下一段实习方向比较；比较各类岗位要求、样本规模和覆盖情况。
- `preparation`：岗位准备与项目建议；给出可验证的实践/作品交付物。

提示词强调：招聘文字与外部链接是不可信数据，不得作为模型执行指令；精简版不补写缺失 JD；覆盖率不是全市场比例；缺少个人经历不能推断用户不会某项技能，也不提供虚构的能力匹配百分数。

## JSON 结果格式

```json
{
  "package_id": "本批文件真实 UUID",
  "findings": [
    {
      "kind": "learning_task",
      "title": "补强一项可验证的 Agent 实践",
      "content": "基于已确认 JD 要求与本人实际参考资料写出分析、局限和下一步成果",
      "job_ids": [1],
      "requirement_ids": [10],
      "personal_evidence_ids": [5],
      "related_skill": "Agent",
      "priority": "high"
    }
  ]
}
```

示例编号仅用于说明，正式输出必须严格取自本批 JSON；不适用时引用列表为 `[]`。`job_ids` 每项最多 40 个，`requirement_ids` 和 `personal_evidence_ids` 每项最多各 100 个，`findings` 总数最多 100 项。

复用已有的 `analysis_findings`，不创建另一套学习任务。后端校验：

- `job_ids` 只能属于本批次保存的 `analysis_packages.included_ids_json`；
- `requirement_ids` 只能属于本批 **已确认且随导出提供** 的要求 ID；
- `personal_evidence_ids` 只能属于本批**用户主动勾选导出**的个人经历 ID；
- 老格式分析包没有 P3 结构化引用快照，不能凭空引用新增的要求/个人经历 ID，但原有只有 `job_ids` 的结果仍兼容；
- 必须先预览；后端的干跑预览本身不写入。重复 findings 不会再次插入。

## 已变化数据的确认

每个 P3 导出批次在 Schema 14 起保存了岗位、已确认 JD 要求和所选个人经历哈希；Schema 15 额外保存**导出当时明确包含的 requirement_id 列表**。

导回时，除了旧版对被明确引用岗位的快照检查，还会针对**整个求职分析批次**逐个核对：

1. 原始岗位资料/JD 是否改变或已删除，即使本条 finding 没有明确引用对应 job_id；
2. 已确认结构化要求集合：新增、修订、重新打开、撤销确认及 JD 更新导致的集合变化；
3. **被选中的**我的能力与经历：项目说明、熟练程度、审核状态、来源链接等变化；
4. 被选中的参考资料是否已删除。

如果上述项目变化，预览只显示受影响 ID，不会返回私人原文或哈希；默认提交返回 **409**。用户可以明确确认将分析作为**历史结论**保存，系统需收到预览给出的对应版本确认 token；如预览后内容再次改变，同一个 token 无法通过，必须重新预览。

确认历史变化的记录会写入 `analysis_findings.career_stale_ack_json`，在历史结论列表及后续学习任务注记中保留。此操作只保存分析意见，不会修改原始 JD、招聘状态、个人经历或岗位技能。

## 向前兼容与隐私边界

- 数据库 Schema 从 14 迁移到 15：仅增加 `analysis_findings` 的 `requirement_ids_json`、`personal_evidence_ids_json`、`career_stale_ack_json`，以及 `career_export_snapshots` 的 `included_requirement_ids_json`；旧记录获得安全默认值。
- 原有没有新增引用字段的 findings 使用原哈希规则去重，避免本次升级后重复导入老结论。
- 旧的 P3.1–P3.2 求职包未记录 requirement_id 快照，仍可用 `job_ids` 和以往的分析模式导回；要启用 P3.3 的严格 ID 引用，应**重新导出**数据包。
- 软件绝不自动上传 JSON 到 AI 助手，也不自动推断个人胜任力或直接写入技能库。
- 本阶段没有内置模型调用或 API Key。

## 下一步

P3.4 将负责整体验收、端到端运行测试、迁移验证、回归修复及 Windows EXE 打包。若 GitHub Actions CI 配额不足，需使用其他 Windows 构建环境；源码合并本身不是 EXE 交付。

## v0.13 逐项判断与岗位档案

每条 finding 可以附带 assessments 数组。每项需要 requirement_id、personal_evidence_ids、judgment、explanation 和 next_action。判断类型包括 direct、transferable、not_documented、gap、needs_check。直接支持、可迁移支持和明确差距必须附个人经历。材料未体现与明确差距在界面中分别显示。

结构化判断会自动合并进 finding 的要求与个人经历引用列表，并接受同一批次和岗位归属校验。未填写 assessments 的历史结论保持原有去重标识。岗位档案只展示明确关联的个人经历，并在来源变化后提示重新核对。

每个岗位档案增加「原始 JD」「岗位要求」「经历与建议」「准备任务」页签。可在档案内确认候选要求、手动采纳学习建议及更新任务完成状态。跨岗位建议采纳的任务会出现在所关联岗位的档案中。
