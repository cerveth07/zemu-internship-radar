# 技能清洗 V1 候选接入（离线交接包）

离线技能清洗包（`radar-skill-handoff-v1`）把 4,059 条旧技能关联整理为 208 项标准能力词表，
并把低风险映射放进**独立暂存表**。本页说明择木如何读取它、以及不做什么。

## 数据位置

暂存表由交接包的 `tools/radar_staging_import.py --stage --confirm STAGE_V1` 写入，
表名以 `radar_skill_cleanup_` 开头：

| 表 | 内容 |
|---|---|
| `radar_skill_cleanup_imports` | 批次信息：`batch_id` / `payload_sha256` / 导入时间 / 条数 |
| `radar_skill_cleanup_links` | 候选关联：主键 `(batch_id, job_id, skill_name)`；字段 `category`、`necessity`、`evidence_json`、`old_skill_ids_json`、`live_check`、`review_status` |

仅在开启「包含待核候选」时读取 `live_check = 'candidate_supported_by_current_jd'`
且 `review_status = 'candidate'` 的行。该标记只代表导入时的检查结果：每次查询还会验证
参考资料是非空字符串数组、每条原文仍出现在当前 JD、强度有效；失效或格式错误的候选不展示。

## 在应用里如何呈现

`app/skill_gap.py` 把暂存行作为第四个来源 `cleanup_candidate` 读入「能力情报 / 能力差距」页，
与既有来源一起按 `(岗位, 能力)` 去重（同一岗位同一能力只计一次），并按强度保留必需/加分：

```
来源优先级：人工确认 (4) > GPT 复核 (3) > 技能清洗 V1 候选 (2) > 待核候选 (1)
```

界面表现：

- 技能行徽章显示「清洗候选 N 岗」，纯候选技能标为「待核」而不是已核实；
- 保留四张概览指标卡，清洗来源计数在技能行显示；
- 详情里的原文依据标注来源「技能清洗V1候选」；
- 复用既有「加入学习 / 已满足 / 详情」按钮与个人状态逻辑，V1 不自动设置任何个人掌握状态。

接口字段：`skills[].cleanup_jobs`、`cleanup_covered_jobs`、`skills[].examples[].origin`。

## 明确不做的事

- 不写入 `skills` / `job_skills` / `job_requirements`；不改动 GPT 复核记录与人工确认状态；
- 不把候选提升为 `confirmed`：只有人工核验后才能提升；
- 不拆解 `any_of` / `all_of` 组合条件，也不将其计为单项必需；
- 不扩充 208 项词表：未匹配的长尾能力保持待核。

## 回滚

- 只清除暂存数据：删除 `radar_skill_cleanup_*` 两张表即可，页面随即回到无清洗候选的状态；
- 完整恢复：使用暂存前的一致性备份（导入工具的 `--backup` 产物）替换 `radar.db`。
  不要在择木运行中替换数据库文件。

## 测试

`tests/test_skill_cleanup_gap.py` 覆盖：候选独立来源、`live_check` 过滤、同岗位人工确认优先且
不重复计数、影子表缺失不报错、加分强度保留。
