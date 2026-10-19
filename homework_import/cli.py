"""Command-line entry points for import, review, AI assistance and batch judging."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import VERSION
from .ai import AIError, provider_from_env
from .ai_review import run_output_review, run_review
from .judge_batch import run_batch
from .workspace import (audit_workspace, compile_check, export_sources, prepare, read_json,
                        summary)


def build_parser():
    parser = argparse.ArgumentParser(prog="cpp-homework-import",
                                     description="Word作业批量提取、教师题目草稿和人工复核（不自动评分）")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="导入教师模板和学生报告，输出到新的工作目录")
    p.add_argument("--teacher", type=Path, required=True)
    p.add_argument("--submissions", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("imports/new-batch"))
    for command, help_text in [("check", "仅语法编译，不执行学生程序"),
                               ("review", "打开本地图形工作台（含人工复核页）"),
                               ("status", "查看导入统计"),
                               ("audit", "核对原件哈希与逐行字符来源"),
                               ("export", "导出就绪源码到现有判题器目录格式")]:
        p = sub.add_parser(command, help=help_text)
        p.add_argument("--workspace", type=Path, default=Path("imports/validation-final"))
        if command == "check":
            p.add_argument("--compiler", default="g++")
            p.add_argument("--jobs", type=int, default=4)
        elif command == "review":
            p.add_argument("--port", type=int, default=0)
            p.add_argument("--open", action="store_true", help="在默认浏览器打开工作台")
        elif command == "export":
            p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("ai-review", help="调用AI辅助检查提取异常和源码要求（只给建议，不改复核状态）")
    p.add_argument("--workspace", type=Path, default=Path("imports/validation-final"))
    p.add_argument("--stage", choices=["triage", "requirements", "all"], default="all")
    p.add_argument("--only", choices=["needs_review", "all"], default="needs_review")
    p.add_argument("--max", type=int, default=0)
    p.add_argument("--force", action="store_true", help="忽略缓存重新调用AI")
    p.add_argument("--base-url", help="覆盖 AI_BASE_URL")
    p.add_argument("--model", help="覆盖 AI_MODEL")
    p = sub.add_parser("judge-batch", help="对导出目录逐个学生运行现有判题器并汇总")
    p.add_argument("--exported", type=Path, required=True)
    p.add_argument("--tests", type=Path, default=Path("tests"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--judge", default="build-windows/judge.exe")
    p.add_argument("--compiler", default="g++")
    p.add_argument("--timeout", type=int, default=5000)
    p.add_argument("--num-tol", type=float)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--force", action="store_true", help="重新判已完成的学生，不使用已有报告")
    p.add_argument("--problem", type=int, default=0, help="只判第 N 题（0=全部）")
    p.add_argument("--pty", action="store_true")
    p.add_argument("--no-compile", action="store_true")
    p.add_argument("--rebuild", action="store_true", help="忽略编译缓存；同时重新判题")
    p.add_argument("--compile-jobs", type=int, choices=range(1, 9), default=2)
    p.add_argument("--keep-waiting", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--no-skip-header", action="store_true")
    p.add_argument("--no-win-compat", action="store_true")
    p = sub.add_parser("ai-output", help="对判题失败用例做AI输出语义判定（只给建议，不改成绩）")
    p.add_argument("--workspace", type=Path, default=Path("imports/validation-final"))
    p.add_argument("--reports", type=Path, required=True, help="judge-batch 的输出目录")
    p.add_argument("--tests", type=Path, default=Path("tests"))
    p.add_argument("--only", default="答案错误", help="只处理该结果类型；空字符串表示全部")
    p.add_argument("--max", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--base-url", help="覆盖 AI_BASE_URL")
    p.add_argument("--model", help="覆盖 AI_MODEL")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.teacher, args.submissions, args.out)
        elif args.command == "ai-review":
            root = args.workspace.resolve()
            provider = provider_from_env(root / "ai" / "cache", args.base_url, args.model)
            stages = ("triage", "requirements") if args.stage == "all" else (args.stage,)
            result = run_review(root, provider, stages, args.only, args.max, args.force)
        elif args.command == "judge-batch":
            extra = ["--compile-jobs", str(args.compile_jobs)]
            if args.rebuild:
                extra.append("--rebuild")
            if args.problem > 0:
                extra += ["--problem", str(args.problem)]
            for key, flag in [("pty", "--pty"), ("no_compile", "--no-compile"),
                              ("keep_waiting", "--keep-waiting"), ("verbose", "--verbose"),
                              ("no_skip_header", "--no-skip-header"), ("no_win_compat", "--no-win-compat")]:
                if getattr(args, key):
                    extra.append(flag)
            result = run_batch(args.exported, args.tests, args.out, args.judge,
                               args.compiler, args.timeout, args.num_tol, args.strict, extra_args=extra,
                               force=args.force or args.rebuild)
        elif args.command == "ai-output":
            root = args.workspace.resolve()
            provider = provider_from_env(root / "ai" / "cache", args.base_url, args.model)
            result = run_output_review(root, args.reports.resolve(), args.tests.resolve(),
                                       provider, args.only, args.max, args.force)
        else:
            root = args.workspace.resolve()
            if args.command == "status":
                result = summary(read_json(root / "batch.json"))
            elif args.command == "audit":
                result = audit_workspace(root)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1 if result["errors"] else 0
            elif args.command == "check":
                result = compile_check(root, args.compiler, args.jobs)
            elif args.command == "export":
                result = export_sources(root, args.out.resolve())
            else:
                from .server import serve
                serve(root, args.port, args.open)
                return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, AIError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
