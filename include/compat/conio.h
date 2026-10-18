// include/compat/conio.h —— 兼容垫片
//
// 学生常用 VS 的 <conio.h>（_getch、_kbhit 等），这些头文件在 Linux 下不存在。
// 当判题器发现源码里出现 conio.h 时，会自动把本目录加入头文件搜索路径（可用 --no-win-compat 关闭），
// 让这类代码也能编译通过，从而正常参与判题。
#pragma once

// Windows 下系统自带真正的 <conio.h>，本垫片仅在类 Unix 平台生效。
#if defined(_WIN32)
#include_next <conio.h>
#else

#include <unistd.h>

// 读入一个字符（不回显）；EOF/-1 表示输入结束
static inline int _getch(void) {
    char c = 0;
    ssize_t n = ::read(0, &c, 1);
    return n == 1 ? static_cast<int>(static_cast<unsigned char>(c)) : -1;
}

static inline int getch(void) { return _getch(); }

// Linux 下没有键盘缓冲区，恒返回 0（表示没有按键）
static inline int _kbhit(void) { return 0; }
static inline int kbhit(void) { return 0; }

static inline void clrscr(void) {}
static inline void gotoxy(int, int) {}
static inline void textcolor(int) {}

#endif // _WIN32
