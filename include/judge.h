// judge.h —— 判题程序的公共数据结构与常量
//
// 设计要点：
//   1. 一份“学生代码 + 用例”的作业 = 若干道题（默认 5 题），每题若干测试用例；
//   2. 学生程序在独立进程中执行，超时或崩溃不会影响其余用例；判题结果直接汇总；
//   3. 比对策略默认“智能容错”（忽略首行学号姓名、忽略 system("pause") 的提示行、
//      忽略行尾空白与空行、全角/半角标点等价），可用 --strict 切换为逐字符严格比对。
#pragma once

#include <string>
#include <vector>

namespace judge {

// ---------------- 运行参数 ----------------
struct Options {
    std::string root;      // 项目根目录（含 code/ tests/）
    std::string code_dir;  // 学生代码目录，默认 root/code
    std::string tests_dir; // 测试用例目录，默认 root/tests
    std::string out_dir;   // 报告输出目录，默认 root/report

    std::string compiler = "g++"; // 学生代码编译器
    int timeout_ms = 5000;        // 单个测试用例的运行超时（毫秒）
    double num_tol = 0.0;         // 数字容差，0 表示关闭（必须逐字符相同）
    bool strict = false;          // true = 严格比对（关闭全部容错，仅保留 CRLF 归一化）
    bool skip_header = true;      // 忽略学生程序首行“学号姓名”
    bool use_pty = false;         // 默认只采集程序输出；--pty 用于需要控制台的交互程序
    bool do_compile = true;       // false = 跳过编译，直接使用已有可执行文件
    bool rebuild = false;        // 强制重建，不复用编译缓存
    int compile_jobs = 2;         // 仅编译并行，学生程序仍串行运行
    bool win_compat = true;       // 自动为 #include <conio.h> 之类的源码补兼容头
    bool eof_on_idle = true;      // 程序等按键时自动送 EOF / 结束运行（避免误判超时）
    bool list_only = false;       // 只列出题目与用例，不判题
    bool verbose = false;         // 打印每题的编译命令行等细节
    int only_problem = 0;         // >0 时只判该题

    // 兼容旧版内部 worker 参数；正常判题不再启动额外判题进程。
    // 这两个字段仅供内部使用，用户无需关心。
    bool worker = false;          // true = 当前进程是判题子进程（只判一题）
    int worker_problem = 0;       // 子进程负责的题号
};

// ---------------- 单个测试用例的判定结果 ----------------
enum class Status {
    Pass,          // 通过
    WrongAnswer,   // 输出与预期不一致
    RunError,      // 运行出错（非 0 退出 / 崩溃 / 无法启动）
    Timeout,       // 运行超时
    Missing,       // 缺少源码 / 输入文件 / 预期文件
};

const char* status_token(Status s);          // 英文短名，写入中间结果文件
const char* status_text(Status s);           // 中文说明，打印用
Status status_from_token(const std::string& t);

struct CaseResult {
    std::string name;      // 用例名，如 case1
    std::string in_file;   // 输入文件
    std::string exp_file;  // 预期输出文件
    Status status = Status::Missing;

    std::string profile;      // 命中的比对档位（英文短名）
    std::string profile_text; // 命中的比对档位（中文说明）

    std::string actual_path; // 实际输出存档路径（report/actual/pN_caseM.txt）
    std::string stderr_path; // 标准错误存档路径
    std::string diff_path;   // 差异说明存档路径
    std::string note;        // 补充说明（编译失败原因、超时时间等）

    int exit_code = 0;       // 学生程序退出码
    bool signaled = false;   // 是否被信号终止
    int sig = 0;             // 信号编号
    std::string diff;        // 差异文本（打印 + 存盘）
};

// ---------------- 单道题的判定结果 ----------------
struct ProblemResult {
    int index = 0;             // 题号，从 1 开始
    std::string name;          // 用例目录名，如 p1
    std::string source;        // 学生源码路径
    std::string exe;           // 可执行文件路径
    bool compiled = false;
    std::string compile_cmd;
    std::string compile_log;
    std::string compile_log_path;

    std::vector<CaseResult> cases;
    int passed = 0;            // 通过用例数

    std::string student_id;    // 从首行解析出的学号
    std::string student_name;  // 从首行解析出的姓名
};

// 定位项目根目录：依次尝试 --root、环境变量 JUDGE_ROOT、当前目录、可执行文件所在目录及其上级，
// 取第一个包含 tests/ 子目录的目录。
std::string find_project_root(const std::string& hint_exe_dir);

} // namespace judge
