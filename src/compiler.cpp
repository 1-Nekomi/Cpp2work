// Incremental compilation: preprocess every input, cache code generation, always link.
#include "runner.h"
#include "text.h"

#include <cstdlib>
#include <filesystem>
#include <sstream>
#ifdef _WIN32
#include <windows.h>
#endif

namespace judge {
namespace {
namespace fs = std::filesystem;

// Length framing permits exact byte comparisons without relying on a weak hash.
void field(std::string& record, const std::string& value) {
    record += std::to_string(value.size()) + ":" + value;
}

fs::path compiler_path(const std::string& compiler) {
    std::error_code ec;
    if (fs::is_regular_file(fs::path(compiler), ec)) return fs::absolute(fs::path(compiler));
#ifdef _WIN32
    const auto name = utf8_to_wide(compiler);
    std::vector<wchar_t> path(32768);
    DWORD n = SearchPathW(nullptr, name.c_str(), L".exe", static_cast<DWORD>(path.size()),
                          path.data(), nullptr);
    if (n > 0 && n < path.size()) return fs::path(std::wstring(path.data(), n));
#else
    std::istringstream paths(std::getenv("PATH") ? std::getenv("PATH") : "");
    std::string dir;
    while (std::getline(paths, dir, ':')) {
        fs::path path = fs::path(dir.empty() ? "." : dir) / compiler;
        if (fs::is_regular_file(path, ec)) return fs::absolute(path);
    }
#endif
    return {}; // Compound compiler commands remain supported, but are not cached.
}
} // namespace

CompileResult compile_problem(const Options& opt, const std::vector<std::string>& srcs,
                              const std::string& exe) {
    CompileResult r;
    const fs::path target = fs::absolute(fs::path(exe));
    const fs::path build = target.parent_path();
    std::error_code ec;
    fs::create_directories(build, ec);
    const auto driver = compiler_path(opt.compiler);
    const std::string compiler = driver.empty() ? opt.compiler : shell_quote(driver.string());
    std::string identity;
    bool cacheable = !driver.empty();
    if (cacheable) {
        field(identity, driver.string());
        const auto size = fs::file_size(driver, ec);
        cacheable = !ec;
        field(identity, std::to_string(size));
        const auto modified = fs::last_write_time(driver, ec);
        cacheable = cacheable && !ec;
        field(identity, std::to_string(modified.time_since_epoch().count()));
    }

    // Do not change the judge process cwd. Only the compiler shell changes cwd.
    // ASCII relative output names avoid MinGW ld's broken Unicode absolute paths.
#ifdef _WIN32
    const std::string prefix = "cd /d " + shell_quote(build.string()) + " && ";
#else
    const std::string prefix = "cd " + shell_quote(build.string()) + " && ";
#endif
    auto run = [&](const std::string& command, std::string& log) {
        const std::string full = prefix + command;
        r.cmd += (r.cmd.empty() ? "" : "\n") + full;
        return run_shell_capture(full, &log);
    };
    auto fail = [&](const std::string& log) {
        r.log += to_utf8(log);
        if (log.empty()) r.log += "编译失败，编译器没有输出诊断信息\n";
        // A failed rebuild must never leave an old executable runnable by --no-compile.
        fs::remove(target, ec);
        return r;
    };

    bool all_utf8 = true, has_conio = false;
    for (const auto& source : srcs) {
        std::string contents;
        if (!read_file(source, &contents)) return fail("无法读取源码: " + source + "\n");
        all_utf8 = all_utf8 && is_utf8(contents);
        has_conio = has_conio || contents.find("conio.h") != std::string::npos;
    }
    const std::string flags = " -std=c++17 -O0 -Wall";
    std::string preprocess_flags = flags;
    if (!all_utf8) preprocess_flags += " -finput-charset=GB18030 -fexec-charset=UTF-8";
#ifndef _WIN32
    if (has_conio && opt.win_compat && file_exists(opt.root + "/include/compat/conio.h"))
        preprocess_flags += " -I " + shell_quote(opt.root + "/include/compat");
#else
    (void)has_conio;
#endif

    std::string objects;
    for (size_t i = 0; i < srcs.size(); ++i) {
        const std::string stem = target.stem().string() + ".unit" + std::to_string(i);
        const std::string preprocessed = stem + ".ii", object = stem + ".o";
        const std::string cache = (build / (stem + ".cache")).string();
        const std::string warnings = (build / (stem + ".warnings")).string();
        std::string preprocess_log;
        // -v records the actual compiler version, target and effective options in the key.
        const int rc = run(compiler + preprocess_flags + " -E -v " +
                           shell_quote(fs::absolute(fs::path(srcs[i])).string()) +
                           " -o " + shell_quote(preprocessed), preprocess_log);
        std::string contents;
        if (rc != 0 || !read_file((build / preprocessed).string(), &contents)) {
            fs::remove(fs::path(cache), ec);
            return fail(preprocess_log);
        }
        const std::string compile = compiler + flags +
            " -finput-charset=UTF-8 -fexec-charset=UTF-8 -c " + shell_quote(preprocessed) +
            " -o " + shell_quote(object);
        std::string key = "judge-object-cache-v1\n";
        field(key, identity);
        field(key, compile);
        field(key, preprocess_log);
        field(key, contents); // Includes all headers and expanded date/time macros.
        std::string object_bytes, saved, compile_log;
        std::string candidate = key;
        const bool have_object = read_file((build / object).string(), &object_bytes);
        field(candidate, object_bytes);
        if (!opt.rebuild && cacheable && have_object && read_file(cache, &saved) &&
            saved == candidate && read_file(warnings, &compile_log)) {
            ++r.cached_units;
            r.log += "编译缓存命中: " + srcs[i] + "\n";
        } else {
            fs::remove(fs::path(cache), ec);
            fs::remove(build / object, ec);
            if (run(compile, compile_log) != 0 ||
                !read_file((build / object).string(), &object_bytes)) return fail(compile_log);
            field(key, object_bytes); // Detect missing or modified cached object files.
            if (cacheable && write_file(warnings, compile_log)) write_file(cache, key);
        }
        r.log += to_utf8(preprocess_log) + to_utf8(compile_log);
        objects += " " + shell_quote(object);
        fs::remove(build / preprocessed, ec);
    }
    // Always relink: library changes and link failures must not be masked by a cache hit.
    fs::remove(target, ec);
    std::string link_log;
    std::string link = compiler + objects + " -o " + shell_quote(target.filename().string());
#ifndef _WIN32
    link += " -lm";
#endif
    if (run(link, link_log) != 0 || !file_exists(exe)) return fail(link_log);
    r.log += to_utf8(link_log);
    r.ok = true;
    return r;
}
} // namespace judge
