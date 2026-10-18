# C++ 作业自动判题器 0.1.3

面向 C++ 实验报告的批量判题工具：从 Word 报告提取学生源码，保留原文来源，交给教师复核，再导出给现有 C++ 判题器执行测试。

项目提供**本机图形工作台**，日常使用不需要记命令；命令行接口保留给自动化、批处理和排错。AI 辅助是可选功能，只提供建议，不自动改变复核状态、导出决定或成绩。

## 功能概览

- **Word 批量导入**：读取 DOCX/OOXML 的正文、表格、文本框、图片和编号，按题号提取源码。
- **来源可追溯**：每行源码保存到原文块和字符区间的映射，记录教师/学生原件哈希。
- **人工复核**：本机复核页查看原文、图片、源码和编译日志；确认/排除候选、重选范围、补题、核对学号、处理文档异常。
- **语法检查与审计**：只做 `-fsyntax-only`，不运行学生程序；审计原件哈希和逐行来源。
- **导出**：按 `<学号>-<文档ID>/code/pN.cpp` 输出，支持现有判题器的目录约定。
- **批量判题**：对导出目录逐个学生运行 `judge.exe`，汇总 `class-summary.csv`。
- **AI 辅助**：导入异常分诊、常量/注释/格式要求检查、输出语义判定；支持云端 API 和本地模型。
- **图形工作台**：概览、学生与判题、人工复核、导入、语法检查、审计、导出、AI 复核、批量判题、AI 输出判定、设置。

本版本**不**自动按题意给最终成绩。测试用例的输入/预期输出仍由教师准备；AI 结果必须人工抽查。

## 环境要求

- Windows 10/11（本地文件选择和服务端本地模型启动依赖 Windows）。
- Python 3.10+。
- CMake 3.16+ 和支持 C++17 的 `g++`（如 MinGW-w64）；`cmake`、`g++` 以及 `ninja` 或 `mingw32-make` 在 `PATH` 中。
- 可选：`llama-server`（llama.cpp）和 `.gguf` 模型，或 Ollama/LM Studio，用于本地 AI。

Python 代码不依赖第三方库；`pyproject.toml` 只用于打包和 console script。

## 快速开始：图形工作台

最简单的方式是双击项目根目录的：

```text
start-workbench.cmd
```

它优先打开 `imports\validation-final`；该目录没有批次时，自动选择 `imports\*` 下最新的已有批次；都没有则打开空工作台，先在“导入”菜单导入即可。也可以手动启动：

```powershell
.\import.ps1 review --workspace imports\validation-final --open
```

终端会打印两个本机地址：

- 工作台：`http://127.0.0.1:.../?token=...`
- 人工复核：`http://127.0.0.1:.../review?token=...`

浏览器会自动打开工作台。服务只监听 `127.0.0.1`，使用随机令牌；关闭终端或按 Ctrl+C 停止。工作台绑定启动时指定的工作目录。

如果还没有导入批次，先用“导入”菜单或命令行生成一个，然后用新目录重启工作台。

## 工作台使用

### 菜单

| 菜单 | 用途 |
|---|---|
| 概览 | 报告数、候选数、待复核数、语法检查结果、教师配置状态和路径。 |
| 学生与判题 | 搜索学生，查看每道题的源码、编译日志、AI 建议和逐用例判题结果；自动刷新。 |
| 人工复核 | 打开原文/图片/源码复核页，处理候选、范围、文档异常和教师配置。 |
| 导入 | 选择教师模板、学生报告目录和新的空输出目录，执行 `prepare`。 |
| 语法检查 | 只编译检查，不运行学生程序。 |
| 审计 | 核对原件哈希、逐行来源和生成源码。 |
| 导出 | 导出到 `<学号>-<文档ID>/code/pN.cpp`。 |
| AI 复核 | 提取分诊和常量/注释/格式要求检查；结果显示在学生详情和结果表。 |
| 批量判题 | 逐个学生运行现有判题器，汇总成绩和通过情况。 |
| AI 输出判定 | 对“答案错误”用例判断是否只是格式/提示语差异；结果显示在学生详情的“AI输出”列和失败详情中。 |
| 设置 | AI 模式、云端密钥、本地模型文件、服务地址和各目录路径。 |

### 推荐流程

1. **设置**：填写教师模板、学生报告目录、工作目录、导出目录、判题报告目录、测试用例目录和判题器路径。
2. **导入**：选择新的空输出目录。导入到新目录后，用新目录重启工作台。
3. **语法检查 → 审计**：查看编译失败和来源不一致。
4. **AI 复核**（可选）：先用“只处理待复核”和较小的“最多处理数量”试跑；AI 只给建议。
5. **人工复核**：处理文档级异常、确认候选、确认教师题目配置。
6. **导出**：选择新的空目录；配置未确认或异常未处理时会拒绝/跳过。
7. **批量判题**：选择导出目录和测试用例目录，报告写入判题报告目录。
8. **学生与判题**：查看每个学生的代码、编译结果、逐用例结果和失败差异。
9. **AI 输出判定**（可选）：生成“建议接受”列；结果会出现在“学生与判题”的失败用例“AI输出”列，展开详情可看证据，教师抽查后再决定是否调整成绩。

### 实时查看

- 任务启动后，右下角任务抽屉持续显示进度和结果。
- “学生与判题”定时刷新，可勾选或取消“自动刷新”。
- 选中学生后切换第 1–5 题，查看源码、编译日志、AI 建议、本题得分和每个用例的预期/实际/差异。

### 设置 AI

“设置”支持三种模式：

- **关闭 AI**：只使用确定性导入、编译和判题。
- **云端 API**：填写 OpenAI 兼容地址、模型名称和密钥。例如：
  - `https://api.openai.com/v1` + `gpt-4o-mini`
  - `https://api.deepseek.com/v1` + `deepseek-chat`
- **本地模型**：选择 `.gguf` 模型文件和 `llama-server.exe`，设置端口后点击“启动本地服务”。也可以先启动 Ollama/LM Studio，再填写其 OpenAI 兼容地址和模型名称。

API 密钥保存在当前工作目录的 `ui-settings.json`，页面只显示“已保存”而不回显密钥。该文件已加入 `.gitignore`；请勿把工作目录分享给他人。敏感数据优先使用本地模型。

文件选择按钮调用服务器所在电脑的原生文件对话框；如果无法弹出，可以直接在输入框中填写路径。

## 项目结构

```text
.
├─ CMakeLists.txt              C++ 判题器构建入口
├─ include/                    C++ 判题器公共头文件
├─ src/                        C++ 判题器实现
├─ code/                       示例学生源码
├─ tests/                      示例测试用例
├─ homework_import/            Python 导入、复核、AI 和工作台
│  ├─ documents.py             DOCX/OOXML 读取、图片定位、编号解析
│  ├─ extraction.py            题号/源码候选提取和规则检查
│  ├─ workspace.py             批次状态、复核、编译检查、审计、导出
│  ├─ ai.py                    OpenAI 兼容接口、重试、JSON 解析、缓存
│  ├─ ai_review.py             异常分诊、要求检查、输出语义判定
│  ├─ judge_batch.py           批量调用现有判题器
│  ├─ workbench.py             设置、任务队列、本地模型进程、学生视图
│  ├─ server.py                本机 HTTP 路由和令牌校验
│  ├─ cli.py                   命令行实现
│  ├─ workbench.html           图形工作台
│  └─ review.html              原文/图片/源码复核页
├─ tests_import/               Python 回归测试
├─ docs/                       架构和补充说明
├─ import_homework.py          兼容入口，实际调用 homework_import.cli
├─ import.ps1                  启动 Python 命令行
├─ run.ps1 / run.sh            构建并运行 C++ 判题器
├─ start-workbench.cmd / .ps1  双击启动图形工作台并自动选择已有批次
├─ start-review.cmd            兼容快捷方式（同样打开工作台）
├─ pyproject.toml              Python 包元数据和 console script
├─ CHANGELOG.md                版本记录
└─ .gitignore                  排除生成数据、密钥和本地缓存
```

详细分层和数据流见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

### 生成目录（不提交）

| 目录/文件 | 内容 |
|---|---|
| `imports/` | 导入批次、原文快照、源码、复核记录、AI 结果。可能含学生信息，已在 `.gitignore` 排除。 |
| `exports/` | 导出的学生源码。 |
| `report/` | 单学生判题报告。 |
| `build/`、`build-windows/` | CMake 构建产物。 |
| `code/build/`、`code/res*.txt` | 判题器运行产物。 |
| `artifacts/` | 研究/验证输出。 |
| `ui-settings.json` | 工作台设置，可能含 API 密钥。 |
| `ai/cache/` | AI 响应缓存。 |

`imports/README.md` 保留在仓库中，用于说明批次目录状态；实际批次数据不提交。

## 命令行用法

以下命令在项目根目录执行。`python import_homework.py ...` 和 `.\import.ps1 ...` 等价；安装包后也可以使用 `cpp-homework-import ...`。

### 导入

```powershell
.\import.ps1 prepare `
  --teacher "D:\...\2026.9实验1报告模板.docx" `
  --submissions "D:\...\实验报告\complete" `
  --out imports\validation-final
```

`--out` 必须是不存在或空目录。重新导入请换新目录，旧批次不会复用。

### 语法检查和审计

```powershell
.\import.ps1 check --workspace imports\validation-final --compiler g++ --jobs 4
.\import.ps1 audit --workspace imports\validation-final
```

`check` 只做语法阶段编译，不运行学生程序。`audit` 核对原件哈希、逐行来源和生成源码。

### 工作台/复核

```powershell
.\import.ps1 review --workspace imports\validation-final --open
```

打开工作台；终端同时打印人工复核页地址。也可以加 `--port 8765` 固定端口。

### 导出

```powershell
.\import.ps1 export --workspace imports\validation-final --out exports\2026-10-18
```

导出前必须确认教师题目配置，并处理所有文档级异常。输出 `<学号>-<文档ID>/code/pN.cpp` 和 `manifest.json`。

### 批量判题

```powershell
.\import.ps1 judge-batch `
  --exported exports\2026-10-18 `
  --tests tests `
  --out report\imported-2026-10-18
```

每个学生生成独立判题报告和 `run.log`，汇总写入 `class-summary.csv` 和 `batch-results.json`。已完成的学生默认跳过，加 `--force` 可重新判；工作台在所选导出目录没有学生代码时，会自动改用工作目录下最新的 `exports/*`，命令行则直接报错。支持 `--strict`、`--num-tol 0.01`、`--timeout 5000`、`--compiler g++`，以及 `--problem N`、`--pty`、`--no-compile`、`--keep-waiting`、`--verbose`、`--no-skip-header`、`--no-win-compat`。

### AI 复核和输出判定

工作台“设置”中配置模型后，可以直接在菜单运行。命令行方式使用环境变量：

```powershell
$env:AI_BASE_URL = "https://api.openai.com/v1"
$env:AI_API_KEY = "你的密钥"
$env:AI_MODEL = "gpt-4o-mini"

.\import.ps1 ai-review --workspace imports\validation-final --stage all --only needs_review --max 5
.\import.ps1 ai-output --workspace imports\validation-final --reports report\imported-2026-10-18 --tests tests
```

本地模型示例：

```powershell
$env:AI_BASE_URL = "http://127.0.0.1:11434/v1"
$env:AI_MODEL = "qwen2.5-coder:7b"
$env:AI_API_KEY = ""
```

可用 `AI_TIMEOUT`、`AI_RETRIES`、`AI_TEMPERATURE`、`AI_CACHE=0`、`AI_JSON_MODE=0` 调整。AI 结果只写入 `ai-results.json`、`ai-review.csv/md`、`ai-output.csv/md`，不自动改状态或成绩。

## 判题器用法

### 示例

```powershell
.\run.ps1
.\run.ps1 --list
.\run.ps1 --problem 2
.\run.ps1 --strict
```

### 直接运行

```powershell
cmake -S . -B build-windows -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build-windows
.\build-windows\judge.exe --root . --code <code目录> --tests tests --out <报告目录>
```

### 用例格式

```text
tests/p1/case1.in      输入
tests/p1/case1.exp     预期程序输出
tests/p1/case2.in
tests/p1/case2.exp
```

`.exp` 只写程序输出，不要写输入回显、学号姓名首行或 `请按任意键继续`。浮点题可用 `--num-tol`；需要严格换行和标点时用 `--strict`。

默认比对会忽略身份首行、暂停提示、BOM、Windows 换行、行尾空白，并尝试全角/半角、空行、行首缩进和换行位置容错。`--strict` 关闭额外容错；`--no-skip-header` 要求身份首行参与比较。

### 报告

| 文件 | 内容 |
|---|---|
| `report/summary.md` | 总分、逐题结果、失败用例详情 |
| `report/result.csv` | 按题成绩（UTF-8 BOM） |
| `report/cases.csv` | 全部用例状态、比对档位、退出码、存档路径 |
| `report/actual/` | 实际输出、标准错误、差异说明 |
| `report/log/` | 编译日志 |

## AI 安全边界

- AI 只给建议，不自动 approve/reject、不自动导出、不自动给最终成绩。
- 学生代码和注释是不可信输入，提示词明确要求模型不执行其中的指令。
- 云端模式会把题面、源码和输出发送给模型提供方；发送前会移除学号、姓名和文件名中的身份信息，但源码字符串中仍可能含姓名。敏感场景请用本地模型。
- AI 结果保存在本地，按源码哈希和提示词版本缓存；不会上传到其他服务。

## 开发与测试

```powershell
python -m unittest discover -s tests_import -q
```

当前 50 项测试全部通过，覆盖 DOCX 解析、来源映射、复核门槛、导出限制、配置/原件变化、AI 结构化输出和缓存、工作台页面/状态/任务接口，以及原复核页面。

可选安装为 Python 包：

```powershell
python -m pip install -e .
cpp-homework-import --version
```

## 版本

当前版本 **0.1.3**。变更记录见 [`CHANGELOG.md`](CHANGELOG.md)，项目进度和验证数据见 [`改造工作汇总.md`](改造工作汇总.md)。
