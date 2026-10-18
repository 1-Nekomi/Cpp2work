// status.cpp —— 状态码文本 + 项目根目录定位
#include <cstdlib>
#include <filesystem>

#include "judge.h"
#include "text.h"

namespace fs = std::filesystem;

namespace judge {

const char* status_token(Status s) {
    switch (s) {
        case Status::Pass: return "PASS";
        case Status::WrongAnswer: return "WA";
        case Status::RunError: return "RE";
        case Status::Timeout: return "TLE";
        case Status::Missing: return "MISS";
    }
    return "??";
}

const char* status_text(Status s) {
    switch (s) {
        case Status::Pass: return "通过";
        case Status::WrongAnswer: return "答案错误";
        case Status::RunError: return "运行错误";
        case Status::Timeout: return "运行超时";
        case Status::Missing: return "缺少文件";
    }
    return "未知";
}

Status status_from_token(const std::string& t) {
    if (t == "PASS") return Status::Pass;
    if (t == "WA") return Status::WrongAnswer;
    if (t == "RE") return Status::RunError;
    if (t == "TLE") return Status::Timeout;
    return Status::Missing;
}

static bool looks_like_root(const std::string& dir) {
    if (dir.empty()) return false;
    std::error_code ec;
    if (!fs::is_directory(dir, ec)) return false;
    // 根目录特征：存在 tests/ 或 code/ 子目录
    return fs::is_directory(fs::path(dir) / "tests", ec) ||
           fs::is_directory(fs::path(dir) / "code", ec);
}

std::string find_project_root(const std::string& hint_exe_dir) {
    if (const char* env = std::getenv("JUDGE_ROOT")) {
#ifdef _WIN32
        const std::string root = acp_to_utf8(env);
#else
        const std::string root = env;
#endif
        if (looks_like_root(root)) return fs::absolute(root).lexically_normal().string();
    }
    std::vector<std::string> candidates;
    std::error_code ec;
    candidates.push_back(fs::current_path(ec).string());
    // 可执行文件所在目录及其上级（build/ 内运行的情况）
    if (!hint_exe_dir.empty()) {
        candidates.push_back(hint_exe_dir);
        fs::path p(hint_exe_dir);
        if (p.has_parent_path()) candidates.push_back(p.parent_path().string());
        if (p.parent_path().has_parent_path())
            candidates.push_back(p.parent_path().parent_path().string());
    }
    fs::path cur = fs::current_path(ec);
    for (int i = 0; i < 4 && cur.has_parent_path(); ++i) {
        candidates.push_back(cur.parent_path().string());
        cur = cur.parent_path();
    }
    for (const std::string& c : candidates) {
        if (looks_like_root(c)) return fs::absolute(c).lexically_normal().string();
    }
    return fs::current_path(ec).string();
}

} // namespace judge
