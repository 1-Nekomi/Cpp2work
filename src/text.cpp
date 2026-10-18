// text.cpp —— 归一化、比对与差异报告的实现
#include "text.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>

#ifdef _WIN32
#include <windows.h>
#endif

namespace judge {

#ifdef _WIN32
std::string wide_to_utf8(const std::wstring& s) {
    if (s.empty()) return std::string();
    const int length = WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()),
                                           nullptr, 0, nullptr, nullptr);
    if (length <= 0) return std::string();
    std::string result(static_cast<size_t>(length), '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), result.data(), length,
                        nullptr, nullptr);
    return result;
}

std::string acp_to_utf8(const std::string& s) {
    if (s.empty()) return s;
    const int length = MultiByteToWideChar(CP_ACP, 0, s.data(), static_cast<int>(s.size()),
                                           nullptr, 0);
    if (length <= 0) return s;
    std::wstring wide(static_cast<size_t>(length), L'\0');
    MultiByteToWideChar(CP_ACP, 0, s.data(), static_cast<int>(s.size()), wide.data(), length);
    return wide_to_utf8(wide);
}

std::wstring utf8_to_wide(const std::string& s) {
    if (s.empty()) return std::wstring();
    int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(),
                                     static_cast<int>(s.size()), nullptr, 0);
    const UINT code_page = length > 0 ? CP_UTF8 : CP_ACP;
    if (length <= 0) {
        length = MultiByteToWideChar(CP_ACP, 0, s.data(), static_cast<int>(s.size()),
                                     nullptr, 0);
    }
    if (length <= 0) return std::wstring();
    std::wstring wide(static_cast<size_t>(length), L'\0');
    MultiByteToWideChar(code_page, 0, s.data(), static_cast<int>(s.size()), wide.data(), length);
    return wide;
}
#endif

// ------------------------------------------------------------------ 文件读写

bool file_exists(const std::string& path) {
    // 用 filesystem::path 重载，Windows 下才能正确地把 UTF-8 路径转成宽字符。
    std::ifstream f(std::filesystem::path(path), std::ios::binary);
    return f.good();
}

bool read_file(const std::string& path, std::string* out) {
    std::ifstream f(std::filesystem::path(path), std::ios::binary);
    if (!f) return false;
    std::ostringstream ss;
    ss << f.rdbuf();
    *out = ss.str();
    return true;
}

bool write_file(const std::string& path, const std::string& data) {
    std::ofstream f(std::filesystem::path(path), std::ios::binary | std::ios::trunc);
    if (!f) return false;
    f.write(data.data(), static_cast<std::streamsize>(data.size()));
    return static_cast<bool>(f);
}

std::string trim(const std::string& s) {
    size_t b = 0, e = s.size();
    while (b < e && (unsigned char)s[b] <= ' ') ++b;
    while (e > b && (unsigned char)s[e - 1] <= ' ') --e;
    return s.substr(b, e - b);
}

std::string to_utf8(const std::string& bytes) {
#ifndef _WIN32
    return bytes;
#else
    if (bytes.empty()) return bytes;
    // PowerShell 5.1's default Set-Content encoding is UTF-16LE with a BOM.
    if (bytes.size() >= 2 &&
        ((unsigned char)bytes[0] == 0xFF && (unsigned char)bytes[1] == 0xFE)) {
        const int units = static_cast<int>((bytes.size() - 2) / 2);
        std::wstring wide;
        wide.reserve(units);
        for (int i = 0; i < units; ++i) {
            const size_t p = 2 + static_cast<size_t>(i) * 2;
            wide.push_back(static_cast<wchar_t>((unsigned char)bytes[p] |
                                             ((unsigned char)bytes[p + 1] << 8)));
        }
        const int length = WideCharToMultiByte(CP_UTF8, 0, wide.data(), units, nullptr, 0,
                                                nullptr, nullptr);
        if (length <= 0) return bytes;
        std::string result(static_cast<size_t>(length), '\0');
        WideCharToMultiByte(CP_UTF8, 0, wide.data(), units, result.data(), length,
                            nullptr, nullptr);
        return result;
    }

    // A pure GB18030 file can contain byte pairs that accidentally resemble valid
    // UTF-8. Prefer decoding the whole file when legacy bytes dominate.
    size_t utf8_bytes = 0, legacy_bytes = 0;
    for (size_t i = 0; i < bytes.size();) {
        const unsigned char c = static_cast<unsigned char>(bytes[i]);
        if (c < 0x80) { ++i; continue; }
        int n = c >= 0xC2 && c <= 0xDF ? 2 :
                c >= 0xE0 && c <= 0xEF ? 3 :
                c >= 0xF0 && c <= 0xF4 ? 4 : 0;
        if (n && i + n <= bytes.size() &&
            MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                bytes.data() + i, n, nullptr, 0) > 0) {
            utf8_bytes += n;
            i += n;
        } else {
            ++legacy_bytes;
            ++i;
        }
    }
    if (legacy_bytes > utf8_bytes) {
        const int length = MultiByteToWideChar(54936, MB_ERR_INVALID_CHARS,
                                               bytes.data(), static_cast<int>(bytes.size()),
                                               nullptr, 0);
        if (length > 0) {
            std::wstring wide(static_cast<size_t>(length), L'\0');
            MultiByteToWideChar(54936, MB_ERR_INVALID_CHARS, bytes.data(),
                                static_cast<int>(bytes.size()), wide.data(), length);
            const int n = WideCharToMultiByte(CP_UTF8, 0, wide.data(), length,
                                              nullptr, 0, nullptr, nullptr);
            if (n > 0) {
                std::string result(static_cast<size_t>(n), '\0');
                WideCharToMultiByte(CP_UTF8, 0, wide.data(), length,
                                    result.data(), n, nullptr, nullptr);
                return result;
            }
        }
    }

    // A redirected program can mix UTF-8 (student code) with GBK (cmd.exe pause).
    // Preserve valid UTF-8 sequences and decode legacy multibyte sequences in place.
    std::string out;
    out.reserve(bytes.size());
    for (size_t i = 0; i < bytes.size();) {
        const unsigned char c = static_cast<unsigned char>(bytes[i]);
        if (c < 0x80) { out.push_back(bytes[i++]); continue; }
        int utf8_len = 0;
        if (c >= 0xC2 && c <= 0xDF) utf8_len = 2;
        else if (c >= 0xE0 && c <= 0xEF) utf8_len = 3;
        else if (c >= 0xF0 && c <= 0xF4) utf8_len = 4;
        if (utf8_len > 0 && i + utf8_len <= bytes.size() &&
            MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data() + i,
                                utf8_len, nullptr, 0) > 0) {
            out.append(bytes, i, utf8_len);
            i += utf8_len;
            continue;
        }
        int legacy_len = 2;
        if (i + 4 <= bytes.size() && c >= 0x81 && c <= 0xFE &&
            bytes[i + 1] >= '0' && bytes[i + 1] <= '9' &&
            (unsigned char)bytes[i + 2] >= 0x81 &&
            bytes[i + 3] >= '0' && bytes[i + 3] <= '9') legacy_len = 4;
        wchar_t wide[2] = {};
        const int count = (i + legacy_len <= bytes.size())
            ? MultiByteToWideChar(54936, MB_ERR_INVALID_CHARS, bytes.data() + i,
                                  legacy_len, wide, 2) : 0;
        if (count > 0) {
            char converted[8];
            int n = WideCharToMultiByte(CP_UTF8, 0, wide, count, converted,
                                        sizeof converted, nullptr, nullptr);
            if (n > 0) out.append(converted, n);
            i += legacy_len;
        } else {
            out += "\xEF\xBF\xBD";
            ++i;
        }
    }
    return out;
#endif
}

// ------------------------------------------------------------------ UTF-8 工具

unsigned int utf8_decode_at(const std::string& s, size_t pos, size_t* len) {
    const unsigned char c = static_cast<unsigned char>(s[pos]);
    if (c < 0x80) { *len = 1; return c; }
    if ((c & 0xE0) == 0xC0 && pos + 1 < s.size()) {
        *len = 2;
        return ((c & 0x1Fu) << 6) | (static_cast<unsigned char>(s[pos + 1]) & 0x3Fu);
    }
    if ((c & 0xF0) == 0xE0 && pos + 2 < s.size()) {
        *len = 3;
        return ((c & 0x0Fu) << 12) | ((static_cast<unsigned char>(s[pos + 1]) & 0x3Fu) << 6) |
               (static_cast<unsigned char>(s[pos + 2]) & 0x3Fu);
    }
    if ((c & 0xF8) == 0xF0 && pos + 3 < s.size()) {
        *len = 4;
        return ((c & 0x07u) << 18) | ((static_cast<unsigned char>(s[pos + 1]) & 0x3Fu) << 12) |
               ((static_cast<unsigned char>(s[pos + 2]) & 0x3Fu) << 6) |
               (static_cast<unsigned char>(s[pos + 3]) & 0x3Fu);
    }
    *len = 1;
    return c;
}

std::string char_name(unsigned int cp) {
    switch (cp) {
        case 0x20: return "半角空格";
        case 0x09: return "制表符(Tab)";
        case 0x0A: return "换行";
        case 0x3000: return "全角空格";
        case 0x3A: return "半角冒号 :";
        case 0xFF1A: return "全角冒号 ：";
        case 0x2C: return "半角逗号 ,";
        case 0xFF0C: return "全角逗号 ，";
        case 0x2E: return "半角句点 .";
        case 0x3002: return "全角句号 。";
        case 0x28: return "半角左括号 (";
        case 0x29: return "半角右括号 )";
        case 0xFF08: return "全角左括号 （";
        case 0xFF09: return "全角右括号 ）";
        case 0x21: return "半角感叹号 !";
        case 0xFF01: return "全角感叹号 ！";
        case 0x3F: return "半角问号 ?";
        case 0xFF1F: return "全角问号 ？";
        case 0x3B: return "半角分号 ;";
        case 0xFF1B: return "全角分号 ；";
        case 0x22: return "半角双引号";
        case 0x201C: return "左双引号 “";
        case 0x201D: return "右双引号 ”";
        default: break;
    }
    if (cp >= 0xFF10 && cp <= 0xFF19) return "全角数字";
    if (cp >= 0xFF21 && cp <= 0xFF3A) return "全角大写字母";
    if (cp >= 0xFF41 && cp <= 0xFF5A) return "全角小写字母";
    if (cp >= 0xFF01 && cp <= 0xFF5E) return "全角字符";
    if (cp < 0x20) return "控制字符";
    if (cp < 0x7F) return "半角字符";
    return "字符";
}

// ------------------------------------------------------------------ 归一化

// 去掉 UTF-8 BOM，并把 CRLF / CR 统一成 LF
static std::string normalize_newlines(const std::string& raw) {
    std::string s = raw;
    if (s.size() >= 3 && (unsigned char)s[0] == 0xEF && (unsigned char)s[1] == 0xBB &&
        (unsigned char)s[2] == 0xBF) {
        s.erase(0, 3);
    }
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\r') {
            if (i + 1 < s.size() && s[i + 1] == '\n') continue; // CRLF 的 CR 丢弃
            out.push_back('\n');
        } else {
            out.push_back(s[i]);
        }
    }
    return out;
}

// 全角 → 半角：U+FF01..U+FF5E 减 0xFEE0 即为对应 ASCII；
// U+3000 全角空格、U+00A0 不间断空格（从网页/Word 粘贴常见）统一成半角空格
static std::string to_halfwidth(const std::string& line) {
    std::string out;
    out.reserve(line.size());
    for (size_t i = 0; i < line.size();) {
        size_t len = 0;
        unsigned int cp = utf8_decode_at(line, i, &len);
        if (cp >= 0xFF01 && cp <= 0xFF5E) {
            out.push_back(static_cast<char>(cp - 0xFEE0));
        } else if (cp == 0x3000 || cp == 0x00A0) {
            out.push_back(' ');
        } else {
            out.append(line, i, len);
        }
        i += len;
    }
    return out;
}

static std::string rtrim(const std::string& s) {
    size_t e = s.size();
    while (e > 0) {
        unsigned char c = static_cast<unsigned char>(s[e - 1]);
        if (c == ' ' || c == '\t' || c == '\v' || c == '\f') { --e; continue; }
        // 全角空格 E3 80 80
        if (e >= 3 && c == 0x80 && (unsigned char)s[e - 2] == 0x80 && (unsigned char)s[e - 3] == 0xE3) {
            e -= 3;
            continue;
        }
        break;
    }
    return s.substr(0, e);
}

// 去掉行首空白（含制表符、全角空格、不间断空格）
static std::string ltrim(const std::string& s) {
    size_t b = 0;
    while (b < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[b]);
        if (c == ' ' || c == '\t') { ++b; continue; }
        if (b + 2 < s.size() && c == 0xE3 && (unsigned char)s[b + 1] == 0x80 &&
            (unsigned char)s[b + 2] == 0x80) {
            b += 3;
            continue;
        }
        if (b + 1 < s.size() && c == 0xC2 && (unsigned char)s[b + 1] == 0xA0) {
            b += 2;
            continue;
        }
        break;
    }
    return s.substr(b);
}

// 是否为 system("pause") 之类的提示行
static std::string without_pause_prompt(std::string line) {
    static const char* keys[] = {"请按任意键继续", "输入任意键继续", "Press any key to continue", "按任意键继续"};
    for (const char* k : keys) {
        size_t pos = line.find(k);
        if (pos == std::string::npos) continue;
        size_t end = pos + std::char_traits<char>::length(k);
        while (end < line.size() && (line[end] == ' ' || line[end] == '.' || line[end] == ':' || line[end] == '!')) ++end;
        line.erase(pos, end - pos);
    }
    return line;
}

std::string normalize(const std::string& raw, const NormOpt& opt) {
    const std::string text = normalize_newlines(raw);
    std::vector<std::string> lines = split_lines_keep_empty(text);

    std::vector<std::string> kept;
    kept.reserve(lines.size());
    for (std::string& line : lines) {
        // 顺序很重要：先把全角/不间断空格转成半角空格，再裁掉首尾空白
        if (opt.punct_equiv) line = to_halfwidth(line);
        if (opt.trim_trailing) line = rtrim(line);
        if (opt.trim_leading) line = ltrim(line);
        if (opt.drop_pause) {
            line = without_pause_prompt(line);
            if (trim(line).empty()) continue;
        }
        if (opt.drop_blank && line.empty()) continue;
        kept.push_back(line);
    }
    if (opt.drop_first_line && !kept.empty()) kept.erase(kept.begin());

    std::string out;
    for (const std::string& l : kept) {
        out += l;
        out.push_back('\n');
    }
    return out;
}

std::vector<std::string> split_lines_keep_empty(const std::string& s) {
    std::vector<std::string> lines;
    std::string cur;
    for (char c : s) {
        if (c == '\n') {
            lines.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    if (!cur.empty()) lines.push_back(cur);
    if (lines.empty()) lines.push_back("");
    return lines;
}

// ------------------------------------------------------------------ 学号姓名

bool looks_like_header(const std::string& line) {
    if (line.find("请输入") != std::string::npos) return false;
    if (line.find("学号") != std::string::npos) return true;
    if (line.find("姓名") != std::string::npos) return true;
    if (line.find("班级") != std::string::npos) return true;
    int run = 0;
    for (size_t i = 0; i < line.size(); ++i) {
        if (std::isdigit(static_cast<unsigned char>(line[i]))) {
            if (++run >= 8) return true;
        } else {
            run = 0;
        }
    }
    return false;
}

std::string first_non_empty_line(const std::string& raw) {
    for (const std::string& l : split_lines_keep_empty(normalize_newlines(raw))) {
        std::string t = trim(rtrim(l));
        if (!t.empty()) return t;
    }
    return "";
}

void parse_id_name(const std::string& line, std::string& id, std::string& name) {
    id.clear();
    name.clear();
    // 学号：最长的连续数字串
    size_t best_b = std::string::npos, best_len = 0;
    for (size_t i = 0; i < line.size();) {
        if (std::isdigit(static_cast<unsigned char>(line[i]))) {
            size_t j = i;
            while (j < line.size() && std::isdigit(static_cast<unsigned char>(line[j]))) ++j;
            if (j - i > best_len) { best_len = j - i; best_b = i; }
            i = j;
        } else {
            ++i;
        }
    }
    if (best_b != std::string::npos && best_len >= 4) {
        id = line.substr(best_b, best_len);
        std::string rest = line.substr(0, best_b) + " " + line.substr(best_b + best_len);
        // 去掉“学号/姓名/班级”标签与常见分隔符
        for (const char* lab : {"学号", "姓名", "班级", "：", ":", "、", ",", "，", "|", "|", " "}) {
            for (std::string::size_type p = rest.find(lab); p != std::string::npos;
                 p = rest.find(lab, p)) {
                rest.erase(p, std::char_traits<char>::length(lab));
            }
        }
        name = trim(rest);
    } else {
        name = trim(line);
    }
}

// ------------------------------------------------------------------ 数字容差

// 把一行拆成“非数字骨架”和“数字列表”
static void split_numbers(const std::string& s, std::vector<std::string>* frames,
                          std::vector<double>* nums) {
    frames->clear();
    nums->clear();
    std::string cur;
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        bool is_num_start = std::isdigit(c) ||
                            ((c == '+' || c == '-') && i + 1 < s.size() &&
                             (std::isdigit(static_cast<unsigned char>(s[i + 1])) ||
                              s[i + 1] == '.'));
        if (is_num_start) {
            // 数字可能出现在标识符中（如 m^2），这里按“数字字面量”切分即可
            size_t j = i;
            if (s[j] == '+' || s[j] == '-') ++j;
            while (j < s.size() && std::isdigit(static_cast<unsigned char>(s[j]))) ++j;
            if (j < s.size() && s[j] == '.') {
                ++j;
                while (j < s.size() && std::isdigit(static_cast<unsigned char>(s[j]))) ++j;
            }
            if (j < s.size() && (s[j] == 'e' || s[j] == 'E')) {
                size_t k = j + 1;
                if (k < s.size() && (s[k] == '+' || s[k] == '-')) ++k;
                if (k < s.size() && std::isdigit(static_cast<unsigned char>(s[k]))) {
                    while (k < s.size() && std::isdigit(static_cast<unsigned char>(s[k]))) ++k;
                    j = k;
                }
            }
            frames->push_back(cur);
            cur.clear();
            nums->push_back(std::strtod(s.substr(i, j - i).c_str(), nullptr));
            i = j;
        } else {
            cur.push_back(s[i]);
            ++i;
        }
    }
    frames->push_back(cur);
}

// 容差比较：25.53 与 25.52 之差在二进制里可能略大于 0.01，边界上按相对误差放宽
static bool within_tol(double a, double b, double tol) {
    const double diff = std::fabs(a - b);
    if (diff <= tol) return true;
    const double scale = std::max(1.0, std::max(std::fabs(a), std::fabs(b)));
    return diff <= tol + 1e-9 * scale;
}

bool numeric_equal(const std::string& a, const std::string& b, double tol) {
    std::vector<std::string> fa, fb;
    std::vector<double> na, nb;
    split_numbers(a, &fa, &na);
    split_numbers(b, &fb, &nb);
    if (fa != fb || na.size() != nb.size()) return false;
    for (size_t i = 0; i < na.size(); ++i) {
        if (!within_tol(na[i], nb[i], tol)) return false;
    }
    return true;
}

// ------------------------------------------------------------------ 差异报告

std::string first_diff_detail(const std::string& expected, const std::string& actual) {
    std::vector<std::string> ea = split_lines_keep_empty(expected);
    std::vector<std::string> aa = split_lines_keep_empty(actual);
    size_t n = std::max(ea.size(), aa.size());
    for (size_t i = 0; i < n; ++i) {
        std::string e = i < ea.size() ? ea[i] : std::string("<无此行>");
        std::string a = i < aa.size() ? aa[i] : std::string("<无此行>");
        if (e == a) continue;

        std::ostringstream os;
        os << "第 " << (i + 1) << " 行不同\n";
        os << "  预期: " << e << "\n";
        os << "  实际: " << a << "\n";

        // 逐字符定位
        size_t pe = 0, pa = 0, idx = 0;
        size_t bad_pe = std::string::npos, bad_pa = std::string::npos;
        while (pe < e.size() || pa < a.size()) {
            size_t le = 0, la = 0;
            unsigned int ce = pe < e.size() ? utf8_decode_at(e, pe, &le) : 0xFFFFFFFFu;
            unsigned int ca = pa < a.size() ? utf8_decode_at(a, pa, &la) : 0xFFFFFFFFu;
            if (ce != ca) { bad_pe = pe; bad_pa = pa; break; }
            pe += le;
            pa += la;
            ++idx;
        }
        if (bad_pe != std::string::npos) {
            os << "  第 " << (idx + 1) << " 个字符起不同：";
            size_t le = 0, la = 0;
            if (bad_pe < e.size()) {
                unsigned int ce = utf8_decode_at(e, bad_pe, &le);
                os << "预期 ‘" << e.substr(bad_pe, le) << "’(" << char_name(ce);
                char buf[16];
                std::snprintf(buf, sizeof buf, " U+%04X", ce);
                os << buf << ")";
            } else {
                os << "预期 已结束";
            }
            os << " ； ";
            if (bad_pa < a.size()) {
                unsigned int ca = utf8_decode_at(a, bad_pa, &la);
                os << "实际 ‘" << a.substr(bad_pa, la) << "’(" << char_name(ca);
                char buf[16];
                std::snprintf(buf, sizeof buf, " U+%04X", ca);
                os << buf << ")";
            } else {
                os << "实际 已结束";
            }
            os << "\n";
        }
        return os.str();
    }
    return std::string();
}

std::string diff_report(const std::string& expected, const std::string& actual, int max_lines) {
    std::vector<std::string> ea = split_lines_keep_empty(expected);
    std::vector<std::string> aa = split_lines_keep_empty(actual);
    std::ostringstream os;
    int shown = 0;
    const size_t n = std::max(ea.size(), aa.size());
    for (size_t i = 0; i < n && shown < max_lines; ++i) {
        const std::string e = i < ea.size() ? ea[i] : std::string("<缺少该行>");
        const std::string a = i < aa.size() ? aa[i] : std::string("<缺少该行>");
        if (e == a) continue;
        os << "  第 " << (i + 1) << " 行\n";
        os << "    预期: " << e << "\n";
        os << "    实际: " << a << "\n";
        ++shown;
    }
    if (shown == 0 && ea.size() != aa.size()) {
        os << "  （行数不同：预期 " << ea.size() << " 行，实际 " << aa.size() << " 行）\n";
    }
    if (shown >= max_lines) os << "  ...（差异过多，仅显示前 " << max_lines << " 处）\n";
    return os.str();
}

} // namespace judge
