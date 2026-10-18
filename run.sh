#!/usr/bin/env bash
# ============================================================================
# 一键判题：配置 CMake → 编译判题器 → 判题 → 打印结论
#
#   ./run.sh                    # 判全部题目
#   ./run.sh --list             # 只看题目与用例，不判题
#   ./run.sh --strict           # 严格逐字符比对
#   ./run.sh --code /path/to/学生代码目录 --out /path/to/报告目录
#   ./run.sh --help             # 查看全部选项
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")"

BUILD_DIR="build"
JUDGE="$BUILD_DIR/judge"

echo "[1/3] 配置 CMake ..."
if ! cmake -S . -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Debug > /dev/null; then
    echo "CMake 配置失败" >&2
    exit 2
fi

echo "[2/3] 编译判题器 ..."
if ! cmake --build "$BUILD_DIR" -j "$(nproc 2>/dev/null || echo 4)" > /dev/null; then
    echo "判题器编译失败" >&2
    exit 2
fi

echo "[3/3] 开始判题 ..."
"$JUDGE" "$@"
rc=$?

echo
if [ "$rc" -eq 0 ]; then
    echo "==== 判题结束：全部通过 ===="
    exit 0
fi
echo "==== 判题结束：存在未通过（判题器退出码 $rc）===="
if [ "$rc" -eq 134 ]; then
    echo "（134 = assert 断言失败，这是判题程序表达“未通过”的方式，属正常判定路径）"
fi
exit 1
