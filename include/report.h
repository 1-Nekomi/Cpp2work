// report.h —— 判题结果的终端展示与成绩单导出
#pragma once

#include <string>
#include <vector>

#include "judge.h"

namespace judge {

// 终端显示宽度（中日韩全角字符按 2 列计）
int display_width(const std::string& s);
std::string pad_display(const std::string& s, int width);

// 彩色输出（非终端自动关闭）
void set_color_enabled(bool on);
std::string color(const std::string& text, const char* code);
extern const char* kGreen;
extern const char* kRed;
extern const char* kYellow;
extern const char* kCyan;
extern const char* kBold;
extern const char* kReset;

// 打印最终汇总表
void print_summary(const std::vector<ProblemResult>& results, double full_score_per_problem,
                   const std::string& student_id, const std::string& student_name,
                   const std::string& out_dir);

// 导出成绩单：report/result.csv（按题汇总）与 report/cases.csv（按用例明细）
void write_csv(const std::vector<ProblemResult>& results, const std::string& out_dir,
               double full_score_per_problem);

// 导出可读报告：report/summary.md
void write_markdown(const std::vector<ProblemResult>& results, const std::string& out_dir,
                    double full_score_per_problem, const std::string& student_id,
                    const std::string& student_name);

// 得分：满分 * 通过用例数 / 用例总数（无用例记 0 分）
double problem_score(const ProblemResult& p, double full_score);

} // namespace judge
