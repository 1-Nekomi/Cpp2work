// main.cpp —— 判题主程序
//
// 流程：
//   1. 扫描 tests/ 下的题目目录（p1、p2…），每题目录里的每个 *.in 配一个同名 *.exp 作为预期输出；
//   2. 逐题编译 code/ 下的源码，生成可执行文件 code/build/pN；
//   3. 逐个用例运行学生程序：输入来自 .in（伪终端回显，与 VS 控制台样张一致），
//      输出同时写入 code/res.txt、code/res_pN.txt 和 report/actual/ 存档；
//   4. 与预期输出比对（默认智能容错，--strict 可关闭容错）；
//   5. 每题的最终结论用 assert 断言表达：断言失败 → 该题子进程 abort，父进程继续判其余题；
//   6. 汇总成绩：终端表格 + report/result.csv + report/cases.csv + report/summary.md。
#include <algorithm>
#include <cassert>
#include <cctype>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>

#ifdef _WIN32
#include <windows.h>
#include <io.h>
#else
#include <sys/wait.h>
#include <unistd.h>
#endif

#include "judge.h"
#include "report.h"
#include "runner.h"
#include "text.h"

namespace fs = std::filesystem;
using namespace judge;

namespace {

const char* kVersion = "1.0";

// ------------------------------------------------------------------ 数据结构

struct CaseSpec {
    std::string name;     // 用例名（不含扩展名）
    std::string in_file;  // 输入文件全路径
    std::string exp_file; // 预期输出文件全路径
};

struct ProblemSpec {
    int index = 0;          // 题号
    std::string dir_name;   // 目录名，如 p1
    std::string dir_path;   // 用例目录全路径
    std::string source;     // 学生源码路径（可能为空）
    std::vector<std::string> extra_sources; // 多文件工程时的附加源码
    std::vector<CaseSpec> cases;
};

struct Args {
    Options opt;
    bool help = false;
    std::string exe_dir;
};

// ------------------------------------------------------------------ 工具

std::string join_path(const std::string& a, const std::string& b) {
    if (a.empty()) return b;
    if (!a.empty() && a.back() == '/') return a + b;
    return a + "/" + b;
}

// 当前可执行文件的完整路径
std::string self_exe_path() {
#ifdef _WIN32
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetModuleFileNameW(nullptr, buf, static_cast<DWORD>(sizeof buf / sizeof buf[0]));
    if (n > 0 && n < sizeof buf / sizeof buf[0]) return wide_to_utf8(std::wstring(buf, n));
    return std::string();
#elif defined(__APPLE__)
    char buf[4096];
    uint32_t size = sizeof buf;
    if (_NSGetExecutablePath(buf, &size) == 0) return std::string(buf);
    return std::string();
#else
    char buf[4096];
    ssize_t n = ::readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n > 0) {
        buf[n] = '\0';
        return std::string(buf);
    }
    return std::string();
#endif
}

#ifdef _WIN32
// Windows 命令行参数加引号
std::string win_quote(const std::string& s) { return shell_quote(s); }

// 以自身为模板重新启动一个子进程（等价于 fork 出的判题子进程），返回退出码；-1 表示启动失败
int spawn_self(const std::vector<std::string>& args) {
    const std::string exe = self_exe_path();
    if (exe.empty()) return -1;
    const std::wstring wexe = utf8_to_wide(exe);
    std::wstring cmdline = utf8_to_wide(win_quote(exe));
    for (const std::string& a : args) cmdline += L" " + utf8_to_wide(win_quote(a));
    std::vector<wchar_t> buf(cmdline.begin(), cmdline.end());
    buf.push_back(L'\0');

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(wexe.c_str(), buf.data(), nullptr, nullptr, TRUE, 0, nullptr, nullptr, &si,
                        &pi)) {
        return -1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return static_cast<int>(code);
}
#endif

// 自然序比较：case2 < case10
bool natural_less(const std::string& a, const std::string& b) {
    size_t i = 0, j = 0;
    while (i < a.size() && j < b.size()) {
        const bool da = std::isdigit(static_cast<unsigned char>(a[i]));
        const bool db = std::isdigit(static_cast<unsigned char>(b[j]));
        if (da && db) {
            size_t i2 = i, j2 = j;
            while (i2 < a.size() && std::isdigit(static_cast<unsigned char>(a[i2]))) ++i2;
            while (j2 < b.size() && std::isdigit(static_cast<unsigned char>(b[j2]))) ++j2;
            long va = std::strtol(a.substr(i, i2 - i).c_str(), nullptr, 10);
            long vb = std::strtol(b.substr(j, j2 - j).c_str(), nullptr, 10);
            if (va != vb) return va < vb;
            i = i2;
            j = j2;
        } else {
            if (a[i] != b[j]) return a[i] < b[j];
            ++i;
            ++j;
        }
    }
    if (i < a.size()) return true;
    if (j < b.size()) return false;
    return false;
}

// 目录名里的第一段数字，如 p3 -> 3
int number_in_name(const std::string& name) {
    int best = -1;
    for (size_t i = 0; i < name.size();) {
        if (std::isdigit(static_cast<unsigned char>(name[i]))) {
            size_t j = i;
            while (j < name.size() && std::isdigit(static_cast<unsigned char>(name[j]))) ++j;
            best = std::atoi(name.substr(i, j - i).c_str());
            break;
        }
        ++i;
    }
    return best;
}

std::string strip_cr(const std::string& s) {
    std::string r;
    r.reserve(s.size());
    for (char c : s) {
        if (c != '\r') r.push_back(c);
    }
    return r;
}

std::string sanitize_field(const std::string& s) {
    std::string r = s;
    for (char& c : r) {
        if (c == '\t' || c == '\n' || c == '\r') c = ' ';
    }
    return r;
}

// ------------------------------------------------------------------ 命令行

void print_help(const char* prog) {
    std::printf(
        "C++ 作业自动判题器 v%s\n"
        "\n"
        "用法: %s [选项]\n"
        "\n"
        "目录约定:\n"
        "  code/            学生源码（p1.cpp … p5.cpp），编译产物在 code/build/\n"
        "  code/res.txt     最近一次运行的输出（每题另有 code/res_pN.txt）\n"
        "  tests/p1/        第 1 题的测试用例：case1.in + case1.exp …\n"
        "  report/          判题报告：result.csv、cases.csv、summary.md、actual/\n"
        "  include/ src/    判题程序自身源码\n"
        "\n"
        "选项:\n"
        "  --root <dir>       指定项目根目录（默认自动查找含 code/ 或 tests/ 的目录）\n"
        "  --code <dir>       学生代码目录（默认 <root>/code）\n"
        "  --tests <dir>      测试用例目录（默认 <root>/tests）\n"
        "  --out <dir>        报告输出目录（默认 <root>/report）\n"
        "  --compiler <cmd>   编译器（默认 g++）\n"
        "  --timeout <ms>     单个用例运行超时，单位毫秒（默认 5000）\n"
        "  --num-tol <eps>    数字容差，如 0.01（默认 0，即要求数字完全相同）\n"
        "  --strict           严格逐字符比对（关闭全角/半角等价、暂停行忽略、空行忽略）\n"
        "  --no-skip-header   不忽略学生程序首行的“学号姓名”\n"
        "  --pty              使用伪终端（输入会回显，适合必须使用控制台的程序）\n"
        "  --no-pty           直接重定向输入（默认，输入不回显）\n"
        "  --keep-waiting     程序等按键时一直等着（默认会自动结束这种“输出已完毕却不退出”的运行）\n"
        "  --no-compile       跳过编译，直接使用已有可执行文件\n"
        "  --no-win-compat    不为 #include <conio.h> 自动补兼容头\n"
        "  --problem <n>      只判第 n 题（答案错了会直接触发 assert 终止，调试用）\n"
        "  --list             只列出题目与用例\n"
        "  --verbose          打印编译命令行等细节\n"
        "  -h, --help         显示本帮助\n"
        "\n"
        "退出码: 0 = 全部通过；非 0 = 存在未通过（每题判定失败会触发 assert，见 README）\n",
        kVersion, prog);
}

bool parse_args(int argc, char** argv, Args* out) {
    Options& o = out->opt;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto need_value = [&](std::string* dst) -> bool {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "选项 %s 缺少参数\n", a.c_str());
                return false;
            }
            *dst = argv[++i];
            return true;
        };
        if (a == "-h" || a == "--help") { out->help = true; return true; }
        else if (a == "--root") { if (!need_value(&o.root)) return false; }
        else if (a == "--code") { if (!need_value(&o.code_dir)) return false; }
        else if (a == "--tests") { if (!need_value(&o.tests_dir)) return false; }
        else if (a == "--out") { if (!need_value(&o.out_dir)) return false; }
        else if (a == "--compiler") { if (!need_value(&o.compiler)) return false; }
        else if (a == "--timeout") {
            std::string v;
            if (!need_value(&v)) return false;
            o.timeout_ms = std::atoi(v.c_str());
            if (o.timeout_ms <= 0) o.timeout_ms = 5000;
        } else if (a == "--num-tol") {
            std::string v;
            if (!need_value(&v)) return false;
            o.num_tol = std::strtod(v.c_str(), nullptr);
        } else if (a == "--strict") { o.strict = true; }
        else if (a == "--no-skip-header") { o.skip_header = false; }
        else if (a == "--pty") { o.use_pty = true; }
        else if (a == "--no-pty") { o.use_pty = false; }
        else if (a == "--keep-waiting") { o.eof_on_idle = false; }
        else if (a == "--no-compile") { o.do_compile = false; }
        else if (a == "--no-win-compat") { o.win_compat = false; }
        else if (a == "--list") { o.list_only = true; }
        else if (a == "--verbose") { o.verbose = true; }
        else if (a == "--problem") {
            std::string v;
            if (!need_value(&v)) return false;
            o.only_problem = std::atoi(v.c_str());
        }
        else if (a == "--worker") {
            std::string v;
            if (!need_value(&v)) return false;
            o.worker = true;
            o.worker_problem = std::atoi(v.c_str());
        } else {
            std::fprintf(stderr, "未知选项: %s（用 --help 查看帮助）\n", a.c_str());
            return false;
        }
    }
    return true;
}

// ------------------------------------------------------------------ 扫描

std::vector<ProblemSpec> discover_problems(const Options& opt, std::string* error) {
    std::vector<ProblemSpec> specs;
    std::error_code ec;
    if (!fs::is_directory(opt.tests_dir, ec)) {
        *error = "用例目录不存在: " + opt.tests_dir;
        return specs;
    }
    std::vector<fs::path> dirs;
    for (const fs::directory_entry& e : fs::directory_iterator(opt.tests_dir, ec)) {
        if (!e.is_directory()) continue;
        bool has_in = false;
        for (const fs::directory_entry& f : fs::directory_iterator(e.path(), ec)) {
            if (f.is_regular_file() && f.path().extension() == ".in") { has_in = true; break; }
        }
        if (has_in) dirs.push_back(e.path());
    }
    // 兜底：用例目录本身就直接放 .in/.exp（只判一道题）时，也当作一道题处理
    if (dirs.empty()) {
        for (const fs::directory_entry& f : fs::directory_iterator(opt.tests_dir, ec)) {
            if (f.is_regular_file() && f.path().extension() == ".in") {
                dirs.push_back(fs::path(opt.tests_dir));
                break;
            }
        }
    }
    std::sort(dirs.begin(), dirs.end(), [](const fs::path& a, const fs::path& b) {
        const int na = number_in_name(a.filename().string());
        const int nb = number_in_name(b.filename().string());
        const int ka = na < 0 ? 1 << 20 : na;
        const int kb = nb < 0 ? 1 << 20 : nb;
        if (ka != kb) return ka < kb;
        return a.filename().string() < b.filename().string();
    });

    int position = 0;
    for (const fs::path& d : dirs) {
        ProblemSpec s;
        s.dir_name = d.filename().string();
        s.dir_path = d.string();
        const int n = number_in_name(s.dir_name);
        s.index = (n > 0) ? n : ++position;

        // 用例：每个 *.in 配对同名 *.exp / *.out / *.ans
        std::vector<std::string> bases;
        for (const fs::directory_entry& f : fs::directory_iterator(d, ec)) {
            if (!f.is_regular_file()) continue;
            if (f.path().extension() == ".in") bases.push_back(f.path().stem().string());
        }
        std::sort(bases.begin(), bases.end(), natural_less);
        for (const std::string& b : bases) {
            CaseSpec c;
            c.name = b;
            c.in_file = join_path(s.dir_path, b + ".in");
            for (const char* ext : {".exp", ".out", ".ans", ".expected"}) {
                std::string candidate = join_path(s.dir_path, b + ext);
                if (file_exists(candidate)) { c.exp_file = candidate; break; }
            }
            if (c.exp_file.empty()) c.exp_file = join_path(s.dir_path, b + ".exp");
            s.cases.push_back(c);
        }

        // 学生源码：<dir>.cpp / p<index>.cpp / <index>.cpp，也支持 .cc/.cxx/.c
        const std::vector<std::string> stems = {s.dir_name, "p" + std::to_string(s.index),
                                                std::to_string(s.index)};
        for (const std::string& stem : stems) {
            for (const char* ext : {".cpp", ".cc", ".cxx", ".c"}) {
                std::string candidate = join_path(opt.code_dir, stem + ext);
                if (file_exists(candidate)) { s.source = candidate; break; }
            }
            if (!s.source.empty()) break;
        }
        // 多文件：<code>/<dir>.sources 里每行一个附加源文件
        std::string list_file = join_path(opt.code_dir, s.dir_name + ".sources");
        std::string list_content;
        if (read_file(list_file, &list_content)) {
            std::istringstream is(list_content);
            std::string line;
            while (std::getline(is, line)) {
                line = trim(line);
                if (line.empty() || line[0] == '#') continue;
                s.extra_sources.push_back(join_path(opt.code_dir, line));
            }
        }
        specs.push_back(s);
    }
    return specs;
}

// ------------------------------------------------------------------ 比对

struct Profile {
    const char* token;
    const char* text;
    bool punct_equiv;
    bool drop_pause;
    bool drop_blank;
    bool drop_header;
    bool drop_indent;
    bool num_tol;
    bool ignore_linebreak;
};

// 从“完全一致”到“最宽松”，依次尝试；命中哪个档位会写进报告，便于教师判断学生的输出偏差程度
std::vector<Profile> build_profiles(const Options& opt) {
    std::vector<Profile> ps;
    ps.push_back({"exact", "程序输出完全一致", false, true, false, opt.skip_header, false, false, false});
    if (opt.strict) {
        return ps;
    }
    ps.push_back({"punct", "忽略全角/半角标点差异后一致", true, true, false, opt.skip_header, false, false, false});
    ps.push_back({"blank", "忽略空行后一致", true, true, true, opt.skip_header, false, false, false});
    ps.push_back({"indent", "忽略行首空白后一致", true, true, true, opt.skip_header, true, false, false});
    ps.push_back({"linebreak", "忽略换行位置后一致", true, true, true, opt.skip_header, true, false, true});
    if (opt.num_tol > 0) {
        ps.push_back({"numeric", "数字在容差内一致", true, true, true, opt.skip_header, true, true, false});
        ps.push_back({"numeric+linebreak", "忽略换行且数字在容差内一致", true, true, true, opt.skip_header, true, true, true});
    }
    return ps;
}

// 返回命中的档位下标，未命中返回 -1
int compare_output(const std::string& expected_raw, const std::string& actual_raw,
                   const std::vector<Profile>& profiles, const Options& opt,
                   std::string* normalized_expected, std::string* normalized_actual) {
    // 首行的“学号姓名”两边对称处理：学生程序打印的那行要忽略，
    // 标准答案（比如从满分参考程序的截图照搬来的）里若也带这行，同样忽略，
    // 否则学生写自己的学号就会与答案里的学号不同而误判。
    const std::string actual_first = first_non_empty_line(actual_raw);
    const std::string expected_first = first_non_empty_line(expected_raw);
    const bool actual_header = !actual_first.empty() && looks_like_header(actual_first);
    const bool expected_header = !expected_first.empty() && looks_like_header(expected_first);

    for (size_t i = 0; i < profiles.size(); ++i) {
        const Profile& p = profiles[i];
        NormOpt no;
        no.trim_trailing = true;
        no.trim_leading = p.drop_indent;
        no.drop_pause = p.drop_pause;
        no.punct_equiv = p.punct_equiv;
        no.drop_blank = p.drop_blank;
        no.drop_first_line = p.drop_header && expected_header;

        NormOpt na = no;
        na.drop_first_line = p.drop_header && actual_header;

        std::string e = normalize(expected_raw, no);
        std::string a = normalize(actual_raw, na);
        if (p.ignore_linebreak) {
            e.erase(std::remove(e.begin(), e.end(), '\n'), e.end());
            a.erase(std::remove(a.begin(), a.end(), '\n'), a.end());
        }
        bool same = false;
        if (p.num_tol) {
            std::vector<std::string> el = split_lines_keep_empty(e);
            std::vector<std::string> al = split_lines_keep_empty(a);
            same = (el.size() == al.size());
            for (size_t k = 0; same && k < el.size(); ++k)
                same = numeric_equal(el[k], al[k], opt.num_tol);
        } else {
            same = (e == a);
        }
        if (same) {
            if (normalized_expected) *normalized_expected = e;
            if (normalized_actual) *normalized_actual = a;
            return static_cast<int>(i);
        }
        if (i == 0) {
            if (normalized_expected) *normalized_expected = e;
            if (normalized_actual) *normalized_actual = a;
        }
    }
    return -1;
}

// ------------------------------------------------------------------ 单题判定

void print_case_line(const ProblemResult& pr, const CaseResult& c) {
    const std::string tag = "  " + c.name + " ";
    std::string state;
    switch (c.status) {
        case Status::Pass: state = color("通过", kGreen); break;
        case Status::WrongAnswer: state = color("答案错误", kRed); break;
        case Status::RunError: state = color("运行错误", kRed); break;
        case Status::Timeout: state = color("运行超时", kYellow); break;
        case Status::Missing: state = color("缺少文件", kYellow); break;
    }
    std::printf("%s %s", pad_display(tag, 12).c_str(), state.c_str());
    if (c.status == Status::Pass && !c.profile_text.empty() && c.profile_text != std::string("完全一致"))
        std::printf("  (%s)", c.profile_text.c_str());
    if (!c.note.empty()) std::printf("  %s", c.note.c_str());
    std::printf("\n");
    (void)pr;
}

ProblemResult judge_problem(const Options& opt, const ProblemSpec& spec,
                            const std::vector<Profile>& profiles) {
    ProblemResult pr;
    pr.index = spec.index;
    pr.name = spec.dir_name;
    pr.source = spec.source;

    std::printf("\n%s\n", color("----------------------------------------------------------------", kCyan).c_str());
    std::printf("%s %s\n", color("[第 " + std::to_string(spec.index) + " 题]", kBold).c_str(),
                spec.dir_name.c_str());

    // ---------- 编译 ----------
    const std::string build_dir = join_path(opt.code_dir, "build");
    std::error_code ec;
    fs::create_directories(build_dir, ec);
    pr.exe = join_path(build_dir, "p" + std::to_string(spec.index) + exe_suffix());

    if (!opt.do_compile) {
        pr.compiled = file_exists(pr.exe);
        if (!pr.compiled) {
            pr.compile_log = "跳过了编译，但可执行文件不存在: " + pr.exe + "\n";
            std::printf("  源码: %s\n", color("跳过编译，且可执行文件不存在", kRed).c_str());
        } else {
            std::printf("  源码: %s（跳过编译，使用已有可执行文件）\n", pr.exe.c_str());
        }
    } else if (spec.source.empty()) {
        pr.compiled = false;
        pr.compile_log = "未找到学生源码（期望 " + opt.code_dir + "/" +
                         spec.dir_name + ".cpp 或 p" + std::to_string(spec.index) + ".cpp）\n";
        std::printf("  源码: %s\n", color("未找到（本题按 0 分处理）", kRed).c_str());
    } else {
        std::vector<std::string> srcs = {spec.source};
        for (const std::string& s : spec.extra_sources) srcs.push_back(s);
        std::printf("  源码: %s\n", spec.source.c_str());
        CompileResult cr = compile_problem(opt, srcs, pr.exe);
        pr.compiled = cr.ok;
        pr.compile_cmd = cr.cmd;
        pr.compile_log = cr.log;
        if (cr.ok) {
            std::printf("  编译: %s\n", color("成功", kGreen).c_str());
            if (opt.verbose) std::printf("        %s\n", cr.cmd.c_str());
        } else {
            std::printf("  编译: %s\n", color("失败", kRed).c_str());
            if (opt.verbose) std::printf("        %s\n", cr.cmd.c_str());
            // 只显示前几行错误，避免刷屏
            std::istringstream is(cr.log);
            std::string line;
            int shown = 0;
            while (std::getline(is, line) && shown < 12) {
                std::printf("        %s\n", line.c_str());
                ++shown;
            }
        }
    }
    pr.compile_log_path = join_path(opt.out_dir, "log/p" + std::to_string(spec.index) + ".compile.log");
    write_file(pr.compile_log_path, pr.compile_cmd + "\n\n" + pr.compile_log + "\n");

    // ---------- 逐用例运行与比对 ----------
    const std::string actual_dir = join_path(opt.out_dir, "actual");
    fs::create_directories(actual_dir, ec);

    for (const CaseSpec& cs : spec.cases) {
        CaseResult c;
        c.name = cs.name;
        c.in_file = cs.in_file;
        c.exp_file = cs.exp_file;

        const std::string stem = "p" + std::to_string(spec.index) + "_" + cs.name;
        c.actual_path = join_path(actual_dir, stem + ".txt");
        c.stderr_path = join_path(actual_dir, stem + ".stderr.txt");
        c.diff_path = join_path(actual_dir, stem + ".diff.txt");
        fs::remove(c.actual_path, ec);
        fs::remove(c.stderr_path, ec);
        fs::remove(c.diff_path, ec);

        if (!file_exists(cs.in_file)) {
            c.status = Status::Missing;
            c.note = "缺少输入文件";
            c.diff = c.note + ": " + cs.in_file + "\n";
            write_file(c.diff_path, c.diff);
            pr.cases.push_back(c);
            print_case_line(pr, c);
            continue;
        }
        if (!file_exists(cs.exp_file)) {
            c.status = Status::Missing;
            c.note = "缺少预期文件（应放在 " + cs.exp_file + "）";
            c.diff = c.note + "\n";
            write_file(c.diff_path, c.diff);
            pr.cases.push_back(c);
            print_case_line(pr, c);
            continue;
        }
        if (!pr.compiled) {
            c.status = Status::Missing;
            c.note = "未编译成功，无法运行";
            c.diff = c.note + "；编译日志: " + pr.compile_log_path + "\n";
            write_file(c.diff_path, c.diff);
            pr.cases.push_back(c);
            print_case_line(pr, c);
            continue;
        }

        RunOut ro = opt.use_pty
                        ? run_case_pty(opt, pr.exe, cs.in_file, c.stderr_path, opt.timeout_ms)
                        : run_case_plain(opt, pr.exe, cs.in_file, c.stderr_path, opt.timeout_ms);
        ro.out = to_utf8(ro.out);
        ro.err = to_utf8(ro.err);
        write_file(c.stderr_path, ro.err);
        c.exit_code = ro.exit_code;
        c.signaled = ro.signaled;
        c.sig = ro.sig;

        // 结果落盘：code/res.txt（最近一次运行）+ 每题一份 + 每用例存档
        // 伪终端会把换行输出成 CRLF，这里存成 LF，方便在编辑器里查看
        const std::string saved = strip_cr(ro.out);
        write_file(c.actual_path, saved);
        write_file(join_path(opt.code_dir, "res.txt"), saved);
        write_file(join_path(opt.code_dir, "res_p" + std::to_string(spec.index) + ".txt"), saved);

        std::string expected_raw;
        read_file(cs.exp_file, &expected_raw);
        expected_raw = to_utf8(expected_raw);

        if (ro.status == Status::Timeout) {
            c.status = Status::Timeout;
            c.note = "运行超过 " + std::to_string(opt.timeout_ms) + " ms（可能是死循环或等待输入）";
            c.diff = "  学生程序运行超时，未产生完整输出。\n";
        } else {
            std::string ne, na;
            const int hit = compare_output(expected_raw, ro.out, profiles, opt, &ne, &na);
            if (hit >= 0) {
                c.status = ro.status == Status::RunError ? Status::RunError : Status::Pass;
                c.profile = profiles[hit].token;
                c.profile_text = profiles[hit].text;
                if (ro.idle_finish) {
                    c.note = "程序输出结束后仍在等待按键，判题器已自动结束本次运行";
                } else if (ro.signaled) {
                    c.note = "注意：程序被信号 " + std::to_string(ro.sig) + " 终止，但输出正确";
                } else if (ro.exit_code != 0) {
                    c.note = "程序输出符合预期，但退出码为 " + std::to_string(ro.exit_code);
                }
                if (c.status == Status::RunError)
                    c.diff = c.note + "\n";
            } else {
                // 用最宽松的档位对齐后再找差异：这样报告里呈现的是“去掉格式噪声之后真正的不同”，
                // 而不是行首缩进、暂停行、首行学号姓名这类本来允许的差别。
                std::string ne0, na0;
                compare_output(expected_raw, ro.out, {profiles.back()}, opt, &ne0, &na0);
                c.diff = opt.strict
                             ? "  （严格模式：忽略身份首行和暂停提示后逐字符比对）\n"
                             : "  （忽略身份首行、暂停提示和允许的格式差异后，仍有如下不同）\n";
                const std::string detail = first_diff_detail(ne0, na0);
                c.diff += detail.empty() ? diff_report(ne0, na0, 10) : detail;
                const bool no_output = trim(ro.out).empty();
                if (ro.status == Status::RunError) {
                    c.status = Status::RunError;
                    c.note = ro.signaled
                                 ? ("程序被信号 " + std::to_string(ro.sig) + " 终止（崩溃？）")
                                 : ("程序退出码 " + std::to_string(ro.exit_code));
                    if (!ro.err.empty()) c.diff += "  标准错误：\n" + ro.err;
                } else {
                    c.status = Status::WrongAnswer;
                    if (no_output) c.note = "程序没有任何输出";
                    if (ro.idle_finish)
                        c.note += "（程序输出完后一直在等按键，判题器已自动结束运行）";
                }
            }
        }
        if (!c.diff.empty()) write_file(c.diff_path, c.diff);
        if (c.status == Status::Pass) ++pr.passed;

        // 学号姓名：从首个用例输出的首行解析
        if (pr.student_id.empty() && pr.student_name.empty()) {
            const std::string first = first_non_empty_line(ro.out);
            if (!first.empty() && looks_like_header(first)) {
                parse_id_name(first, pr.student_id, pr.student_name);
            }
        }
        pr.cases.push_back(c);
        print_case_line(pr, c);
        if (c.status != Status::Pass && !c.diff.empty()) {
            std::istringstream is(c.diff);
            std::string line;
            int shown = 0;
            while (std::getline(is, line) && shown < 12) {
                std::printf("      %s\n", line.c_str());
                ++shown;
            }
        }
    }

    const int total = static_cast<int>(pr.cases.size());
    std::printf("  小计: %s\n", pad_display(std::to_string(pr.passed) + "/" + std::to_string(total),
                                        6).c_str());
    if (pr.passed == total && total > 0 && pr.compiled) {
        std::printf("  %s\n", color("判定：通过", kGreen).c_str());
    } else {
        std::printf("  %s\n", color("判定：未通过", kRed).c_str());
    }
    return pr;
}

// ------------------------------------------------------------------ 中间结果交换文件

void write_tsv(const Options& opt, const ProblemResult& pr) {
    std::ostringstream os;
    os << "compile\t" << (pr.compiled ? "ok" : "fail") << "\t" << sanitize_field(pr.compile_log_path)
       << "\t" << sanitize_field(pr.compile_cmd) << "\n";
    os << "student\t" << sanitize_field(pr.student_id) << "\t" << sanitize_field(pr.student_name)
       << "\n";
    for (const CaseResult& c : pr.cases) {
        os << "case\t" << sanitize_field(c.name) << "\t" << status_token(c.status) << "\t"
           << sanitize_field(c.profile) << "\t" << sanitize_field(c.profile_text) << "\t"
           << c.exit_code << "\t" << (c.signaled ? c.sig : 0) << "\t" << sanitize_field(c.in_file)
           << "\t" << sanitize_field(c.exp_file) << "\t" << sanitize_field(c.actual_path) << "\t"
           << sanitize_field(c.stderr_path) << "\t" << sanitize_field(c.diff_path) << "\t"
           << sanitize_field(c.note) << "\n";
    }
    write_file(join_path(opt.out_dir, ".internal/p" + std::to_string(pr.index) + ".tsv"), os.str());
}

std::vector<std::string> split_tsv(const std::string& line) {
    std::vector<std::string> out;
    std::string cur;
    for (char ch : line) {
        if (ch == '\t') {
            out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(ch);
        }
    }
    out.push_back(cur);
    return out;
}

ProblemResult load_tsv(const Options& opt, const ProblemSpec& spec) {
    ProblemResult pr;
    pr.index = spec.index;
    pr.name = spec.dir_name;
    pr.source = spec.source;
    pr.exe = join_path(join_path(opt.code_dir, "build"),
                       "p" + std::to_string(spec.index) + exe_suffix());

    std::string data;
    if (!read_file(join_path(opt.out_dir, ".internal/p" + std::to_string(spec.index) + ".tsv"),
                   &data)) {
        return pr; // 子进程没能写出结果（例如被强杀）
    }
    std::istringstream is(data);
    std::string line;
    while (std::getline(is, line)) {
        if (line.empty()) continue;
        std::vector<std::string> f = split_tsv(line);
        if (f[0] == "compile" && f.size() >= 2) {
            pr.compiled = (f[1] == "ok");
            if (f.size() >= 3) pr.compile_log_path = f[2];
            if (f.size() >= 4) pr.compile_cmd = f[3];
            if (!pr.compiled && !pr.compile_log_path.empty())
                read_file(pr.compile_log_path, &pr.compile_log);
        } else if (f[0] == "student" && f.size() >= 3) {
            pr.student_id = f[1];
            pr.student_name = f[2];
        } else if (f[0] == "case" && f.size() >= 13) {
            CaseResult c;
            c.name = f[1];
            c.status = status_from_token(f[2]);
            c.profile = f[3];
            c.profile_text = f[4];
            c.exit_code = std::atoi(f[5].c_str());
            c.sig = std::atoi(f[6].c_str());
            c.signaled = c.sig != 0;
            c.in_file = f[7];
            c.exp_file = f[8];
            c.actual_path = f[9];
            c.stderr_path = f[10];
            c.diff_path = f[11];
            c.note = f[12];
            if (c.status == Status::Pass) ++pr.passed;
            // 失败详情从存档文件读回来，供汇总报告使用
            if (c.status != Status::Pass && !c.diff_path.empty()) {
                read_file(c.diff_path, &c.diff);
                if (c.diff.size() > 4000) c.diff = c.diff.substr(0, 4000) + "\n...\n";
            }
            pr.cases.push_back(c);
        }
    }
    return pr;
}

// 在子进程中判定某题，最后用 assert 表达结论。
// fork 出的子进程与 Windows 下“重新启动自身”的 worker 共用这段逻辑。
void judge_and_assert(const Options& opt, const ProblemSpec& spec,
                      const std::vector<Profile>& profiles) {
    ProblemResult pr = judge_problem(opt, spec, profiles);
    write_tsv(opt, pr);

    const int total = static_cast<int>(pr.cases.size());
    const bool all_pass = pr.compiled && total > 0 && pr.passed == total;
    std::fflush(stdout);
    if (!all_pass) {
        std::printf("%s\n",
                    color("  [断言] 本题存在未通过的测试用例 → assert 断言失败", kRed).c_str());
        std::fflush(stdout);
    }
    // 核心判定：assert 断言。失败时子进程 abort（SIGABRT / 退出码 3），父进程继续判其余题目。
    assert(all_pass && "本题未通过全部测试用例");
    std::fflush(stdout);
}

} // namespace

// ------------------------------------------------------------------ main

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0); // 行缓冲：fork/assert abort 时不会丢日志

#ifdef _WIN32
    // CRT 把命令行从 UTF-16 转成系统 ANSI 编码，而 libstdc++ 的文件系统按 UTF-8
    // 解释窄字符路径。先统一转成 UTF-8，判题器内部才能处理中文目录。
    std::vector<std::string> utf8_args;
    utf8_args.reserve(static_cast<size_t>(argc));
    for (int i = 0; i < argc; ++i)
        utf8_args.push_back(argv[i] ? acp_to_utf8(argv[i]) : std::string());
    std::vector<char*> utf8_argv;
    utf8_argv.reserve(utf8_args.size());
    for (std::string& a : utf8_args) utf8_argv.push_back(a.data());
    argv = utf8_argv.data();
#endif

    Args args;
    if (!parse_args(argc, argv, &args)) return 2;
    if (args.help) {
        print_help(argv[0]);
        return 0;
    }
    Options& opt = args.opt;

    // 定位项目根目录
    {
        const std::string exe_path = self_exe_path();
        if (!exe_path.empty()) args.exe_dir = fs::path(exe_path).parent_path().string();
    }
    if (opt.root.empty()) opt.root = find_project_root(args.exe_dir);
    if (opt.code_dir.empty()) opt.code_dir = join_path(opt.root, "code");
    if (opt.tests_dir.empty()) opt.tests_dir = join_path(opt.root, "tests");
    if (opt.out_dir.empty()) opt.out_dir = join_path(opt.root, "report");

    // 统一转成绝对路径：运行学生程序时会先 chdir 到代码目录，相对路径会失效
    auto to_absolute = [](std::string& p) {
        if (p.empty()) return;
        std::error_code e;
        p = fs::absolute(p, e).lexically_normal().string();
    };
    to_absolute(opt.root);
    to_absolute(opt.code_dir);
    to_absolute(opt.tests_dir);
    to_absolute(opt.out_dir);

    std::error_code ec;
    fs::create_directories(opt.out_dir + "/actual", ec);
    fs::create_directories(opt.out_dir + "/log", ec);
    fs::create_directories(opt.out_dir + "/.internal", ec);

#ifdef _WIN32
    {
        const bool tty = _isatty(_fileno(stdout)) != 0;
        if (tty) { // 打开 ANSI 转义序列，让彩色输出在 Windows 控制台生效
            HANDLE h = GetStdHandle(STD_OUTPUT_HANDLE);
            DWORD mode = 0;
            if (h != INVALID_HANDLE_VALUE && GetConsoleMode(h, &mode))
                SetConsoleMode(h, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING);
        }
        set_color_enabled(tty);
    }
#else
    set_color_enabled(::isatty(STDOUT_FILENO) != 0);
#endif

    std::string err;
    std::vector<ProblemSpec> specs = discover_problems(opt, &err);
    if (!err.empty()) {
        std::fprintf(stderr, "%s\n", color(err, kRed).c_str());
        std::fprintf(stderr, "提示：用例目录结构应为 tests/p1/case1.in、tests/p1/case1.exp …\n");
        return 2;
    }
    if (specs.empty()) {
        std::fprintf(stderr, "%s\n", color("在 " + opt.tests_dir +
                                               " 下没有找到任何含 *.in 的题目目录", kRed).c_str());
        return 2;
    }

    // ---------- Windows worker 模式：本进程由父进程派生，只判一题并 assert ----------
    if (opt.worker) {
        const std::vector<Profile> profiles = build_profiles(opt);
        for (const ProblemSpec& spec : specs) {
            if (spec.index == opt.worker_problem) {
                judge_and_assert(opt, spec, profiles);
                return 0;
            }
        }
        std::fprintf(stderr, "worker: 未找到第 %d 题\n", opt.worker_problem);
        return 2;
    }

    std::printf("%s\n", color("C++ 作业自动判题器 v" + std::string(kVersion), kBold).c_str());
    std::printf("项目根目录: %s\n", opt.root.c_str());
    std::printf("用例目录  : %s\n", opt.tests_dir.c_str());
    std::printf("代码目录  : %s\n", opt.code_dir.c_str());
    std::printf("模式      : %s%s，首行学号姓名%s，超时 %d ms\n",
                opt.strict ? "严格比对" : "智能容错",
                opt.use_pty ? "（伪终端回显输入）" : "（直接重定向输入）",
                opt.skip_header ? "忽略" : "参与比对", opt.timeout_ms);
    for (const ProblemSpec& s : specs) {
        std::printf("  第 %d 题: %s（%d 个用例）  源码: %s\n", s.index, s.dir_name.c_str(),
                    static_cast<int>(s.cases.size()),
                    s.source.empty() ? "（未找到）" : fs::path(s.source).filename().string().c_str());
    }

    if (opt.list_only) return 0;

    const std::vector<Profile> profiles = build_profiles(opt);

    // ---------- 逐题判定：每题一个子进程，assert 失败只终止该题 ----------
    std::vector<ProblemResult> results;
    for (const ProblemSpec& spec : specs) {
        if (opt.only_problem > 0 && spec.index != opt.only_problem) continue;

        const std::string tsv = join_path(
            opt.out_dir, ".internal/p" + std::to_string(spec.index) + ".tsv");
        std::error_code rec;
        fs::remove(tsv, rec);

#ifdef _WIN32
        // Windows 没有 fork：把自身重新启动为一个“只判该题并 assert”的子进程，
        // 效果与 fork 相同——某题断言失败只终止该子进程，父进程继续判其余题目。
        std::vector<std::string> wargs;
        for (int i = 1; i < argc; ++i) wargs.push_back(argv[i]);
        wargs.push_back("--worker");
        wargs.push_back(std::to_string(spec.index));
        std::fflush(stdout);
        const int rc = spawn_self(wargs);
        if (rc == -1) {
            std::printf("%s\n", color("  → 第 " + std::to_string(spec.index) +
                                          " 题判题子进程无法启动（" + self_exe_path() + "）",
                                      kRed)
                                     .c_str());
        } else if (rc != 0) {
            std::printf("%s\n", color("  → 第 " + std::to_string(spec.index) +
                                          " 题 assert 断言失败（该题判定为未通过）",
                                      kRed)
                                     .c_str());
        }
#else
        std::fflush(stdout); // fork 前清空缓冲，避免子进程重复输出
        pid_t pid = ::fork();
        if (pid == 0) {
            // ---- 子进程：判定该题，最后用 assert 表达结论 ----
            judge_and_assert(opt, spec, profiles);
            ::_exit(0);
        }

        int status = 0;
        ::waitpid(pid, &status, 0);
        if (WIFSIGNALED(status)) {
            const int sig = WTERMSIG(status);
            if (sig == SIGABRT) {
                std::printf("%s\n", color("  → 第 " + std::to_string(spec.index) +
                                              " 题 assert 断言失败（该题判定为未通过）",
                                          kRed)
                                         .c_str());
            } else {
                std::printf("%s\n", color("  → 第 " + std::to_string(spec.index) +
                                              " 题判题子进程被信号 " + std::to_string(sig) +
                                              " 终止",
                                          kRed)
                                         .c_str());
            }
        } else if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            std::printf("%s\n", color("  → 第 " + std::to_string(spec.index) +
                                          " 题判题子进程异常退出（status=" +
                                          std::to_string(status) + "）",
                                      kRed)
                                     .c_str());
        }
#endif
        results.push_back(load_tsv(opt, spec));
    }

    if (results.empty()) {
        std::fprintf(stderr, "没有判定任何题目（--problem %d 未匹配到题目）\n", opt.only_problem);
        return 2;
    }

    // ---------- 汇总 ----------
    std::string student_id, student_name;
    for (const ProblemResult& p : results) {
        if (!p.student_id.empty() || !p.student_name.empty()) {
            student_id = p.student_id;
            student_name = p.student_name;
            break;
        }
    }
    const double full_per_problem =
        results.empty() ? 0.0 : 100.0 / static_cast<double>(results.size());

    print_summary(results, full_per_problem, student_id, student_name, opt.out_dir);
    write_csv(results, opt.out_dir, full_per_problem);
    write_markdown(results, opt.out_dir, full_per_problem, student_id, student_name);

    int total_cases = 0, total_pass = 0;
    for (const ProblemResult& p : results) {
        total_cases += static_cast<int>(p.cases.size());
        total_pass += p.passed;
    }
    std::fflush(stdout);

    // 最终判定：整份作业是否全部通过（报告已落盘，断言失败不会丢失任何信息）
    const bool all_ok = (total_cases > 0 && total_pass == total_cases);
    if (all_ok) return 0;
    std::fprintf(stderr, "整体判定：%d/%d 个用例通过 → 未全部通过\n", total_pass, total_cases);
    assert(all_ok && "整份作业未通过全部测试用例");
    return 1;
}
