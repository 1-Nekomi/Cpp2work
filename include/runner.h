// runner.h —— 编译学生源码、运行学生程序（含超时保护与伪终端回显）
#pragma once

#include <string>
#include <vector>

#include "judge.h"

namespace judge {

struct CompileResult {
    bool ok = false;
    std::string cmd;
    std::string log;
};

// 源码是否为合法 UTF-8；不是则视为 GBK/GB18030（VS 在中文 Windows 下的默认编码）
bool is_utf8(const std::string& data);

// 编译一道题。srcs 通常只有一个文件；exe 为输出可执行文件路径。
CompileResult compile_problem(const Options& opt, const std::vector<std::string>& srcs,
                              const std::string& exe);

// 一次运行的结果
struct RunOut {
    Status status = Status::RunError;
    int exit_code = 0;
    bool signaled = false;
    int sig = 0;
    bool idle_finish = false; // 程序输出完毕后一直等着按键/输入，被判题器主动结束
    std::string out; // 学生程序写入“控制台”的全部文字（含伪终端回显的输入）
    std::string err; // 标准错误（单独留存，不参与比对）
};

// 伪终端模式：学生的 stdin/stdout 接在一个伪终端上，键盘输入会被回显，
// 因此输出与在 VS/CMD 里看到的样张完全一致（system("pause") 的提示行除外）。
RunOut run_case_pty(const Options& opt, const std::string& exe, const std::string& in_file,
                    const std::string& err_file, int timeout_ms);

// 普通模式：直接把输入文件重定向到 stdin（不回显），适合非交互式题目。
RunOut run_case_plain(const Options& opt, const std::string& exe, const std::string& in_file,
                      const std::string& err_file, int timeout_ms);

std::string shell_quote(const std::string& s);
// 可执行文件扩展名：Windows 下为 ".exe"，其余平台为空
std::string exe_suffix();
// 执行 shell 命令并收集输出（用于编译），返回进程退出码
int run_shell_capture(const std::string& cmd, std::string* output);

} // namespace judge
