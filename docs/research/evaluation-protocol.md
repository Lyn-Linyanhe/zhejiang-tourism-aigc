# 成本、效率与内容质量离线评测

## 目标

此协议把待办“补事实准确率、画面—旁白匹配、生产耗时/成本和批量成功率的离线评测与样本记录”变成可复核的实验记录。它不将平台播放数据解释为因果传播效果，不把 provider 的 `cost=0` 解释为免费，也不把生成成功等同于内容合格。

## 最小评测表

每个任务一行（另用逐镜表记录镜头级判断），至少记录：

| 字段 | 说明 |
|---|---|
| `sample_id`, `task_id`, `run_id` | 稳定样本/运行标识；禁止写 API key、口令 |
| `input_snapshot_sha256` | 对城市、主题、受众、事实版本、分镜输入的规范化快照计算 SHA-256 |
| `provider/model/fallback` | 记录脚本、配音、素材、渲染 provider/model 与回退 |
| `started_at`, `finished_at`, `duration_seconds`, `status` | 来自实际运行事件；区分端到端时间与单阶段时间 |
| `provider_cost_status`, `provider_reported_amount`, `provider_currency` | 仅记录 provider 明确提供的成本；缺失为 unknown/incomplete |
| `billing_amount`, `billing_currency`, `billing_source`, `billing_captured_at` | 从账单/发票人工抄录；与 provider 估值分开，按币种汇总 |
| `fact_claim_count`, `supported_claim_count`, `unsupported_claim_count` | 双人或至少一名复核人按引用记录逐条核对；给每条结论保留证据 ID |
| `shot_count`, `matched_shots`, `mismatch_shots` | 逐镜核对旁白事实与画面主体/活动/时空；保留镜头 ID 与理由 |
| `batch_size`, `batch_successes`, `batch_failures` | 同一配置批次；报告分子/分母和失败原因，不只给百分比 |
| `reviewer`, `reviewed_at`, `notes` | 人工复核记录，避免自动指标冒充人工验收 |

模板位于 `docs/research/evaluation-sample-template.csv` 与 `docs/research/evaluation-shot-template.csv`。空值表示未测，不填零。

## 计算口径

- **事实支持率** = `supported_claim_count / fact_claim_count`；仅当分母大于 0 且证据逐条登记时计算。事实精确性不以模型自评为准。
- **画面—旁白匹配率** = `matched_shots / shot_count`；逐镜审查地点、实体、活动、季节/年代等具体主张，画面不支持的细节视为不匹配。
- **生产耗时** = 运行结束时间减开始时间；另报批次 P50/P90，不将排队、人工剪辑等未记录时间假装包含在内。
- **批量成功率** = `batch_successes / batch_size`，同时给失败清单和重试次数。
- **成本**：provider 报告值只标注 provider-reported，无币种不得跨 provider 相加；实际账单按币种分别汇总并记录账单来源。无成本字段一律 unknown，不是零成本。
- **传播观测**：需记录平台、原帖 URL、发布日期、观测日期/窗口，以及平台直接显示的 views/reach/likes/comments/shares 等。不同平台不合并、不把不同窗口混算、不声称由工具生成导致效果变化；只有在存在可比对照设计时再讨论归因。

## 建议试验步骤

1. 先建立 10 条人工审核样本（杭州/宁波，覆盖景区、非遗、工艺/环境镜头），锁定文案、知识库版本、模型与配置。
2. 每条任务保存分镜、事实来源、逐镜素材来源、trace、manifest 和输出文件哈希；逐条人工标注事实与画面匹配结果。
3. 离线跑一批固定输入；记录尝试数、成功数、端到端耗时、provider 报告成本状态和外部账单。
4. 正式发布后，在第 1/7/30 天（若平台可见）录入平台指标和截图/导出来源；缺失的指标留空。
5. 汇总样本量、分布、失败原因和局限，不用单条片子或单个平台数据宣称“提升传播效果”。

## 当前实现边界

作品档案支持记录带来源的账单和帖子观测，并显示 run 耗时。当前成本 provider 报告值仍可能缺失；新增数据录入并不代表已拿到供应商发票或平台分析权限。当前还没有自动抓取平台数据、事实准确性分类器、实验对照组或统计显著性分析。未来只在获得用户授权与合法数据访问后开展真实付费/投放评测。
