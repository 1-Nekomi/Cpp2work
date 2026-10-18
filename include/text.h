// text.h —— 输出文本的归一化、比对与差异报告
#pragma once

#include <string>
#include <vector>

namespace judge {

// 归一化开关
struct NormOpt {
    bool trim_trailing = true;   // 去掉每行行尾空白
    bool trim_leading = false;   // 去掉每行行首空白（从 Word/网页粘贴答案时常见的缩进）
    bool drop_pause = true;      // 丢掉 system("pause") 产生的“请按任意键继续. . .”整行
    bool punct_equiv = true;     // 全角标点/字母/数字 ↔ 半角 等价，不间断空格 → 普通空格
    bool drop_blank = true;      // 丢掉空行
    bool drop_first_line = false;// 丢掉第一行（程序打印的“学号姓名”）
};

// 按行归一化：统一换行、去 BOM、去行尾空白、可选丢暂停行/空行/首行
std::string normalize(const std::string& raw, const NormOpt& opt);

// 原样按行切分（保留空行），用于读取输入文件
std::vector<std::string> split_lines_keep_empty(const std::string& s);

// 是否像“学号姓名”行：含 6 位以上连续数字，或含“学号/姓名/班级”等字样
bool looks_like_header(const std::string& line);

// 从“2026120001张三”这类行中提取学号与姓名
void parse_id_name(const std::string& line, std::string& id, std::string& name);

// 取第一个非空行（用于解析学生身份）
std::string first_non_empty_line(const std::string& raw);

// 数字容差比对：把两侧的“骨架”（非数字部分）逐字比对，数字部分允许 |差| <= tol
bool numeric_equal(const std::string& a, const std::string& b, double tol);

// 生成行级差异报告（最多 max_lines 行差异）
std::string diff_report(const std::string& expected, const std::string& actual, int max_lines);

// 定位首个不同字符，给出人类可读的说明（含全角/半角提示）
std::string first_diff_detail(const std::string& expected, const std::string& actual);

// 字符说明，如 U+FF1A -> "全角冒号"
std::string char_name(unsigned int cp);
unsigned int utf8_decode_at(const std::string& s, size_t pos, size_t* len);

// 文件读写
bool file_exists(const std::string& path);
bool read_file(const std::string& path, std::string* out);
bool write_file(const std::string& path, const std::string& data);
std::string trim(const std::string& s);

// Windows: convert UTF-16/GB18030 or mixed UTF-8 + legacy console output to UTF-8.
// Other platforms return the bytes unchanged.
std::string to_utf8(const std::string& bytes);

} // namespace judge
