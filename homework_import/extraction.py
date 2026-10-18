"""Conservative source selection and teacher-owned assignment drafts."""
from __future__ import annotations

import re
from .documents import sha

NUM = r"[0-9０-９一二三四五六七八九十百零〇两]+"
HEADING = re.compile(r"^\s*第\s*(" + NUM + r")\s*题\s*[：:；;、.．]?\s*")
ALT_HEADING = re.compile(r"^\s*(?:实验|题目|任务)\s*(" + NUM + r")\s*[：:；;、.．]?\s*$")
NUMBERED = re.compile(r"^\s*(" + NUM + r")\s*(?:[.．](?![0-9０-９])|[、)）])\s*")
RESULTS = re.compile(r"^\s*(?:[一二三四五六七八九十\d]+\s*[、.．]\s*)?(?:实验结果|实验情况|实验过程|作业答案|学生答案|参考答案)")
TASKS = re.compile(r"^\s*(?:[一二三四五六七八九十\d]+\s*[、.．]\s*)?(?:实验任务|实验内容|作业内容|题目要求|编程题|作业题)")
CAPTION = re.compile(r"^\s*(?:运行(?:结果)?截图|运行结果|程序(?:运行)?结果|结果截图|截图|源(?:代)?码|代码如下|源程序)\s*[：:：。]?\s*$")
NOTE = re.compile(r"^\s*[（(][^）)]{0,40}(?:截图|运行结果|结果截图|见下页|见下一页|下一页|下页|见后页)[^）)]{0,40}[）)]\s*$")
CODE_START = re.compile(r"^\s*(?:#\s*\w+|//|/\*|using\s+|(?:const\s+|static\s+|unsigned\s+|signed\s+)*(?:int|void|double|float|char|long|short|auto|bool|string|std::string)\b|(?:class|struct|enum|namespace|template|typedef)\b)")


def is_caption(text: str) -> bool:
    """A standalone caption or screenshot note, not source code."""
    return bool(CAPTION.fullmatch(text) or NOTE.fullmatch(text))


def code_like(text: str) -> bool:
    """True when a line carries more than blank space or stray delimiters."""
    masked = mask_cpp(text).strip()
    return bool(masked) and not re.fullmatch(r"[{}()\[\];,、]+", masked)


def number(text: str) -> int:
    if text.isdecimal():
        return int(text)
    digits = dict(zip("零〇一二三四五六七八九两", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2]))
    total, digit = 0, 0
    for c in text:
        if c in digits:
            digit = digits[c]
        elif c in "十百":
            total += (digit or 1) * (10 if c == "十" else 100)
            digit = 0
    return total + digit


def mask_cpp(code: str) -> str:
    """Mask comments, strings and raw strings, retaining positions/newlines."""
    token = re.compile(r'R"([^\s()\\]{0,16})\([\s\S]*?\)\1"|//[^\n]*|/\*[\s\S]*?(?:\*/|$)|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    return token.sub(lambda m: "".join("\n" if c == "\n" else " " for c in m.group()), code)


def source_issues(code: str) -> list[str]:
    issues = []
    masked = mask_cpp(code)
    mains = len(re.findall(r"\bmain\s*\(", masked))
    if mains != 1:
        issues.append(f"入口函数数量为{mains}，请检查缺失、多个版本或多文件程序")
    depth = 0
    for c in masked:
        depth += (c == "{") - (c == "}")
        if depth < 0:
            break
    if depth != 0:
        issues.append("花括号不平衡：可能是学生语法错误或提取不完整")
    if re.search(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\u00a0]", code):
        issues.append("源码含不可见或特殊空白字符，已原样保留")
    if re.search(r"[“”‘’；｛｝＃]", masked):
        issues.append("疑似全角源码标点，已原样保留，未自动修复")
    return issues


def line_spans(block: dict, start: int = 0):
    text = block["text"]
    offset = start
    for line in text[start:].split("\n"):
        yield {"block": block["id"], "start": offset, "end": offset + len(line), "text": line}
        offset += len(line) + 1


def assemble(spans: list[dict]) -> tuple[str, list[dict]]:
    code = "\n".join(s["text"] for s in spans) + ("\n" if spans else "")
    mapping = [{"line": i, "block": s["block"], "start": s["start"], "end": s["end"]}
               for i, s in enumerate(spans, 1)]
    return code, mapping


def candidate_from_segment(doc: dict, blocks: list[dict], problem: int | None,
                           heading_offset: int = 0, heading_id: str | None = None) -> dict:
    spans = []
    for b in blocks:
        spans.extend(line_spans(b, heading_offset if b["id"] == heading_id else 0))
    substantive = [s for s in spans if s["text"].strip() and not is_caption(s["text"])]
    first = next((i for i, s in enumerate(substantive) if CODE_START.match(s["text"])), None)
    issues = []
    if first is None:
        selected = []
        issues.append("没有定位到可编辑源码；请检查是否只有图片、缺交或代码形式特殊")
    else:
        initial = substantive[first]
        start = next(i for i, s in enumerate(spans) if s is initial)
        last = next(i for i in range(len(spans)-1, start-1, -1)
                    if spans[i]["text"].strip() and not is_caption(spans[i]["text"]))
        selected = spans[start:last+1]
        # Never silently remove a caption or prose in the middle of source.
        if any(is_caption(s["text"]) for s in selected):
            issues.append("代码范围中夹有说明文字，请核对提取范围")
        prefix = [s for s in substantive[:first] if s["block"] != heading_id]
        if prefix:
            issues.append("代码前存在未选入的文字，请确认不是源码的一部分")
    code, mapping = assemble(selected)
    if code:
        issues.extend(source_issues(code))
    selected_ids = {s["block"] for s in selected}
    block_by_id = {b["id"]: b for b in blocks}
    # Screenshots pasted at the end of an answer are expected by the template.
    # Only images that interrupt preceding/subsequent source need review.
    risky_images = []
    for i, span in enumerate(selected):
        block = block_by_id.get(span["block"])
        if block is None or not block["images"]:
            continue
        if any(code_like(later["text"]) for later in selected[i+1:]):
            risky_images.append(block["id"])
    if risky_images:
        issues.append("源码范围中间包含图片，可能夹有代码或打断代码；请核对图片内容")
    if any(b["flags"] for b in blocks) or doc["warnings"]:
        issues.append("原文含修订、特殊对象或未覆盖内容，需要核对")
    if any(b["context"] == "textbox" and b["id"] in selected_ids for b in blocks):
        issues.append("源码包含浮动文本框，需确认阅读顺序")
    if problem is None:
        issues.append("未找到明确题号，不能按出现顺序自动判定所属题目")
    return {"problem_id": problem, "code": code, "original_code": code, "source_map": mapping,
            "context_blocks": [b["id"] for b in blocks], "heading_block": heading_id,
            "issues": sorted(set(issues)), "state": "needs_review" if issues else "auto_ready",
            "compile": None, "review": None, "code_sha256": sha(code.encode("utf-8"))}


def extract_submission(doc: dict, expected: list[int]) -> dict:
    blocks = doc["blocks"]
    result = next((i for i, b in enumerate(blocks) if RESULTS.match(b["text"])), None)
    scope = blocks[result+1:] if result is not None else blocks
    headers = []
    for i, b in enumerate(scope):
        match = HEADING.match(b["text"]) or (ALT_HEADING.match(b["text"]) if result is not None else None)
        if match:
            headers.append((i, number(match.group(1)), match.end(), b["id"]))
    # Prefer explicit question labels. Numbered template reminders are not tasks.
    if not headers and result is not None:
        for i, b in enumerate(scope):
            match = NUMBERED.match(b["numbering"] + b["text"])
            if match and number(match.group(1)) in expected and b["context"] != "textbox":
                offset = max(0, match.end() - len(b["numbering"]))
                headers.append((i, number(match.group(1)), offset, b["id"]))
    candidates, issues = [], []
    if headers:
        for j, (start, problem, offset, heading) in enumerate(headers):
            end = headers[j+1][0] if j+1 < len(headers) else len(scope)
            candidate = candidate_from_segment(doc, scope[start:end], problem, offset, heading)
            if ALT_HEADING.fullmatch(scope[start]["text"]):
                candidate["issues"].append("题号使用实验/任务别名，请核对所属题目")
            candidates.append(candidate)
    else:
        candidates.append(candidate_from_segment(doc, scope, None))
        issues.append("没有识别到题号，请人工划分源码")
    selected = {m["block"] for c in candidates for m in c["source_map"]}
    uncovered = [b["id"] for b in scope if b["id"] not in selected
                 and re.search(r"#\s*include|\bmain\s*\(", mask_cpp(b["text"]))]
    if uncovered:
        issues.append("存在未提取的疑似代码块：" + ", ".join(uncovered))
    observed = [c["problem_id"] for c in candidates]
    for c in candidates:
        if c["problem_id"] not in expected:
            c["issues"].append("题号不在教师题目配置中")
        if observed.count(c["problem_id"]) > 1:
            c["issues"].append("同题有多个候选版本，请选择或重新划分")
        if result is None:
            c["issues"].append("未找到明确的答案区段，请核对是否混入题面示例")
        c["state"] = "needs_review" if c["issues"] else "auto_ready"
    missing = sorted(set(expected) - set(observed))
    if missing:
        issues.append("缺少题号：" + ", ".join(map(str, missing)))
    filename_ids = re.findall(r"(?<!\d)\d{8,14}(?!\d)", doc["filename"])
    front = "\n".join(b["text"] for b in blocks[:15])
    declared = re.search(r"(?:学生)?学号\s*[：:]?\s*(\d{8,14})", front)
    student_id = declared.group(1) if declared else (filename_ids[0] if filename_ids else "")
    if not student_id:
        issues.append("无法确定学生学号")
    if declared and filename_ids and declared.group(1) != filename_ids[0]:
        issues.append("报告学号与文件名不一致，请确认学生身份")
    return {"student_id": student_id, "candidates": candidates,
            "identity": {"filename_id": filename_ids[0] if filename_ids else "", "declared_id": declared.group(1) if declared else ""},
            "issues": issues + doc["warnings"], "uncovered_blocks": uncovered,
            "review": None, "error": None}


def assignment_draft(doc: dict) -> dict:
    blocks = doc["blocks"]
    start = next((i + 1 for i, b in enumerate(blocks) if TASKS.match(b["text"])), 0)
    end = next((i for i in range(start, len(blocks)) if RESULTS.match(blocks[i]["text"])), len(blocks))
    scope = blocks[start:end]
    headers = []
    for i, b in enumerate(scope):
        text = b["numbering"] + b["text"]
        match = HEADING.match(text) or NUMBERED.match(text)
        if match:
            headers.append((i, number(match.group(1)), match.end()))
    problems = []
    for j, (begin, pid, offset) in enumerate(headers):
        section = scope[begin:headers[j+1][0] if j+1 < len(headers) else len(scope)]
        statement = "\n".join((b["numbering"] + b["text"])[offset:] if k == 0 else b["text"]
                              for k, b in enumerate(section) if b["text"].strip()).strip()
        requirements = []
        for b in section:
            for sentence in re.split(r"(?<=[。；])", b["text"]):
                if re.search(r"要求|必须|不得|禁止|保留\s*\d+\s*位|符号常量|输入输出格式", sentence):
                    requirements.append({"text": sentence.strip(), "source_blocks": [b["id"]], "status": "proposed"})
        problems.append({"id": pid, "title": f"第{pid}题", "statement": statement,
                         "source_blocks": [b["id"] for b in section],
                         "images": [im for b in section for im in b["images"]],
                         "requirements": requirements, "format_policy": "undecided",
                         "teacher_notes": "", "tests": [], "checker": "not_configured"})
    globals_ = [{"text": b["text"], "source_blocks": [b["id"]]}
                for b in blocks if re.search(r"源程序.*注释|源代码必须|每题.*源代码|不得|禁止", b["text"])]
    return {"schema_version": 1, "status": "draft", "title": doc["filename"],
            "source": {k: doc[k] for k in ("path", "filename", "sha256")},
            "global_requirements": globals_, "problems": problems,
            "warnings": doc["warnings"] + ([] if headers else ["未识别到题目，请在复核页手动添加"]),
            "note": "草稿仅忠实整理教师原文和图片；未自动生成标准答案、评分权重或可执行检查器。",
            "confirmed_at": None}
