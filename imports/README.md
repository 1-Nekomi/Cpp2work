# imports 目录说明

本目录保存 Word 报告导入批次。批次之间互不复用：重新导入必须使用新的空目录，以免覆盖既有复核记录。

| 目录 | 状态 | 说明 |
|---|---|---|
| `lab1/` | 早期试验，已废弃 | 误把模板提醒文字识别成题号，统计不能作为最终结果。 |
| `validation-v2/` | 历史验证批次 | 旧提取规则运行结果（459 段候选、39 段待复核、20 段编译失败），已被 `validation-final/` 取代。 |
| `validation-final/` | 当前正式验证批次 | 92 份报告、460 段候选、441 段语法通过、27 段待复核、4 份文档级待复核；`integrity.json` 审计无错误。教师配置仍为草稿，尚未正式确认。 |

`validation-final/` 中的主要文件：

| 文件 | 用途 |
|---|---|
| `assignment.json` | 教师题目配置草稿；确认后才能导出。 |
| `batch.json` | 提交、源码候选、编译结果和复核记录。 |
| `documents/`、`assets/` | 原文块快照、图片和教师模板快照。 |
| `sources/` | 每段候选源码、逐行来源映射和编译日志。 |
| `summary.md`、`summary.json`、`review.csv` | 导入统计和异常清单。 |
| `integrity.json` | 原件哈希、逐行来源和入口数量审计结果。 |
| `review-events.jsonl` | 人工复核操作记录（首次复核后生成）。 |
| `ai-results.json`、`ai-review.md/csv` | 可选的 AI 分诊/要求检查建议（不自动改变复核状态）。 |
| `ai/cache/` | AI 响应缓存，按源码和提示词哈希保存。 |
| `ui-settings.json` | 工作台保存的路径和 AI 配置；API 密钥使用 Windows DPAPI 加密保存。 |

使用方式见项目根目录 `README.md` 的“图形界面工作台（推荐）”或“命令行导入与复核（高级）”一节。
