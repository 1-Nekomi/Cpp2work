// report.cpp —— 汇总表、CSV 成绩单、Markdown 报告
#include "report.h"

#include <cstdio>
#include <ctime>
#include <sstream>

#include "text.h"

namespace judge {

const char* kGreen = "\033[32m";
const char* kRed = "\033[31m";
const char* kYellow = "\033[33m";
const char* kCyan = "\033[36m";
const char* kBold = "\033[1m";
const char* kReset = "\033[0m";

static bool g_color = false;
void set_color_enabled(bool on) { g_color = on; }

std::string color(const std::string& text, const char* code) {
    if (!g_color) return text;
    return std::string(code) + text + kReset;
}

// ------------------------------------------------------------------ 显示宽度

static bool is_wide(unsigned int cp) {
    return (cp >= 0x1100 && cp <= 0x115F) || (cp >= 0x2E80 && cp <= 0x303E) ||
           (cp >= 0x3041 && cp <= 0x33FF) || (cp >= 0x3400 && cp <= 0x4DBF) ||
           (cp >= 0x4E00 && cp <= 0x9FFF) || (cp >= 0xA000 && cp <= 0xA4CF) ||
           (cp >= 0xAC00 && cp <= 0xD7A3) || (cp >= 0xF900 && cp <= 0xFAFF) ||
           (cp >= 0xFE30 && cp <= 0xFE4F) || (cp >= 0xFF00 && cp <= 0xFF60) ||
           (cp >= 0xFFE0 && cp <= 0xFFE6);
}

int display_width(const std::string& s) {
    int w = 0;
    for (size_t i = 0; i < s.size();) {
        size_t len = 0;
        unsigned int cp = utf8_decode_at(s, i, &len);
        if (cp == '\t') w += 4;
        else w += is_wide(cp) ? 2 : 1;
        i += len;
    }
    return w;
}

std::string pad_display(const std::string& s, int width) {
    int w = display_width(s);
    if (w >= width) return s;
    return s + std::string(static_cast<size_t>(width - w), ' ');
}

// ------------------------------------------------------------------ 得分

double problem_score(const ProblemResult& p, double full_score) {
    if (p.cases.empty()) return 0.0;
    return full_score * static_cast<double>(p.passed) / static_cast<double>(p.cases.size());
}

static std::string fmt1(double v) {
    char buf[32];
    std::snprintf(buf, sizeof buf, "%.1f", v);
    return buf;
}

static std::string now_string() {
    std::time_t t = std::time(nullptr);
    char buf[64];
    std::strftime(buf, sizeof buf, "%Y-%m-%d %H:%M:%S", std::localtime(&t));
    return buf;
}

static std::string csv_field(const std::string& s) {
    if (s.find_first_of(",\"\r\n") == std::string::npos) return s;
    std::string out = "\"";
    for (char ch : s) {
        if (ch == '"') out.push_back('"');
        out.push_back(ch);
    }
    return out + "\"";
}

static void report_file(std::ostringstream& os, const char* label, const std::string& path) {
    os << "- " << label << "：`" << path << "`\n";
    std::string data;
    if (read_file(path, &data)) {
        os << "\n````text\n" << (data.empty() ? "（空）\n" : data) << "\n````\n\n";
    } else {
        os << "  （文件不存在）\n\n";
    }
}

// ------------------------------------------------------------------ 汇总表

void print_summary(const std::vector<ProblemResult>& results, double full_score_per_problem,
                   const std::string& student_id, const std::string& student_name,
                   const std::string& out_dir) {
    const int wIdx = 6, wCases = 8, wPass = 8, wScore = 8, wState = 14;

    std::string line;
    line += "+" + std::string(wIdx + 2, '-') + "+" + std::string(wCases + 2, '-') + "+" +
            std::string(wPass + 2, '-') + "+" + std::string(wScore + 2, '-') + "+" +
            std::string(wState + 2, '-') + "+\n";

    std::printf("\n%s\n", color("==================== 判题汇总 ====================", kBold).c_str());
    std::string who;
    if (!student_id.empty() || !student_name.empty()) {
        who = "学号: " + (student_id.empty() ? std::string("(未识别)") : student_id) +
              "   姓名: " + (student_name.empty() ? std::string("(未识别)") : student_name);
    } else {
        who = "学号/姓名: (未从程序首行识别到)";
    }
    std::printf("%s\n", who.c_str());
    std::printf("判题时间: %s\n\n", now_string().c_str());

    std::fputs(line.c_str(), stdout);
    std::printf("| %s| %s| %s| %s| %s|\n", pad_display("题号", wIdx).c_str(),
                pad_display("用例数", wCases).c_str(), pad_display("通过", wPass).c_str(),
                pad_display("得分", wScore).c_str(), pad_display("结果", wState).c_str());
    std::fputs(line.c_str(), stdout);

    int total_cases = 0, total_pass = 0;
    double total_score = 0.0;
    for (const ProblemResult& p : results) {
        const int n = static_cast<int>(p.cases.size());
        total_cases += n;
        total_pass += p.passed;
        const double sc = problem_score(p, full_score_per_problem);
        total_score += sc;

        std::string state;
        if (!p.compiled) state = "编译失败";
        else if (n == 0) state = "无用例";
        else if (p.passed == n) state = "通过";
        else state = "未通过";

        std::string state_colored = state;
        if (state == "通过") state_colored = color(state, kGreen);
        else state_colored = color(state, kRed);
        // 彩色码不占显示宽度，重新补齐
        const int pad = wState - display_width(state);
        if (pad > 0) state_colored += std::string(static_cast<size_t>(pad), ' ');

        std::printf("| %s| %s| %s| %s| %s|\n", pad_display(std::to_string(p.index), wIdx).c_str(),
                    pad_display(std::to_string(n), wCases).c_str(),
                    pad_display(std::to_string(p.passed), wPass).c_str(),
                    pad_display(fmt1(sc), wScore).c_str(), state_colored.c_str());
    }
    std::fputs(line.c_str(), stdout);

    const double full_total = full_score_per_problem * static_cast<double>(results.size());
    std::printf("  合计: 用例 %d/%d 通过，总分 %s / %s\n", total_pass, total_cases,
                fmt1(total_score).c_str(), fmt1(full_total).c_str());
    std::printf("  报告: %s/result.csv  %s/summary.md  %s/actual/\n", out_dir.c_str(),
                out_dir.c_str(), out_dir.c_str());

    const bool all_ok = (total_pass == total_cases) && total_cases > 0;
    std::printf("\n%s\n", all_ok ? color("整体判定：全部通过 ✔", kGreen).c_str()
                                 : color("整体判定：存在未通过 ✘", kRed).c_str());
}

// ------------------------------------------------------------------ CSV

void write_csv(const std::vector<ProblemResult>& results, const std::string& out_dir,
               double full_score_per_problem) {
    std::ostringstream os;
    os << "\xEF\xBB\xBF"; // 带 BOM，Excel 直接打开不乱码
    os << "学号,姓名,题号,用例目录,用例通过,用例总数,本题满分,本题得分,状态\n";

    int total_cases = 0, total_pass = 0;
    double total_score = 0.0;
    for (const ProblemResult& p : results) {
        const int n = static_cast<int>(p.cases.size());
        total_cases += n;
        total_pass += p.passed;
        const double sc = problem_score(p, full_score_per_problem);
        total_score += sc;
        std::string state = !p.compiled ? "编译失败" : (n == 0 ? "无用例" : (p.passed == n ? "通过" : "未通过"));
        os << csv_field(p.student_id) << "," << csv_field(p.student_name) << "," << p.index << "," << csv_field(p.name) << ","
           << p.passed << "," << n << "," << fmt1(full_score_per_problem) << "," << fmt1(sc) << ","
           << state << "\n";
    }
    os << ",,合计,," << total_pass << "," << total_cases << ","
       << fmt1(full_score_per_problem * static_cast<double>(results.size())) << ","
       << fmt1(total_score) << "," << (total_pass == total_cases && total_cases > 0 ? "全部通过" : "存在未通过")
       << "\n";
    write_file(out_dir + "/result.csv", os.str());

    // 用例明细
    std::ostringstream cs;
    cs << "\xEF\xBB\xBF";
    cs << "题号,用例,输入文件,预期文件,结果,比对档位,退出码,说明,实际输出,标准错误,差异说明\n";
    for (const ProblemResult& p : results) {
        for (const CaseResult& c : p.cases) {
            cs << p.index << "," << csv_field(c.name) << "," << csv_field(c.in_file) << "," << csv_field(c.exp_file) << ","
               << status_text(c.status) << "," << (c.profile.empty() ? "-" : c.profile) << ","
               << c.exit_code << "," << csv_field(c.note) << "," << csv_field(c.actual_path) << "," << csv_field(c.stderr_path) << ","
               << (c.status == Status::Pass ? "" : csv_field(c.diff_path)) << "\n";
        }
    }
    write_file(out_dir + "/cases.csv", cs.str());
}

// ------------------------------------------------------------------ Markdown

void write_markdown(const std::vector<ProblemResult>& results, const std::string& out_dir,
                    double full_score_per_problem, const std::string& student_id,
                    const std::string& student_name) {
    std::ostringstream os;
    os << "# 实验判题报告\n\n";
    os << "- 学号：`" << student_id << "`\n";
    os << "- 姓名：`" << student_name << "`\n";
    os << "- 判题时间：" << now_string() << "\n\n";

    int total_cases = 0, total_pass = 0;
    double total_score = 0.0;
    os << "## 汇总\n\n| 题号 | 用例数 | 通过 | 得分 | 状态 |\n|---|---|---|---|---|\n";
    for (const ProblemResult& p : results) {
        const int n = static_cast<int>(p.cases.size());
        total_cases += n;
        total_pass += p.passed;
        const double sc = problem_score(p, full_score_per_problem);
        total_score += sc;
        std::string state = !p.compiled ? "编译失败" : (n == 0 ? "无用例" : (p.passed == n ? "通过" : "未通过"));
        os << "| " << p.index << " | " << n << " | " << p.passed << " | " << fmt1(sc) << " | "
           << state << " |\n";
    }
    os << "| 合计 | " << total_cases << " | " << total_pass << " | " << fmt1(total_score) << " / "
       << fmt1(full_score_per_problem * static_cast<double>(results.size())) << " | "
       << (total_pass == total_cases && total_cases > 0 ? "全部通过" : "存在未通过") << " |\n\n";

    os << "## 逐题明细\n\n";
    for (const ProblemResult& p : results) {
        os << "### 第 " << p.index << " 题（`" << p.name << "`）\n\n";
        os << "- 源码：`" << p.source << "`\n";
        os << "- 编译：" << (p.compiled ? "成功" : "**失败**") << "\n";
        if (!p.compiled) report_file(os, "编译日志", p.compile_log_path);
        if (!p.cases.empty()) {
            os << "\n| 用例 | 结果 | 比对档位 | 实际输出存档 |\n|---|---|---|---|\n";
            for (const CaseResult& c : p.cases) {
                os << "| " << c.name << " | " << status_text(c.status) << " | "
                   << (c.profile_text.empty() ? "-" : c.profile_text) << " | `" << c.actual_path
                   << "` |\n";
            }
        }
        for (const CaseResult& c : p.cases) {
            if (c.status == Status::Pass) continue;
            os << "\n#### " << c.name << " 失败详情\n\n";
            os << "- 状态：" << status_text(c.status) << "\n";
            os << "- 退出码：" << c.exit_code << "\n";
            if (c.signaled) os << "- 终止信号：" << c.sig << "\n";
            if (!c.note.empty()) os << "- 说明：" << c.note << "\n";
            os << "\n";
            report_file(os, "输入", c.in_file);
            report_file(os, "预期输出", c.exp_file);
            report_file(os, "实际输出", c.actual_path);
            report_file(os, "标准错误", c.stderr_path);
            report_file(os, "差异说明", c.diff_path);
        }
        os << "\n";
    }
    write_file(out_dir + "/summary.md", os.str());
}

} // namespace judge
