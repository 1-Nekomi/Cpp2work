"""Run the existing C++ judge over an exported batch and aggregate class results."""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _student_summary(student: Path, target: Path, exit_code):
    result_rows = _read_csv(target / "result.csv")
    case_rows = _read_csv(target / "cases.csv")
    score = 0.0
    student_id = student_name = ""
    problem_count = 0
    for row in result_rows:
        if not str(row.get("题号", "")).isdigit():
            continue
        problem_count += 1
        student_id = student_id or row.get("学号", "")
        student_name = student_name or row.get("姓名", "")
        try:
            score += float(row.get("本题得分", row.get("得分", "0")) or 0)
        except ValueError:
            pass
    passed = sum(1 for row in case_rows if row.get("结果") == "通过")
    has_report = (target / "summary.md").is_file()
    status = ("无报告" if not has_report else
              "全部通过" if case_rows and passed == len(case_rows) else "存在未通过")
    return {"student_dir": student.name, "student_id": student_id, "student_name": student_name,
            "problems": problem_count, "cases": len(case_rows), "passed": passed,
            "score": round(score, 2), "exit_code": exit_code,
            "report": str(target), "status": status}


def run_batch(exported: Path, tests: Path, out: Path, judge: str,
              compiler="g++", timeout_ms=5000, num_tol=None, strict=False,
              judge_timeout=900, extra_args=None, force=False, progress=print) -> dict:
    exported = exported.resolve()
    tests = tests.resolve()
    out = out.resolve()
    if not exported.is_dir():
        raise ValueError("导出目录不存在：" + str(exported))
    if not tests.is_dir():
        raise ValueError("测试用例目录不存在：" + str(tests))
    judge_path = Path(judge)
    if not judge_path.is_absolute():
        candidates = [Path.cwd() / judge_path, Path.cwd() / "build-windows" / judge_path,
                      Path.cwd() / "build" / judge_path]
        found = next((p for p in candidates if p.is_file()), None)
        if found is None:
            which = shutil.which(str(judge))
            found = Path(which) if which else None
        judge_path = found or judge_path
    if not judge_path.is_file():
        raise ValueError("找不到判题器：" + str(judge))
    out.mkdir(parents=True, exist_ok=True)
    root = tests.parent
    students = sorted([p for p in exported.iterdir() if p.is_dir() and (p / "code").is_dir()],
                      key=lambda p: p.name.casefold())
    if not students:
        raise ValueError("导出目录中没有学生 code 子目录：" + str(exported) +
                         "。请先导出，或选择包含 <学号>-<文档ID>/code 的目录")
    results = []
    skipped = 0
    for i, student in enumerate(students, 1):
        target = out / student.name
        target.mkdir(parents=True, exist_ok=True)
        if not force and (target / "summary.md").is_file() and (target / "result.csv").is_file():
            summary = _student_summary(student, target, None)
            summary["skipped"] = True
            results.append(summary)
            skipped += 1
            progress(f"跳过已完成 {i}/{len(students)} {student.name}：{summary['passed']}/{summary['cases']} 用例，{summary['score']:.1f} 分", flush=True)
            continue
        command = [str(judge_path), "--root", str(root), "--code", str(student / "code"),
                   "--tests", str(tests), "--out", str(target), "--compiler", compiler,
                   "--timeout", str(timeout_ms)]
        if num_tol is not None:
            command += ["--num-tol", str(num_tol)]
        if strict:
            command.append("--strict")
        command += list(extra_args or [])
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True, timeout=judge_timeout)
            exit_code = completed.returncode
            log = _decode(completed.stdout) + _decode(completed.stderr)
        except subprocess.TimeoutExpired as exc:
            exit_code = None
            log = "判题超时（" + str(judge_timeout) + "秒）\n" + _decode(exc.stdout or b"") + _decode(exc.stderr or b"")
        (target / "run.log").write_text(log, encoding="utf-8")
        summary = _student_summary(student, target, exit_code)
        results.append(summary)
        progress(f"判题 {i}/{len(students)} {student.name}：{summary['passed']}/{summary['cases']} 用例，{summary['score']:.1f} 分", flush=True)
    report = {"created_at": _now(), "exported": str(exported), "tests": str(tests),
              "judge": str(judge_path), "strict": strict, "num_tol": num_tol,
              "skipped": skipped, "students": results}
    (out / "batch-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "class-summary.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["学生目录", "学号", "姓名", "题目数", "用例数", "通过", "得分", "状态", "报告"])
        for row in results:
            writer.writerow([row["student_dir"], row["student_id"], row["student_name"], row["problems"],
                             row["cases"], row["passed"], row["score"], row["status"], row["report"]])
    return {"students": len(results), "cases": sum(r["cases"] for r in results),
            "passed": sum(r["passed"] for r in results),
            "failed_students": sum(1 for r in results if r["passed"] != r["cases"]),
            "skipped": skipped, "output": str(out)}
