# 项目架构

版本 0.1。项目分为四层：C++ 判题核心、Word 导入核心、AI 辅助层和本机工作台。各层之间通过文件和工作目录解耦，便于单独测试和替换。

## 目录结构

```text
.
├─ CMakeLists.txt              C++ 判题器构建入口
├─ include/                    C++ 判题器公共头文件
├─ src/                        C++ 判题器实现（编译、运行、比对、报告）
├─ code/                       示例学生源码（p1.cpp … p5.cpp）
├─ tests/                      示例测试用例（tests/pN/caseM.in/.exp）
├─ homework_import/            Python 导入、复核、AI 和工作台
│  ├─ documents.py             DOCX/OOXML 读取、图片定位、编号解析
│  ├─ extraction.py            题号/源码候选提取和规则检查
│  ├─ workspace.py             批次状态、复核、编译检查、审计、导出
│  ├─ ai.py                    OpenAI 兼容接口、重试、JSON 解析、缓存
│  ├─ ai_review.py             异常分诊、要求检查、输出语义判定
│  ├─ judge_batch.py           对导出目录批量运行现有判题器
│  ├─ workbench.py             设置、任务队列、本地模型进程、学生视图
│  ├─ server.py                仅本机 HTTP 路由和令牌校验
│  ├─ workbench.html           图形工作台
│  └─ review.html              原文/图片/源码人工复核页
├─ tests_import/               Python 回归测试
├─ docs/                       架构和补充说明
├─ imports/                    生成的导入批次（不提交学生数据）
├─ exports/                    导出的学生源码（不提交）
├─ report/                     判题报告（不提交）
├─ import_homework.py          兼容入口，实际调用 homework_import.cli
├─ import.ps1                  启动 Python 命令行
├─ run.ps1 / run.sh            构建并运行 C++ 判题器
├─ start-workbench.cmd         双击启动图形工作台
└─ pyproject.toml              Python 包元数据和 console script
```

## 分层

### 1. C++ 判题核心

`src/` 和 `include/` 只负责确定性的编译、运行、输出比对和报告生成。它不知道 Word、AI 或工作台的存在。输入约定是 `code/pN.cpp` 加 `tests/pN/caseM.in/.exp`，输出是 `report/summary.md`、`result.csv`、`cases.csv` 和实际输出存档。

C++ 层保持确定性：同样的源码和用例必须得到同样的结论。AI 不进入这一层。

### 2. Word 导入核心

`documents.py` 把 DOCX 解析为可追溯的原文块；`extraction.py` 在块上识别题号、源码范围和异常；`workspace.py` 负责批次目录、来源校验、复核记录、语法检查、审计和导出。

关键不变量：

- 每行源码都保存到原文块和字符区间的映射；
- 复核决定和教师配置可追溯；
- 导出前校验配置哈希、原件哈希和候选状态；
- 没有教师确认，不导出为正式成绩流程。

### 3. AI 辅助层

`ai.py` 是唯一的模型访问层，支持任何 OpenAI 兼容接口，负责超时、重试、JSON 模式回退和按源码/提示词哈希缓存。

`ai_review.py` 提供三类建议：

- **导入异常分诊**：判断更像提取错误、学生源码错误还是不确定；
- **要求检查**：先做确定性检查（注释、`const`/`#define`、`setprecision(2)`），再由 AI 判断语义要求；
- **输出语义判定**：对判题失败的“答案错误”用例判断是否只是格式/提示语差异。

AI 结果写入 `ai-results.json`、`ai-review.csv/md`、`ai-output.csv/md`，只作为建议，不自动改变候选状态、导出决定或分数。发送前会移除学号、姓名和文件名中的身份信息。

### 4. 本机工作台

`workbench.py` 保存 UI 设置、管理后台任务、启动/停止本地 `llama-server`，并组装学生视图和判题结果。`server.py` 只做三件事：校验随机令牌、路由 HTTP、返回 JSON/HTML。服务只监听 `127.0.0.1`。

后台任务通过 `JobManager` 在独立线程运行，工作台轮询任务状态并显示日志；学生视图定时刷新导入候选和判题结果。原生文件选择窗口由服务器所在电脑打开。

## 数据流

```text
Word 报告 ──documents/extraction──> imports/<batch>/sources/*.cpp
                                        │
教师模板 ──assignment_draft─────────────┤
                                        ▼
                                  人工复核/确认
                                        │
                                        ▼
                              exports/<student>/code/pN.cpp
                                        │
tests/pN/caseM.in/.exp ──C++ judge──────┘
                                        │
                                        ▼
                              report/<student>/cases.csv
                                        │
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
                 工作台学生视图                  AI 输出语义判定
```

## 扩展点

- **新增判题能力**：优先修改 C++ 判题核心和测试用例，不要依赖 AI 给最终分数。
- **新增源码要求**：先在 `ai_review.deterministic_checks` 做确定性规则，再让 AI 处理语义部分。
- **新增导入规则**：在 `extraction.py` 增加规则并补充 `tests_import` 样本；无法确定的继续标记 `needs_review`。
- **新增 UI 功能**：在 `workbench.py` 增加 dispatch action，在 `server.py` 增加路由，在 `workbench.html` 增加菜单/表单。

## 测试

```powershell
python -m unittest discover -s tests_import -q
```

回归测试覆盖 DOCX 解析、来源映射、复核门槛、导出限制、配置/原件变化、AI 结构化和缓存、工作台页面/状态/任务接口，以及原复核页面。
