// runner.cpp —— 编译与运行学生程序的实现（Windows / Linux / macOS 通用）
//
// 交互运行说明（Windows）：
//   无法使用 Linux 的伪终端，这里改用管道 + 手工回显：程序安静下来（在等输入）时，
//   判题器把下一行输入写进子进程 stdin，同时把这一行追加到采集到的输出里，
//   于是输出与在 VS 控制台看到的样张一致（提示行 + 回显输入 + 结果行）。
//
// 交互运行说明（Linux/macOS）：
//   把学生的 stdin/stdout 接到一个 pty 上，pty 的行规程会把“输入的字符”回显到输出流里。
#ifdef _WIN32
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#ifndef WINVER
#define WINVER 0x0A00
#endif
#ifndef NTDDI_VERSION
#define NTDDI_VERSION 0x0A000006 // NTDDI_WIN10_RS5，ConPTY 需要
#endif
#endif
#include "runner.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <filesystem>

#ifdef _WIN32
#include <io.h>
#include <windows.h>
#else
#include <fcntl.h>
#include <poll.h>
#include <sys/wait.h>
#include <unistd.h>
#if defined(__linux__) || defined(__APPLE__)
#include <pty.h>
#endif
#endif

#include "text.h"

namespace judge {

namespace {
constexpr int kFeedIdleMs = 120;   // 程序“安静”这么久就认为它在等输入
constexpr int kFeedForceMs = 400;  // 即使判断不出状态，安静这么久也送下一行
constexpr int kDrainIdleMs = 250;  // 子进程结束后再收这么多毫秒的输出

#ifndef _WIN32
bool child_is_sleeping(pid_t pid) {
    std::string stat_path = "/proc/" + std::to_string((long)pid) + "/stat";
    std::string data;
    if (!read_file(stat_path, &data)) return true; // 拿不到状态就按“在等输入”处理
    size_t close_paren = data.rfind(')');
    if (close_paren == std::string::npos || close_paren + 2 >= data.size()) return true;
    char state = data[close_paren + 2];
    return state == 'S' || state == 'D';
}
#endif

std::vector<std::string> read_input_lines(const std::string& path) {
    std::string data;
    read_file(path, &data);
    data = to_utf8(data);
    std::vector<std::string> lines = split_lines_keep_empty(data);
    std::vector<std::string> out;
    for (std::string& l : lines) {
        if (!l.empty() && l.back() == '\r') l.pop_back();
        out.push_back(l);
    }
    // 文件末尾换行产生的空元素不算一行
    if (!out.empty() && out.back().empty() && !data.empty() && data.back() == '\n') out.pop_back();
    return out;
}

#ifdef _WIN32
std::string win_last_error() {
    const DWORD e = GetLastError();
    char* msg = nullptr;
    FormatMessageA(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                       FORMAT_MESSAGE_IGNORE_INSERTS,
                   nullptr, e, MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT), reinterpret_cast<LPSTR>(&msg),
                   0, nullptr);
    std::string s = msg ? acp_to_utf8(msg) : "未知错误";
    if (msg) LocalFree(msg);
    while (!s.empty() && (s.back() == '\r' || s.back() == '\n' || s.back() == ' ')) s.pop_back();
    return s + " (代码 " + std::to_string(static_cast<unsigned long>(e)) + ")";
}
#endif
} // namespace

// ------------------------------------------------------------------ 基础工具

bool is_utf8(const std::string& data) {
    size_t i = 0;
    while (i < data.size()) {
        unsigned char c = static_cast<unsigned char>(data[i]);
        size_t need;
        if (c < 0x80) { ++i; continue; }
        else if ((c & 0xE0) == 0xC0) need = 1;
        else if ((c & 0xF0) == 0xE0) need = 2;
        else if ((c & 0xF8) == 0xF0) need = 3;
        else return false;
        if (i + need >= data.size()) return false;
        for (size_t k = 1; k <= need; ++k) {
            if ((static_cast<unsigned char>(data[i + k]) & 0xC0) != 0x80) return false;
        }
        i += need + 1;
    }
    return true;
}

std::string shell_quote(const std::string& s) {
#ifdef _WIN32
    // Windows 命令行：用双引号包裹，反斜杠只在引号前需要转义
    std::string r = "\"";
    size_t backslashes = 0;
    for (char c : s) {
        if (c == '\\') { ++backslashes; r.push_back(c); continue; }
        if (c == '"') { r.append(backslashes, '\\'); r.push_back('\\'); r.push_back('"'); backslashes = 0; continue; }
        backslashes = 0;
        r.push_back(c);
    }
    r.append(backslashes, '\\'); // 结尾的连续反斜杠要翻倍，避免转义收尾引号
    r.push_back('"');
    return r;
#else
    std::string r = "'";
    for (char c : s) {
        if (c == '\'') r += "'\\''";
        else r.push_back(c);
    }
    r += "'";
    return r;
#endif
}

int run_shell_capture(const std::string& cmd, std::string* output) {
#ifdef _WIN32
    const std::wstring wide_cmd = utf8_to_wide(cmd + " 2>&1");
    FILE* p = _wpopen(wide_cmd.c_str(), L"r");
#else
    FILE* p = popen((cmd + " 2>&1").c_str(), "r");
#endif
    if (!p) {
        if (output) *output = "无法启动 shell 命令\n";
        return -1;
    }
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, p)) > 0) {
        if (output) output->append(buf, n);
    }
#ifdef _WIN32
    int st = _pclose(p);
    return st;
#else
    int st = pclose(p);
    if (WIFEXITED(st)) return WEXITSTATUS(st);
    if (WIFSIGNALED(st)) return 128 + WTERMSIG(st);
    return -1;
#endif
}

std::string exe_suffix() {
#ifdef _WIN32
    return ".exe";
#else
    return "";
#endif
}

// ------------------------------------------------------------------ 编译

CompileResult compile_problem(const Options& opt, const std::vector<std::string>& srcs,
                              const std::string& exe) {
    CompileResult r;
    std::string cmd = opt.compiler + " -std=c++17 -O0 -Wall -o " + shell_quote(exe);

    // 源码编码：只要有一份不是合法 UTF-8，就按 GBK/GB18030 处理并转成 UTF-8 输出
    bool any_read = false, all_utf8 = true, has_conio = false;
    for (const std::string& s : srcs) {
        std::string data;
        if (!read_file(s, &data)) continue;
        any_read = true;
        if (!is_utf8(data)) all_utf8 = false;
        if (data.find("conio.h") != std::string::npos) has_conio = true;
    }
    if (any_read && !all_utf8) cmd += " -finput-charset=GB18030 -fexec-charset=UTF-8";

#ifndef _WIN32
    // VS 专属头文件的兼容垫片（Windows/MinGW 自带 conio.h，无需垫片）
    if (has_conio && opt.win_compat) {
        std::string compat = opt.root + "/include/compat";
        if (file_exists(compat + "/conio.h")) cmd += " -I " + shell_quote(compat);
    }
#else
    (void)has_conio;
#endif

    for (const std::string& s : srcs) cmd += " " + shell_quote(s);
#ifndef _WIN32
    cmd += " -lm";
#endif
    r.cmd = cmd;

    int rc = run_shell_capture(cmd, &r.log);
    r.log = to_utf8(r.log);
    r.ok = (rc == 0) && file_exists(exe);
    if (!r.ok && r.log.empty()) {
        r.log = "编译失败（退出码 " + std::to_string(rc) + "），且编译器没有输出任何信息\n";
    }
    return r;
}

// ------------------------------------------------------------------ 运行：Windows（ConPTY 伪控制台；不可用时回退到管道）

#ifdef _WIN32

// 去掉 ConPTY 输出里的 ANSI 转义序列（光标控制、清屏等），只保留可见文字
static std::string strip_ansi_escapes(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size();) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        if (c != 0x1B) {
            out.push_back(s[i++]);
            continue;
        }
        ++i; // 跳过 ESC
        if (i >= s.size()) break;
        if (s[i] == '[') { // CSI: ESC [ ... 终止字节 0x40..0x7E
            ++i;
            while (i < s.size() && !(s[i] >= 0x40 && s[i] <= 0x7E)) ++i;
            if (i < s.size()) ++i;
        } else if (s[i] == ']') { // OSC: ESC ] ... 以 BEL 或 ST(ESC \) 结束
            ++i;
            while (i < s.size() && s[i] != 0x07) {
                if (s[i] == 0x1B && i + 1 < s.size() && s[i + 1] == '\\') { i += 2; break; }
                ++i;
            }
            if (i < s.size() && s[i] == 0x07) ++i;
        } else {
            ++i; // 其它两字节序列
        }
    }
    return out;
}

typedef HRESULT(WINAPI* PfnCreatePseudoConsole)(COORD, HANDLE, HANDLE, DWORD, HPCON*);
typedef void(WINAPI* PfnClosePseudoConsole)(HPCON);

struct ConPtyApi {
    PfnCreatePseudoConsole Create = nullptr;
    PfnClosePseudoConsole Close = nullptr;
    bool ok() const { return Create != nullptr && Close != nullptr; }
};

// 动态加载 ConPTY（Windows 10 1809+ 才提供；缺失时回退到管道方案）
static const ConPtyApi& conpty_api() {
    static ConPtyApi api;
    static bool inited = false;
    if (!inited) {
        inited = true;
        HMODULE k = GetModuleHandleA("kernel32.dll");
        if (k) {
            FARPROC pc = GetProcAddress(k, "CreatePseudoConsole");
            FARPROC pcc = GetProcAddress(k, "ClosePseudoConsole");
            std::memcpy(&api.Create, &pc, sizeof pc);
            std::memcpy(&api.Close, &pcc, sizeof pcc);
        }
    }
    return api;
}

// 回退方案：普通管道 + 手工回显（stdout 被重定向到管道时会变成全缓冲，回显位置可能不准）
static RunOut run_case_pipe_win(const Options& opt, const std::string& exe,
                                const std::string& in_file, const std::string& err_file,
                                int timeout_ms) {
    RunOut r;
    std::vector<std::string> input_lines = read_input_lines(in_file);

    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(sa);
    sa.lpSecurityDescriptor = nullptr;
    sa.bInheritHandle = TRUE;

    HANDLE in_r = nullptr, in_w = nullptr, out_r = nullptr, out_w = nullptr;
    if (!CreatePipe(&in_r, &in_w, &sa, 0)) {
        r.status = Status::RunError;
        r.out = "创建输入管道失败: " + win_last_error() + "\n";
        return r;
    }
    SetHandleInformation(in_w, HANDLE_FLAG_INHERIT, 0);
    if (!CreatePipe(&out_r, &out_w, &sa, 0)) {
        CloseHandle(in_r);
        CloseHandle(in_w);
        r.status = Status::RunError;
        r.out = "创建输出管道失败: " + win_last_error() + "\n";
        return r;
    }
    SetHandleInformation(out_r, HANDLE_FLAG_INHERIT, 0);

    const std::wstring werr_file = utf8_to_wide(err_file);
    HANDLE err_h = CreateFileW(werr_file.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                               &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (err_h == INVALID_HANDLE_VALUE) err_h = out_w;

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in_r;
    si.hStdOutput = out_w;
    si.hStdError = err_h;

    const std::wstring wexe = utf8_to_wide(exe);
    std::wstring cmdline = utf8_to_wide(shell_quote(exe));
    std::vector<wchar_t> cbuf(cmdline.begin(), cmdline.end());
    cbuf.push_back(L'\0');
    const std::wstring wcode_dir = utf8_to_wide(opt.code_dir);

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof pi);
    BOOL ok = CreateProcessW(wexe.c_str(), cbuf.data(), nullptr, nullptr, TRUE, 0, nullptr,
                             opt.code_dir.empty() ? nullptr : wcode_dir.c_str(), &si, &pi);

    // 父进程关闭子进程那一端的句柄
    CloseHandle(in_r);
    CloseHandle(out_w);
    if (err_h != out_w && err_h != INVALID_HANDLE_VALUE) CloseHandle(err_h);

    if (!ok) {
        CloseHandle(in_w);
        CloseHandle(out_r);
        r.status = Status::RunError;
        r.out = "无法启动学生程序 " + exe + ": " + win_last_error() + "\n";
        return r;
    }

    std::string out;
    bool child_done = false, timed_out = false, idle_finish = false, eof_sent = false;
    int idle_ms = 0;
    DWORD exit_code = 0;
    size_t next_input = 0;
    const int finish_idle_ms = std::max(600, std::min(2000, timeout_ms / 3));
    auto start = std::chrono::steady_clock::now();

    for (;;) {
        // 读取子进程当前可用的输出
        DWORD avail = 0;
        if (PeekNamedPipe(out_r, nullptr, 0, nullptr, &avail, nullptr) && avail > 0) {
            std::vector<char> buf(avail);
            DWORD got = 0;
            if (ReadFile(out_r, buf.data(), avail, &got, nullptr) && got > 0) {
                out.append(buf.data(), got);
                idle_ms = 0;
            } else {
                idle_ms += 20;
            }
        } else {
            idle_ms += 20;
        }

        if (!child_done && WaitForSingleObject(pi.hProcess, 0) == WAIT_OBJECT_0) {
            child_done = true;
            GetExitCodeProcess(pi.hProcess, &exit_code);
        }

        const long elapsed = static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                                   std::chrono::steady_clock::now() - start)
                                                   .count());
        if (!child_done && elapsed > timeout_ms) {
            timed_out = true;
            TerminateProcess(pi.hProcess, 1);
            WaitForSingleObject(pi.hProcess, 1000);
            child_done = true;
        }

        // 程序安静下来且还有输入没送：送下一行，并手工回显（模拟控制台）
        if (!child_done && next_input < input_lines.size() && idle_ms >= kFeedForceMs) {
            const std::string line = input_lines[next_input] + "\r\n";
            DWORD written = 0;
            if (in_w) WriteFile(in_w, line.data(), static_cast<DWORD>(line.size()), &written, nullptr);
            out += line; // 控制台回显
            ++next_input;
            idle_ms = 0;
        }

        // 输入已全部送出、程序仍阻塞在读取上：关闭 stdin 发送 EOF（getch/cin.get 会立即返回）
        if (!child_done && opt.eof_on_idle && !eof_sent && next_input >= input_lines.size() &&
            idle_ms >= 250) {
            if (in_w) {
                CloseHandle(in_w);
                in_w = nullptr;
            }
            eof_sent = true;
            idle_ms = 0;
        }

        // 送过 EOF 仍不退出：认为“输出已结束，程序卡在等按键”，主动结束并按已产生的输出判定
        if (!child_done && opt.eof_on_idle && eof_sent && idle_ms >= finish_idle_ms) {
            idle_finish = true;
            TerminateProcess(pi.hProcess, 1);
            WaitForSingleObject(pi.hProcess, 1000);
            child_done = true;
        }

        if (child_done && idle_ms > kDrainIdleMs) break;
        Sleep(20);
    }

    if (in_w) CloseHandle(in_w);
    CloseHandle(out_r);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);

    r.out = out;
    r.idle_finish = idle_finish;
    if (timed_out) {
        r.status = Status::Timeout;
    } else if (idle_finish) {
        r.status = Status::Pass;
    } else if (exit_code != 0) {
        r.exit_code = static_cast<int>(exit_code);
        r.status = Status::RunError;
    } else {
        r.status = Status::Pass;
    }
    read_file(err_file, &r.err);
    return r;
}

// 首选方案：ConPTY 伪控制台。子程序的 stdout 是控制台，行缓冲 + 输入自动回显，与 VS 一致。
static RunOut run_case_conpty_win(const Options& opt, const std::string& exe,
                                  const std::string& in_file, const std::string& err_file,
                                  int timeout_ms) {
    const ConPtyApi& api = conpty_api();
    if (!api.ok()) return run_case_pipe_win(opt, exe, in_file, err_file, timeout_ms);

    RunOut r;
    std::vector<std::string> input_lines = read_input_lines(in_file);
    write_file(err_file, ""); // ConPTY 只有一个输出流，stderr 会并入 stdout

    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof sa;
    sa.lpSecurityDescriptor = nullptr;
    sa.bInheritHandle = TRUE;

    HANDLE in_r = nullptr, in_w = nullptr, out_r = nullptr, out_w = nullptr;
    if (!CreatePipe(&in_r, &in_w, &sa, 0) || !CreatePipe(&out_r, &out_w, &sa, 0)) {
        if (in_r) CloseHandle(in_r);
        if (in_w) CloseHandle(in_w);
        if (out_r) CloseHandle(out_r);
        if (out_w) CloseHandle(out_w);
        return run_case_pipe_win(opt, exe, in_file, err_file, timeout_ms);
    }
    SetHandleInformation(in_w, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(out_r, HANDLE_FLAG_INHERIT, 0);

    COORD size;
    size.X = 120;
    size.Y = 40;
    HPCON hpc = INVALID_HANDLE_VALUE;
    HRESULT hr = api.Create(size, in_r, out_w, 0, &hpc);
    CloseHandle(in_r);
    CloseHandle(out_w);
    if (FAILED(hr)) {
        CloseHandle(in_w);
        CloseHandle(out_r);
        return run_case_pipe_win(opt, exe, in_file, err_file, timeout_ms);
    }

    STARTUPINFOEXW si;
    ZeroMemory(&si, sizeof si);
    si.StartupInfo.cb = sizeof si;
    SIZE_T bytes = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
    si.lpAttributeList =
        reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(HeapAlloc(GetProcessHeap(), 0, bytes));
    BOOL attr_ok = si.lpAttributeList &&
                   InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, &bytes) &&
                   UpdateProcThreadAttribute(si.lpAttributeList, 0,
                                             PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hpc,
                                             sizeof(HPCON), nullptr, nullptr);

    const std::wstring wexe = utf8_to_wide(exe);
    std::wstring cmdline = utf8_to_wide(shell_quote(exe));
    std::vector<wchar_t> cbuf(cmdline.begin(), cmdline.end());
    cbuf.push_back(L'\0');
    const std::wstring wcode_dir = utf8_to_wide(opt.code_dir);

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof pi);
    BOOL ok = FALSE;
    if (attr_ok) {
        ok = CreateProcessW(wexe.c_str(), cbuf.data(), nullptr, nullptr, FALSE,
                            EXTENDED_STARTUPINFO_PRESENT, nullptr,
                            opt.code_dir.empty() ? nullptr : wcode_dir.c_str(),
                            &si.StartupInfo, &pi);
    }
    if (si.lpAttributeList) {
        DeleteProcThreadAttributeList(si.lpAttributeList);
        HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
    }
    if (!ok) {
        api.Close(hpc);
        CloseHandle(in_w);
        CloseHandle(out_r);
        return run_case_pipe_win(opt, exe, in_file, err_file, timeout_ms);
    }
    std::string out;
    bool child_done = false, timed_out = false, idle_finish = false;
    int idle_ms = 0;
    DWORD exit_code = 0;
    size_t next_input = 0;
    const int finish_idle_ms = std::max(600, std::min(2000, timeout_ms / 3));
    auto start = std::chrono::steady_clock::now();

    for (;;) {
        DWORD avail = 0;
        if (PeekNamedPipe(out_r, nullptr, 0, nullptr, &avail, nullptr) && avail > 0) {
            std::vector<char> buf(avail);
            DWORD got = 0;
            if (ReadFile(out_r, buf.data(), avail, &got, nullptr) && got > 0) {
                out.append(buf.data(), got);
                idle_ms = 0;
            } else {
                idle_ms += 20;
            }
        } else {
            idle_ms += 20;
        }

        if (!child_done && WaitForSingleObject(pi.hProcess, 0) == WAIT_OBJECT_0) {
            child_done = true;
            GetExitCodeProcess(pi.hProcess, &exit_code);
        }

        const long elapsed = static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                                   std::chrono::steady_clock::now() - start)
                                                   .count());
        if (!child_done && elapsed > timeout_ms) {
            timed_out = true;
            TerminateProcess(pi.hProcess, 1);
            WaitForSingleObject(pi.hProcess, 1000);
            child_done = true;
        }

        // 控制台安静下来（在等输入）时送下一行；回显由控制台自动完成
        if (!child_done && next_input < input_lines.size() && idle_ms >= kFeedIdleMs) {
            const std::string line = input_lines[next_input++] + "\r";
            DWORD written = 0;
            if (in_w) WriteFile(in_w, line.data(), static_cast<DWORD>(line.size()), &written, nullptr);
            idle_ms = 0;
        }

        // 输入已全部送出、程序仍不退出（在等按键）：按“输出已结束”收尾
        if (!child_done && opt.eof_on_idle && next_input >= input_lines.size() &&
            idle_ms >= finish_idle_ms) {
            idle_finish = true;
            TerminateProcess(pi.hProcess, 1);
            WaitForSingleObject(pi.hProcess, 1000);
            child_done = true;
        }

        if (child_done && idle_ms > kDrainIdleMs) break;
        Sleep(20);
    }

    if (in_w) CloseHandle(in_w);
    CloseHandle(out_r);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    api.Close(hpc);

    r.out = strip_ansi_escapes(out);
    r.idle_finish = idle_finish;
    if (timed_out) {
        r.status = Status::Timeout;
    } else if (idle_finish) {
        r.status = Status::Pass;
    } else if (exit_code != 0) {
        r.exit_code = static_cast<int>(exit_code);
        r.status = Status::RunError;
    } else {
        r.status = Status::Pass;
    }
    return r;
}
#endif

// ------------------------------------------------------------------ 运行：伪终端（Linux/macOS）

RunOut run_case_pty(const Options& opt, const std::string& exe, const std::string& in_file,
                    const std::string& err_file, int timeout_ms) {
#ifdef _WIN32
    return run_case_conpty_win(opt, exe, in_file, err_file, timeout_ms);
#else
    RunOut r;
    std::vector<std::string> input_lines = read_input_lines(in_file);

    int master = -1;
    struct winsize ws;
    std::memset(&ws, 0, sizeof ws);
    ws.ws_row = 40;
    ws.ws_col = 120;

    pid_t pid = forkpty(&master, nullptr, nullptr, &ws);
    if (pid < 0) {
        r.status = Status::RunError;
        r.out = std::string("forkpty 失败: ") + std::strerror(errno) + "\n";
        return r;
    }
    if (pid == 0) {
        // ---- 子进程 ----
        int fd = ::open(err_file.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) {
            ::dup2(fd, STDERR_FILENO);
            ::close(fd);
        }
        if (!opt.code_dir.empty()) {
            if (::chdir(opt.code_dir.c_str()) != 0) { /* 切不过去就用当前目录 */ }
        }
        ::execl(exe.c_str(), exe.c_str(), static_cast<char*>(nullptr));
        std::fprintf(stderr, "judge: 无法执行 %s: %s\n", exe.c_str(), std::strerror(errno));
        ::_exit(127);
    }

    // ---- 父进程 ----
    std::string out;
    bool child_done = false, timed_out = false, slave_closed = false;
    bool eof_sent = false, idle_finish = false;
    int wstatus = 0;
    size_t next_input = 0;
    int idle_ms = 0;
    // 程序“输出完毕却一直等按键”（getch/cin.get/system("pause") 之类）的收尾窗口
    const int finish_idle_ms = std::max(600, std::min(2000, timeout_ms / 3));
    auto start = std::chrono::steady_clock::now();

    for (;;) {
        struct pollfd pfd;
        pfd.fd = master;
        pfd.events = POLLIN;
        pfd.revents = 0;
        int pr = ::poll(&pfd, 1, 30);
        if (pr > 0 && (pfd.revents & (POLLIN | POLLHUP | POLLERR))) {
            char buf[8192];
            ssize_t n = ::read(master, buf, sizeof buf);
            if (n > 0) {
                out.append(buf, static_cast<size_t>(n));
                idle_ms = 0;
            } else if (n == 0) {
                slave_closed = true;
            } else if (errno == EIO) {
                slave_closed = true; // 从端已关闭
            }
        } else {
            idle_ms += 30;
        }

        if (!child_done) {
            int st = 0;
            pid_t w = ::waitpid(pid, &st, WNOHANG);
            if (w == pid) {
                child_done = true;
                wstatus = st;
            }
        }

        long elapsed = static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                             std::chrono::steady_clock::now() - start)
                                             .count());
        if (!child_done && elapsed > timeout_ms) {
            timed_out = true;
            ::kill(-pid, SIGKILL); // 连同子孙进程一起结束（子进程是会话首进程）
            ::kill(pid, SIGKILL);
            ::waitpid(pid, &wstatus, 0);
            child_done = true;
        }

        const bool want_feed =
            (idle_ms >= kFeedIdleMs && child_is_sleeping(pid)) || idle_ms >= kFeedForceMs;
        if (!child_done && next_input < input_lines.size() && want_feed) {
            std::string line = input_lines[next_input++] + "\n";
            ssize_t w = ::write(master, line.data(), line.size());
            (void)w;
            idle_ms = 0;
        }

        // 输入已全部送出、程序却仍阻塞在读取上：发送终端 EOF（Ctrl+D），
        // 让 getch()/cin.get() 之类的“等待按键”立即返回，程序得以正常结束。
        if (!child_done && opt.eof_on_idle && !eof_sent && next_input >= input_lines.size() &&
            idle_ms >= 250 && child_is_sleeping(pid)) {
            const char eof = 4; // Ctrl+D
            ssize_t w = ::write(master, &eof, 1);
            (void)w;
            eof_sent = true;
            idle_ms = 0;
        }

        // 送过 EOF 仍不退出：认为“输出已结束，程序卡在等按键”，主动结束本次运行并按已产生的输出判定
        if (!child_done && opt.eof_on_idle && eof_sent && idle_ms >= finish_idle_ms &&
            child_is_sleeping(pid)) {
            idle_finish = true;
            ::kill(-pid, SIGKILL);
            ::kill(pid, SIGKILL);
            ::waitpid(pid, &wstatus, 0);
            child_done = true;
        }

        if (child_done && (slave_closed || idle_ms > kDrainIdleMs)) break;
    }
    ::close(master);

    r.out = out;
    r.idle_finish = idle_finish;
    if (timed_out) {
        r.status = Status::Timeout;
    } else if (idle_finish) {
        r.status = Status::Pass; // 输出已完整产生，只是程序在等按键，按“正常结束”处理
    } else if (WIFSIGNALED(wstatus)) {
        r.signaled = true;
        r.sig = WTERMSIG(wstatus);
        r.status = Status::RunError;
    } else if (WIFEXITED(wstatus)) {
        r.exit_code = WEXITSTATUS(wstatus);
        r.status = (r.exit_code == 0) ? Status::Pass : Status::RunError;
    } else {
        r.status = Status::RunError;
    }
    read_file(err_file, &r.err);
    return r;
#endif
}

// ------------------------------------------------------------------ 运行：直接重定向

#ifdef _WIN32
static RunOut run_case_plain_win(const Options& opt, const std::string& exe,
                                 const std::string& in_file, const std::string& err_file,
                                 int timeout_ms) {
    RunOut r;
    std::string out_tmp = err_file + ".stdout";
    std::string input_path = in_file;
    std::string input_raw;
    if (read_file(in_file, &input_raw)) {
        const std::string converted = to_utf8(input_raw);
        if (converted != input_raw) {
            input_path = err_file + ".stdin";
            if (!write_file(input_path, converted)) {
                r.out = "无法转换输入文件编码: " + in_file + "\n";
                return r;
            }
        }
    }
    struct TempInputCleanup {
        std::string path;
        ~TempInputCleanup() {
            if (!path.empty()) {
                std::error_code ec;
                std::filesystem::remove(path, ec);
            }
        }
    } cleanup{input_path == in_file ? "" : input_path};

    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(sa);
    sa.lpSecurityDescriptor = nullptr;
    sa.bInheritHandle = TRUE;

    const std::wstring winput_path = utf8_to_wide(input_path);
    const std::wstring wout_tmp = utf8_to_wide(out_tmp);
    const std::wstring werr_file = utf8_to_wide(err_file);
    HANDLE in_h = CreateFileW(winput_path.c_str(), GENERIC_READ, FILE_SHARE_READ, &sa, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    HANDLE out_h = CreateFileW(wout_tmp.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &sa, CREATE_ALWAYS,
                               FILE_ATTRIBUTE_NORMAL, nullptr);
    HANDLE err_h = CreateFileW(werr_file.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &sa, CREATE_ALWAYS,
                               FILE_ATTRIBUTE_NORMAL, nullptr);
    if (in_h == INVALID_HANDLE_VALUE || out_h == INVALID_HANDLE_VALUE ||
        err_h == INVALID_HANDLE_VALUE) {
        if (in_h != INVALID_HANDLE_VALUE) CloseHandle(in_h);
        if (out_h != INVALID_HANDLE_VALUE) CloseHandle(out_h);
        if (err_h != INVALID_HANDLE_VALUE) CloseHandle(err_h);
        r.status = Status::RunError;
        r.out = "无法打开重定向文件: " + win_last_error() + "\n";
        return r;
    }

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in_h;
    si.hStdOutput = out_h;
    si.hStdError = err_h;

    const std::wstring wexe = utf8_to_wide(exe);
    std::wstring cmdline = utf8_to_wide(shell_quote(exe));
    std::vector<wchar_t> cbuf(cmdline.begin(), cmdline.end());
    cbuf.push_back(L'\0');
    const std::wstring wcode_dir = utf8_to_wide(opt.code_dir);

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof pi);
    BOOL ok = CreateProcessW(wexe.c_str(), cbuf.data(), nullptr, nullptr, TRUE, 0, nullptr,
                             opt.code_dir.empty() ? nullptr : wcode_dir.c_str(), &si, &pi);
    CloseHandle(in_h);
    CloseHandle(out_h);
    CloseHandle(err_h);

    if (!ok) {
        r.status = Status::RunError;
        r.out = "无法启动学生程序 " + exe + ": " + win_last_error() + "\n";
        return r;
    }

    DWORD w = WaitForSingleObject(pi.hProcess, static_cast<DWORD>(timeout_ms));
    DWORD exit_code = 0;
    if (w == WAIT_TIMEOUT) {
        r.status = Status::Timeout;
        TerminateProcess(pi.hProcess, 1);
        WaitForSingleObject(pi.hProcess, 1000);
    } else {
        GetExitCodeProcess(pi.hProcess, &exit_code);
        r.exit_code = static_cast<int>(exit_code);
        r.status = (exit_code == 0) ? Status::Pass : Status::RunError;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);

    read_file(out_tmp, &r.out);
    read_file(err_file, &r.err);
    std::error_code ec;
    std::filesystem::remove(out_tmp, ec);
    return r;
}
#endif

RunOut run_case_plain(const Options& opt, const std::string& exe, const std::string& in_file,
                      const std::string& err_file, int timeout_ms) {
#ifdef _WIN32
    return run_case_plain_win(opt, exe, in_file, err_file, timeout_ms);
#else
    RunOut r;
    std::string out_tmp = err_file + ".stdout";
    char sec[32];
    std::snprintf(sec, sizeof sec, "%.3f", timeout_ms / 1000.0);

    std::string cmd = "cd " + shell_quote(opt.code_dir) + " && timeout -k 1 " + sec + " " +
                      shell_quote(exe) + " < " + shell_quote(in_file) + " > " + shell_quote(out_tmp) +
                      " 2> " + shell_quote(err_file);

    int st = system(cmd.c_str());
    read_file(out_tmp, &r.out);
    read_file(err_file, &r.err);
    ::unlink(out_tmp.c_str());

    if (st == -1) {
        r.status = Status::RunError;
        r.out += "judge: 无法执行命令\n";
        return r;
    }
    if (WIFEXITED(st)) {
        int rc = WEXITSTATUS(st);
        if (rc == 124) {
            r.status = Status::Timeout;
        } else {
            r.exit_code = rc;
            r.status = (rc == 0) ? Status::Pass : Status::RunError;
        }
    } else if (WIFSIGNALED(st)) {
        r.signaled = true;
        r.sig = WTERMSIG(st);
        r.status = Status::RunError;
    } else {
        r.status = Status::RunError;
    }
    return r;
#endif
}

} // namespace judge
