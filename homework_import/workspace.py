"""Persistent imports, review decisions and explicit export to the existing judge."""
from __future__ import annotations

import copy
import csv
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import VERSION
from .documents import read_document, sha
from .extraction import assignment_draft, extract_submission, line_spans, assemble, source_issues, mask_cpp
from .locking import locked


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_candidate_files(root, doc_id, candidate):
    folder = root / "sources" / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (candidate["id"] + ".cpp")).write_text(candidate["code"], encoding="utf-8", newline="\n")
    write_json(folder / (candidate["id"] + ".source-map.json"), {
        "code_sha256": sha(candidate["code"].encode("utf-8")),
        "mapping": candidate["source_map"], "normalization": "段落/手动换行转LF；原文字符不改写"})


def verify_source_document(document):
    """The imported snapshot is only trustworthy while the Word file is unchanged."""
    try:
        return sha(Path(document["path"]).read_bytes()) == document["sha256"]
    except (OSError, TypeError):
        return False


def verify_provenance(root, document, candidate):
    original = read_json(root / "documents" / (document["id"] + ".json"))
    if original["sha256"] != document["sha256"]:
        raise ValueError("文档快照与批次记录不一致")
    blocks = {b["id"]: b for b in original["blocks"]}
    lines = []
    for index, mapping in enumerate(candidate["source_map"], 1):
        block = blocks.get(mapping["block"])
        if block is None or mapping["line"] != index or not 0 <= mapping["start"] <= mapping["end"] <= len(block["text"]):
            raise ValueError("源码来源映射损坏，请重新提取")
        lines.append(block["text"][mapping["start"]:mapping["end"]])
    rebuilt = "\n".join(lines) + ("\n" if lines else "")
    if rebuilt != candidate["code"] or sha(rebuilt.encode("utf-8")) != candidate["code_sha256"]:
        raise ValueError("源码与原文映射不一致，拒绝继续；请在复核页重新选取原文")


def prepare(teacher: Path, submissions: Path, root: Path):
    root = root.resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("输出目录非空。为保留已有复核记录，请选择新的输出目录。")
    if not submissions.is_dir():
        raise ValueError("学生报告目录不存在")
    root.mkdir(parents=True, exist_ok=True)
    teacher_doc = read_document(teacher, root / "assets" / "teacher")
    assignment = assignment_draft(teacher_doc)
    write_json(root / "documents" / "teacher.json", teacher_doc)
    write_json(root / "assignment.json", assignment)
    expected = [p["id"] for p in assignment["problems"]]
    if len(expected) != len(set(expected)):
        assignment["warnings"].append("教师原文中存在重复题号，请先核对配置")
        write_json(root / "assignment.json", assignment)
    state = {"schema_version": 1, "importer_version": VERSION, "created_at": now(),
             "revision": 0, "submissions_directory": str(submissions.resolve()),
             "assignment_source_sha256": teacher_doc["sha256"],
             "assignment_sha256": sha((root / "assignment.json").read_bytes()),
             "documents": [], "ignored_files": [], "events": []}
    for file in sorted(submissions.iterdir(), key=lambda p: p.name.casefold()):
        if not file.is_file():
            continue
        if file.name.startswith("~$") or file.suffix.lower() not in {".doc", ".docx"}:
            state["ignored_files"].append(file.name)
            continue
        if file.resolve() == teacher.resolve():
            state["ignored_files"].append(file.name)
            continue
        ident = "d" + sha(file.name.encode("utf-8"))[:16]
        item = {"id": ident, "filename": file.name, "path": str(file.resolve())}
        try:
            doc = read_document(file, root / "assets" / ident)
            write_json(root / "documents" / (ident + ".json"), doc)
            item.update(extract_submission(doc, expected))
            item["sha256"] = doc["sha256"]
            item["block_count"] = len(doc["blocks"])
            for i, candidate in enumerate(item["candidates"], 1):
                candidate["id"] = f"c{i:03d}"
                save_candidate_files(root, ident, candidate)
        except Exception as exc:
            # A malformed single submission must never terminate the batch.
            item.update({"student_id": "", "candidates": [], "issues": [str(exc)],
                         "error": str(exc), "review": None, "sha256": None, "block_count": 0})
        state["documents"].append(item)
    groups = {}
    for doc in state["documents"]:
        if doc["student_id"]:
            groups.setdefault(doc["student_id"], []).append(doc)
    for sid, docs in groups.items():
        if len(docs) > 1:
            for doc in docs:
                doc["issues"].append("同一学号存在多份提交，请核对版本：" + sid)
    write_json(root / "batch.json", state)
    write_summary(root, state)
    return summary(state)


def summary(state):
    docs = state["documents"]
    candidates = [c for d in docs for c in d["candidates"]]
    return {"documents": len(docs), "readable": sum(not d["error"] for d in docs),
            "file_errors": sum(bool(d["error"]) for d in docs), "candidates": len(candidates),
            "auto_ready": sum(c["state"] == "auto_ready" for c in candidates),
            "needs_review": sum(c["state"] == "needs_review" for c in candidates),
            "approved": sum(c["state"] == "approved" for c in candidates),
            "rejected": sum(c["state"] == "rejected" for c in candidates),
            "document_reviews": sum(bool(d["issues"]) and not d["review"] for d in docs),
            "compile_ok": sum(bool(c["compile"]) and c["compile"]["status"] == "ok" for c in candidates),
            "compile_failed": sum(bool(c["compile"]) and c["compile"]["status"] == "failed" for c in candidates),
            "ignored_files": len(state["ignored_files"])}


def write_summary(root, state):
    stats = summary(state)
    write_json(root / "summary.json", stats)
    lines = ["# Word导入与复核报告", "", "这些是提取和编译检查结果，不是学生成绩。", "",
             "| 指标 | 数量 |", "|---|---|"]
    labels = {"documents": "提交文件", "readable": "成功读取", "file_errors": "读取异常", "candidates": "候选源码",
              "auto_ready": "源码提取规则检查通过", "needs_review": "源码待复核", "approved": "人工确认", "rejected": "人工排除",
              "document_reviews": "文档级待复核", "compile_ok": "语法编译通过", "compile_failed": "语法编译失败",
              "ignored_files": "忽略的临时或无关文件"}
    lines += [f"| {labels[k]} | {v} |" for k, v in stats.items()]
    lines += ["", "## 异常项目", ""]
    with (root / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["文件", "学号", "候选", "题号", "提取状态", "编译", "原因"])
        for doc in state["documents"]:
            if doc["issues"]:
                lines.append("- " + doc["filename"] + "：" + "；".join(doc["issues"]))
                writer.writerow([csv_safe(doc["filename"]), doc["student_id"], "文档", "", "待复核" if not doc["review"] else "已处理", "", csv_safe("；".join(doc["issues"]))])
            for c in doc["candidates"]:
                writer.writerow([csv_safe(doc["filename"]), doc["student_id"], c["id"], c["problem_id"], c["state"],
                                 c["compile"]["status"] if c["compile"] else "未检查", csv_safe("；".join(c["issues"]))])
                if c["state"] == "needs_review":
                    lines.append(f"- {doc['filename']} / {c['id']} / 第{c['problem_id']}题：" + "；".join(c["issues"]))
    lines += ["", "来源字符和行号映射位于 sources；原文内容及图片位于 documents 和 assets。", "",
              "自动就绪只代表提取规则检查通过，不代表功能正确。编译失败可能来自学生原文，不能自动归因为提取失败。"]
    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def csv_safe(s):
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) else s


@locked
def compile_check(root: Path, compiler="g++", jobs=4, progress=print):
    resolved = shutil.which(compiler)
    if not resolved:
        raise ValueError("找不到编译器：" + compiler)
    preflight = subprocess.run([resolved, "-x", "c++", "-std=c++17", "-fsyntax-only", "-"],
                              input=b"#include <iostream>\nint main(){return 0;}\n", capture_output=True, timeout=30)
    if preflight.returncode:
        raise ValueError("编译器环境检查失败；未对学生代码做结论：" + preflight.stderr.decode("utf-8", errors="replace")[:2000])
    state = read_json(root / "batch.json")
    jobs = max(1, min(jobs, 8))
    tasks = [(d, c) for d in state["documents"] for c in d["candidates"] if c["code"] and c["state"] != "rejected"]
    def work(task):
        d, c = task
        folder = root / "sources" / d["id"]
        verify_provenance(root, d, c)
        path = folder / (c["id"] + ".cpp")
        if path.exists() and path.read_bytes() != c["code"].encode("utf-8"):
            raise ValueError("生成源码已被外部修改，未覆盖：" + str(path))
        if not path.exists():
            save_candidate_files(root, d["id"], c)
        cmd = [resolved, "-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", str(path)]
        try:
            result = subprocess.run(cmd, cwd=folder, capture_output=True, timeout=25)
            log = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            status, exit_code = ("ok" if result.returncode == 0 else "failed"), result.returncode
        except subprocess.TimeoutExpired:
            log, status, exit_code = "编译超时（25秒）", "failed", None
        (folder / (c["id"] + ".compile.log")).write_text(log, encoding="utf-8")
        c["compile"] = {"status": status, "exit_code": exit_code, "log": log[:30000],
                        "checked_code_sha256": sha(c["code"].encode("utf-8")), "compiler": resolved, "checked_at": now()}
        if status == "failed":
            message = "语法编译失败，请对照原文区分学生错误与提取错误"
            if message not in c["issues"]:
                c["issues"].append(message)
            # A human may already have verified the same code despite its syntax error.
            if c["state"] != "approved":
                c["state"] = "needs_review"
        return d["id"], c["id"]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(work, task) for task in tasks]
        for i, future in enumerate(as_completed(futures), 1):
            future.result()
            if i % 25 == 0 or i == len(tasks):
                progress(f"编译检查 {i}/{len(tasks)}", flush=True)
    state["revision"] += 1
    write_json(root / "batch.json", state)
    write_summary(root, state)
    return summary(state)


def validate_assignment(config, confirmed=False):
    problems = config.get("problems")
    if not isinstance(problems, list) or not problems:
        raise ValueError("至少需要一道题")
    ids = []
    for p in problems:
        if not isinstance(p.get("id"), int) or not 1 <= p["id"] <= 9999:
            raise ValueError("题号必须是1到9999的整数")
        ids.append(p["id"])
        if not isinstance(p.get("statement"), str) or not p["statement"].strip():
            raise ValueError("题目正文不能为空")
        if p.get("format_policy") not in {"undecided", "strict", "tolerant", "results_only"}:
            raise ValueError("未知的格式要求")
        if confirmed and p["format_policy"] == "undecided":
            raise ValueError(f"请先确定第{p['id']}题的格式要求")
        if not isinstance(p.get("requirements"), list) or any(not isinstance(r.get("text"), str) for r in p["requirements"]):
            raise ValueError("题目要求格式错误")
    if len(ids) != len(set(ids)):
        raise ValueError("题号不能重复")


@locked
def decide(root: Path, payload: dict):
    state = read_json(root / "batch.json")
    if payload.get("revision") != state["revision"]:
        raise ValueError("页面数据已更新，请刷新后重试，避免覆盖其他复核操作")
    action = payload.get("action")
    note = str(payload.get("note", "")).strip()
    before = None
    if action == "assignment":
        old = read_json(root / "assignment.json")
        config = copy.deepcopy(payload["assignment"])
        confirmed = bool(payload.get("confirm"))
        validate_assignment(config, confirmed)
        # Source provenance is not user-editable metadata.
        config["source"], config["schema_version"] = old["source"], 1
        config["status"] = "confirmed" if confirmed else "draft"
        config["confirmed_at"] = now() if confirmed else None
        for p in config["problems"]:
            for requirement in p["requirements"]:
                requirement["status"] = "confirmed" if confirmed else "proposed"
        old_ids = {p["id"] for p in old["problems"]}
        new_ids = {p["id"] for p in config["problems"]}
        if old_ids != new_ids:
            for d in state["documents"]:
                d["issues"].append("教师配置题号已变更，请核对源码题号和完整性")
                d["review"] = None
        before = old
        write_json(root / "assignment.json", config)
        state["assignment_sha256"] = sha((root / "assignment.json").read_bytes())
    else:
        doc = next((d for d in state["documents"] if d["id"] == payload.get("document_id")), None)
        if doc is None:
            raise ValueError("找不到该提交")
        if not note:
            raise ValueError("请填写简短复核依据")
        if action in {"document_approve", "document_reject"}:
            if doc["error"] and action == "document_approve":
                raise ValueError("读取失败的文件不能确认就绪，请转换格式后重新导入")
            before = {"review": doc["review"], "student_id": doc["student_id"]}
            if "student_id" in payload and action == "document_approve":
                selected_id = str(payload["student_id"]).strip()
                if not re.fullmatch(r"[0-9]{1,30}", selected_id):
                    raise ValueError("确认的学号须为1到30位数字")
                doc["student_id"] = selected_id
            doc["review"] = {"action": action, "note": note, "at": now()}
        elif action in {"approve", "reject", "select", "add"}:
            candidate = next((c for c in doc["candidates"] if c["id"] == payload.get("candidate_id")), None)
            if candidate is None and action != "add":
                raise ValueError("找不到该源码候选")
            before = copy.deepcopy(candidate)
            if action in {"select", "add"}:
                original = read_json(root / "documents" / (doc["id"] + ".json"))
                blocks = original["blocks"]
                start = next((i for i, b in enumerate(blocks) if b["id"] == payload.get("start_block")), -1)
                end = next((i for i, b in enumerate(blocks) if b["id"] == payload.get("end_block")), -1)
                if start < 0 or end < start:
                    raise ValueError("原文块范围无效")
                spans = []
                for i in range(start, end+1):
                    b = blocks[i]
                    a = int(payload.get("start_offset", 0)) if i == start else 0
                    z = payload.get("end_offset") if i == end else None
                    z = len(b["text"]) if z in (None, "") else int(z)
                    if not 0 <= a <= z <= len(b["text"]):
                        raise ValueError("字符范围超出原文长度")
                    short = {**b, "text": b["text"][:z]}
                    spans.extend(line_spans(short, a))
                code, mapping = assemble(spans)
                if not code.strip():
                    raise ValueError("选中范围没有源码文字")
                if action == "add":
                    candidate = {"id": f"c{len(doc['candidates'])+1:03d}", "original_code": "", "heading_block": None}
                    doc["candidates"].append(candidate)
                candidate.update({"code": code, "source_map": mapping, "context_blocks": [b["id"] for b in blocks[start:end+1]],
                                  "issues": source_issues(code), "compile": None, "state": "needs_review",
                                  "code_sha256": sha(code.encode("utf-8"))})
            if action != "reject":
                pid = int(payload.get("problem_id", candidate.get("problem_id") or 0))
                config = read_json(root / "assignment.json")
                if pid not in {p["id"] for p in config["problems"]}:
                    raise ValueError("题号不在教师配置中")
                candidate["problem_id"] = pid
                if not candidate["code"].strip():
                    raise ValueError("空源码不能确认，请补选范围或排除此候选")
            if action == "approve":
                verify_provenance(root, doc, candidate)
                candidate["state"] = "approved"
            elif action == "reject":
                candidate["state"] = "rejected"
            candidate["review"] = {"action": action, "note": note, "at": now()}
            save_candidate_files(root, doc["id"], candidate)
        else:
            raise ValueError("未知复核操作")
    state["revision"] += 1
    event = {"at": now(), "revision": state["revision"], "request": payload, "before": before}
    state.setdefault("events", []).append(event)
    write_json(root / "batch.json", state)
    with (root / "review-events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    write_summary(root, state)
    return {"revision": state["revision"], "summary": summary(state)}


@locked
def export_sources(root: Path, out: Path):
    state = read_json(root / "batch.json")
    assignment_path = root / "assignment.json"
    expected_assignment_hash = state.get("assignment_sha256")
    if not expected_assignment_hash:
        raise ValueError("工作目录缺少配置校验信息，请重新确认教师配置后再导出")
    if sha(assignment_path.read_bytes()) != expected_assignment_hash:
        raise ValueError("教师题目配置在复核页面之外被修改，请重新确认后再导出")
    config = read_json(assignment_path)
    validate_assignment(config, confirmed=True)
    if config["status"] != "confirmed":
        raise ValueError("教师题目配置仍为草稿，请在复核页面确认后导出")
    if out.exists() and any(out.iterdir()):
        raise ValueError("导出目录非空，请选择新目录以免覆盖旧源码")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": now(), "assignment": config, "exported": [], "skipped": []}
    expected = {p["id"] for p in config["problems"]}
    active_ids = [d["student_id"] for d in state["documents"] if not d["error"] and
                  not (d["review"] and d["review"]["action"] == "document_reject")]
    for d in state["documents"]:
        reason = None
        if d["error"]:
            reason = "文档读取失败"
        elif not verify_source_document(d):
            reason = "原始Word文件已变化或无法读取，请重新导入或核对"
        elif d["review"] and d["review"]["action"] == "document_reject":
            reason = "人工排除整份提交"
        elif d["issues"] and not d["review"]:
            reason = "文档级异常尚未复核"
        elif active_ids.count(d["student_id"]) > 1:
            reason = "同一学号有多份有效提交，请修正身份或排除旧版本"
        candidates = [c for c in d["candidates"] if c["state"] != "rejected"]
        ids = [c["problem_id"] for c in candidates]
        if len(ids) != len(set(ids)):
            reason = "同题仍有多个未排除版本"
        for c in candidates:
            blocked = reason or ("源码尚未复核" if c["state"] not in {"approved", "auto_ready"} else None)
            if c["problem_id"] not in expected:
                blocked = "题号不在教师配置中"
            if not blocked:
                try:
                    verify_provenance(root, d, c)
                except (ValueError, KeyError, OSError) as exc:
                    blocked = str(exc)
            if blocked:
                manifest["skipped"].append({"file": d["filename"], "candidate": c["id"], "reason": blocked})
                continue
            folder = out / ((d["student_id"] or "unknown") + "-" + d["id"]) / "code"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"p{c['problem_id']}.cpp"
            target.write_text(c["code"], encoding="utf-8", newline="\n")
            manifest["exported"].append({"document": d["id"], "candidate": c["id"], "problem_id": c["problem_id"],
                                         "path": str(target.resolve()), "code_sha256": sha(c["code"].encode("utf-8")),
                                         "source_sha256": d["sha256"], "state": c["state"]})
        if not candidates:
            manifest["skipped"].append({"file": d["filename"], "reason": reason or "没有可导出的源码"})
        for missing in sorted(expected - set(ids)):
            manifest["skipped"].append({"file": d["filename"], "problem_id": missing, "reason": "该题没有未排除的源码候选"})
    write_json(out / "manifest.json", manifest)
    return {"exported": len(manifest["exported"]), "skipped": len(manifest["skipped"])}


@locked
def audit_workspace(root: Path):
    """Integrity/coverage evidence, deliberately not a claimed accuracy percentage."""
    state = read_json(root / "batch.json")
    result = {"checked_at": now(), "documents_checked": 0, "candidates_checked": 0,
              "mapped_lines": 0, "original_main_markers": 0, "selected_main_markers": 0,
              "errors": [], "note": "字符回溯一致性和入口数量检查不能证明题号归属、提取完整性或学生答案正确。"}
    assignment = read_json(root / "assignment.json")
    teacher = read_json(root / "documents" / "teacher.json")
    try:
        if sha(Path(teacher["path"]).read_bytes()) != teacher["sha256"] or assignment["source"]["sha256"] != teacher["sha256"]:
            result["errors"].append("教师原件或题目配置来源发生变化")
        if state.get("assignment_sha256") and sha((root / "assignment.json").read_bytes()) != state["assignment_sha256"]:
            result["errors"].append("教师题目配置在复核页面之外被修改")
    except OSError as exc:
        result["errors"].append("无法核对教师原件或配置：" + str(exc))
    for d in state["documents"]:
        if d["error"]:
            result["errors"].append(d["filename"] + "：读取失败，无法验证")
            continue
        result["documents_checked"] += 1
        doc = read_json(root / "documents" / (d["id"] + ".json"))
        try:
            if sha(Path(d["path"]).read_bytes()) != d["sha256"]:
                result["errors"].append(d["filename"] + "：原始文件已变化")
        except OSError as exc:
            result["errors"].append(d["filename"] + "：无法读取原件：" + str(exc))
        text = "\n".join(b["text"] for b in doc["blocks"])
        result["original_main_markers"] += len(re.findall(r"\bmain\s*\(", mask_cpp(text)))
        for c in d["candidates"]:
            result["candidates_checked"] += 1
            result["mapped_lines"] += len(c["source_map"])
            if c["state"] != "rejected":
                result["selected_main_markers"] += len(re.findall(r"\bmain\s*\(", mask_cpp(c["code"])))
            try:
                verify_provenance(root, d, c)
                source = root / "sources" / d["id"] / (c["id"] + ".cpp")
                if source.read_bytes() != c["code"].encode("utf-8"):
                    raise ValueError("生成源码文件与导入记录不一致")
            except (ValueError, KeyError, OSError) as exc:
                result["errors"].append(d["filename"] + "/" + c["id"] + "：" + str(exc))
    write_json(root / "integrity.json", result)
    return result
