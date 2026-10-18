"""Workbench backend: settings, local model process, background jobs and views.

The HTTP layer lives in server.py. Keeping state and job dispatch here makes the
server small and lets tests exercise the workbench without opening a socket.
"""
from __future__ import annotations

import copy
import csv
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .ai import AIConfig, AIError, AIProvider
from .ai_review import attach_ai_to_candidates, run_output_review, run_review
from .judge_batch import run_batch
from .secrets import SecretError, protect, unprotect
from .workspace import (audit_workspace, compile_check, export_sources, prepare,
                        read_json, write_json)

SETTINGS_FILE = "ui-settings.json"
LOCAL_PROCESSES = {}
LOCAL_LOCK = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def settings_path(root: Path) -> Path:
    return root / SETTINGS_FILE


def has_batch(root: Path) -> bool:
    return (root / "batch.json").is_file()


def empty_summary() -> dict:
    return {"documents": 0, "readable": 0, "file_errors": 0, "candidates": 0,
            "auto_ready": 0, "needs_review": 0, "approved": 0, "rejected": 0,
            "document_reviews": 0, "compile_ok": 0, "compile_failed": 0,
            "ignored_files": 0}


def default_settings(root: Path) -> dict:
    project = Path.cwd()
    judge = project / "build-windows" / "judge.exe"
    if not judge.is_file() and (project / "build-windows" / "Debug" / "judge.exe").is_file():
        judge = project / "build-windows" / "Debug" / "judge.exe"
    return {
        "teacher": "",
        "submissions": "",
        "workspace": str(root),
        "exported": "",
        "reports": str(project / "report" / "imported"),
        "tests": str(project / "tests"),
        "judge": str(judge),
        "compiler": "g++",
        "ai_mode": "off",
        "ai_base_url": "https://api.openai.com/v1",
        "ai_model": "gpt-4o-mini",
        "api_key": "",
        "api_key_enc": "",
        "ai_timeout": 90,
        "local_model_path": "",
        "local_server_path": "",
        "local_port": 8080,
    }


def _persistable(settings: dict) -> dict:
    """Never write the plaintext key; only its DPAPI ciphertext is persisted."""
    return {k: v for k, v in settings.items() if k not in {"api_key", "api_key_error"}}


def _write_settings(root: Path, settings: dict):
    write_json(settings_path(root), _persistable(settings))


def load_settings(root: Path) -> dict:
    settings = default_settings(root)
    path = settings_path(root)
    if path.is_file():
        try:
            stored = read_json(path)
            if isinstance(stored, dict):
                settings.update(stored)
        except (OSError, ValueError):
            pass
    encrypted = str(settings.get("api_key_enc", "") or "")
    plaintext = str(settings.get("api_key", "") or "")
    settings["api_key"] = ""
    settings["api_key_enc"] = encrypted
    if encrypted:
        try:
            settings["api_key"] = unprotect(encrypted)
        except SecretError as exc:
            settings["api_key_error"] = str(exc)
    elif plaintext:
        # Migrate keys written by older versions. If DPAPI is unavailable,
        # keep the plaintext only in memory and tell the user to use an env var.
        try:
            settings["api_key_enc"] = protect(plaintext)
            settings["api_key"] = plaintext
            _write_settings(root, settings)
        except SecretError as exc:
            settings["api_key"] = plaintext
            settings["api_key_error"] = str(exc)
    return settings


def public_settings(settings: dict) -> dict:
    public = {k: v for k, v in settings.items() if k not in {"api_key", "api_key_enc"}}
    public["api_key_set"] = bool(settings.get("api_key") or settings.get("api_key_enc"))
    public["api_key"] = ""
    return public


def save_settings(root: Path, payload: dict) -> dict:
    current = load_settings(root)
    allowed = set(current) - {"api_key", "api_key_enc", "api_key_error"}
    for key, value in payload.items():
        if key in allowed:
            current[key] = value
    if payload.get("clear_api_key"):
        current["api_key"] = ""
        current["api_key_enc"] = ""
        current["api_key_error"] = ""
    elif payload.get("api_key") and payload.get("api_key") != "***":
        secret = str(payload["api_key"])
        try:
            current["api_key_enc"] = protect(secret)
            current["api_key"] = secret
            current["api_key_error"] = ""
        except SecretError as exc:
            raise SecretError("无法加密保存 API 密钥：" + str(exc) +
                              "。非 Windows 环境请使用 AI_API_KEY 环境变量。") from exc
    if not current.get("workspace"):
        current["workspace"] = str(root)
    _write_settings(root, current)
    return public_settings(current)


def provider_from_settings(root: Path, settings: dict | None = None) -> AIProvider:
    settings = settings or load_settings(root)
    mode = settings.get("ai_mode", "off")
    if mode == "off":
        raise AIError("请先在“设置”中启用 AI")
    base = str(settings.get("ai_base_url", "")).strip()
    model = str(settings.get("ai_model", "")).strip()
    if not base or not model:
        raise AIError("请先在“设置”中填写 AI 服务地址和模型名称")
    try:
        timeout = float(settings.get("ai_timeout", 90))
    except (TypeError, ValueError):
        timeout = 90.0
    key = str(settings.get("api_key") or os.environ.get("AI_API_KEY", ""))
    config = AIConfig(base.rstrip("/"), model, key, timeout)
    return AIProvider(config, root / "ai" / "cache")


def choose_path(mode: str, title: str, file_filter: str) -> str:
    if os.name != "nt":
        raise ValueError("当前系统不支持服务器端文件选择，请直接填写路径")
    title = title.replace("'", "''")
    if mode == "folder":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            f"$d.Description='{title}';"
            "$d.ShowNewFolderButton=$true;"
            "if($d.ShowDialog() -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}"
        )
    else:
        file_filter = (file_filter or "All files (*.*)|*.*").replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.OpenFileDialog;"
            f"$d.Title='{title}';$d.Filter='{file_filter}';$d.CheckFileExists=$true;"
            "if($d.ShowDialog() -eq 'OK'){[Console]::Out.Write($d.FileName)}"
        )
    result = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise ValueError("无法打开文件选择窗口：" + result.stderr.strip()[:500])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def _wait_port(port: int, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def start_local_ai(root: Path, settings: dict) -> dict:
    server = str(settings.get("local_server_path", "")).strip() or shutil.which("llama-server") or ""
    model = str(settings.get("local_model_path", "")).strip()
    if not model or not Path(model).is_file():
        raise ValueError("请选择本地模型文件（.gguf）")
    if not server or not Path(server).is_file():
        raise ValueError("请选择 llama-server 可执行文件，或先安装 llama.cpp")
    server = str(Path(server).resolve())
    model = str(Path(model).resolve())
    try:
        port = int(settings.get("local_port", 8080))
    except (TypeError, ValueError):
        port = 8080
    with LOCAL_LOCK:
        process = LOCAL_PROCESSES.get(port)
        if process and process.poll() is None:
            return {"running": True, "port": port, "base_url": f"http://127.0.0.1:{port}/v1"}
        log_path = root / "ai" / f"local-server-{port}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        command = [server, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-c", "4096"]
        process = subprocess.Popen(command, cwd=str(Path(server).parent), stdout=log, stderr=subprocess.STDOUT)
        log.close()
        LOCAL_PROCESSES[port] = process
    if not _wait_port(port, 90):
        with LOCAL_LOCK:
            process = LOCAL_PROCESSES.pop(port, None)
        if process and process.poll() is None:
            process.terminate()
        raise ValueError("本地模型服务启动超时，请查看 " + str(log_path))
    settings["ai_mode"] = "local"
    settings["ai_base_url"] = f"http://127.0.0.1:{port}/v1"
    settings["ai_model"] = "local"
    _write_settings(root, settings)
    return {"running": True, "port": port, "base_url": settings["ai_base_url"],
            "model": "local", "log": str(log_path)}


def stop_local_ai() -> dict:
    stopped = []
    with LOCAL_LOCK:
        for port, process in list(LOCAL_PROCESSES.items()):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            stopped.append(port)
            LOCAL_PROCESSES.pop(port, None)
    return {"stopped": stopped}


class JobManager:
    def __init__(self, root: Path):
        self.root = root
        self.jobs = {}
        self.lock = threading.Lock()
        self.current = None

    def start(self, action: str, params: dict):
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "action": action, "params": params, "status": "queued",
               "progress": [], "result": None, "error": None,
               "created_at": _now(), "updated_at": _now()}
        with self.lock:
            active = [j for j in self.jobs.values() if j['status'] in {'queued', 'running'}]
            if any(j['action'] == action or action == 'prepare' or j['action'] == 'prepare'
                   or {j['action'], action} <= {'check', 'export', 'audit', 'ai-review'} for j in active):
                raise ValueError('相同模块或共享工作区的写入任务正在运行，请稍后重试；批量判题与 AI 输出判定可同时运行')
            job['root'] = str(self.root)
            self.jobs[job_id] = job
            self.current = job_id
            if len(self.jobs) > 50:
                for key in list(self.jobs)[:-50]:
                    if self.jobs[key]['status'] not in {'queued', 'running'}:
                        self.jobs.pop(key, None)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job_id

    def _run(self, job):
        with self.lock:
            job["status"] = "running"
            job["started_at"] = _now()
            job["updated_at"] = _now()

        def progress(message, **kwargs):
            with self.lock:
                job["progress"].append(str(message))
                job["updated_at"] = _now()

        try:
            result = dispatch(Path(job['root']), job["action"], job["params"], progress)
            with self.lock:
                job["status"] = "done"
                job["result"] = result
                job["updated_at"] = _now()
        except Exception as exc:
            with self.lock:
                job["status"] = "error"
                job["error"] = str(exc)
                job["updated_at"] = _now()

    def get(self, job_id):
        with self.lock:
            return copy.deepcopy(self.jobs.get(job_id, {}))

    def all(self):
        with self.lock:
            return copy.deepcopy(list(self.jobs.values()))

    def running(self):
        return any(j['status'] in {'queued', 'running'} for j in self.all())

    def last(self):
        with self.lock:
            if not self.current:
                return {}
            return copy.deepcopy(self.jobs.get(self.current, {}))


def has_student_code(path: Path) -> bool:
    return path.is_dir() and any(p.is_dir() and (p / "code").is_dir() for p in path.iterdir())


def latest_export(workspace: Path) -> Path | None:
    base = workspace / "exports"
    if not base.is_dir():
        return None
    candidates = [p for p in base.iterdir() if has_student_code(p)]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def dispatch(root: Path, action: str, params: dict, progress):
    workspace = Path(params.get("workspace") or root).resolve()
    if action == "prepare":
        target = Path(params["out"])
        result = prepare(Path(params["teacher"]), Path(params["submissions"]), target)
        progress("导入完成。若输出目录不是当前工作目录，请用该目录重启工作台：--workspace " + str(target))
        return result
    if action == "check":
        return compile_check(workspace, params.get("compiler", "g++"),
                             int(params.get("jobs", 4)), progress=progress)
    if action == "audit":
        return audit_workspace(workspace)
    if action == "export":
        return export_sources(workspace, Path(params["out"]).resolve())
    if action == "ai-review":
        provider = provider_from_settings(root)
        stage = params.get("stage", "all")
        stages = ("triage", "requirements") if stage == "all" else (stage,)
        return run_review(workspace, provider, stages, params.get("only", "needs_review"),
                          int(params.get("max", 0)), bool(params.get("force")), progress=progress,
                          document_id=params.get('document_id'), candidate_id=params.get('candidate_id'))
    if action == "judge-batch":
        exported = Path(params["exported"]).resolve()
        if not has_student_code(exported):
            fallback = latest_export(workspace)
            if fallback is None:
                raise ValueError("导出目录中没有学生 code 子目录：" + str(exported) +
                                 "。请先导出，或选择包含 <学号>-<文档ID>/code 的目录")
            exported = fallback
            progress(f"所选导出目录没有学生源码，已自动使用最新导出：{exported}")
        num_tol = params.get("num_tol")
        extra = []
        try:
            problem = int(params.get("problem", 0) or 0)
        except (TypeError, ValueError):
            problem = 0
        if problem > 0:
            extra += ["--problem", str(problem)]
        for key, flag in [("pty", "--pty"), ("no_compile", "--no-compile"),
                          ("keep_waiting", "--keep-waiting"), ("verbose", "--verbose"),
                          ("no_skip_header", "--no-skip-header"), ("no_win_compat", "--no-win-compat")]:
            if params.get(key):
                extra.append(flag)
        return run_batch(exported, Path(params.get("tests") or workspace.parent / "tests"),
                         Path(params["out"]), params.get("judge", "build-windows/judge.exe"),
                         params.get("compiler", "g++"), int(params.get("timeout", 5000)),
                         float(num_tol) if num_tol not in (None, "") else None,
                         bool(params.get("strict")), extra_args=extra,
                         force=bool(params.get("force")), progress=progress)
    if action == "ai-output":
        provider = provider_from_settings(root)
        return run_output_review(workspace, Path(params["reports"]), Path(params.get("tests") or workspace.parent / "tests"),
                                 provider, params.get("only", "答案错误"),
                                 int(params.get("max", 0)), bool(params.get("force")), progress=progress)
    if action == "test-ai":
        provider = provider_from_settings(root)
        result = provider.chat_json("Only output JSON.", 'Return {"ok": true}')
        return {"ok": bool(result.get("ok")), "model": provider.config.model,
                "base_url": provider.config.base_url}
    raise ValueError("未知操作：" + action)


def _report_dir(settings: dict, document: dict):
    base = str(settings.get("reports", "")).strip()
    if not base:
        return None
    root = Path(base)
    if not root.is_dir():
        return None
    direct = root / f"{document.get('student_id', '')}-{document['id']}"
    if direct.is_dir():
        return direct
    prefix = str(document.get("student_id", ""))
    matches = [p for p in root.iterdir() if p.is_dir() and prefix and p.name.startswith(prefix + "-")]
    return matches[0] if matches else None


def read_csv(path: Path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _limited_text(path, limit=5000):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return text[:limit] + ("\n…（已截断）" if len(text) > limit else "")
    except OSError:
        return ""


def student_detail(root: Path, settings: dict, document_id: str):
    state = read_json(root / "batch.json")
    document = next((d for d in state["documents"] if d["id"] == document_id), None)
    if document is None:
        raise ValueError("找不到该学生")
    attach_ai_to_candidates(root, document)
    report = {"dir": None, "cases": [], "result": [], "ai_output_path": None}
    report_dir = _report_dir(settings, document)
    if report_dir:
        report["dir"] = str(report_dir)
        report["result"] = read_csv(report_dir / "result.csv")
        ai_output = {}
        ai_path = report_dir / "ai-output.json"
        if ai_path.is_file():
            try:
                ai_output = read_json(ai_path).get("items", {}) or {}
                report["ai_output_path"] = str(ai_path)
            except (OSError, ValueError):
                ai_output = {}
        for row in read_csv(report_dir / "cases.csv"):
            item = dict(row)
            item["expected_text"] = _limited_text(row.get("预期文件", "")) if row.get("结果") != "通过" else ""
            item["actual_text"] = _limited_text(row.get("实际输出", "")) if row.get("结果") != "通过" else ""
            item["diff_text"] = _limited_text(row.get("差异说明", "")) if row.get("结果") != "通过" else ""
            ai_item = ai_output.get(f"p{row.get('题号')}/{row.get('用例')}")
            if isinstance(ai_item, dict):
                item["ai_output"] = ai_item
            report["cases"].append(item)
    report['compilation'] = {}
    if report_dir:
        for row in report['result']:
            problem_id = str(row.get('题号', ''))
            if problem_id.isdigit():
                log_path = report_dir / 'log' / f'p{problem_id}.compile.log'
                report['compilation'][problem_id] = {
                    'status': row.get('状态', ''), 'path': str(log_path),
                    'log': _limited_text(log_path, 100000)}
    return {"document": document, "report": report,
            "assignment": read_json(root / 'assignment.json')}


def student_overview(root: Path, settings: dict):
    state = read_json(root / "batch.json")
    rows = []
    for document in state["documents"]:
        report_dir = _report_dir(settings, document)
        report = None
        if report_dir:
            result_rows = read_csv(report_dir / "result.csv")
            case_rows = read_csv(report_dir / "cases.csv")
            score = 0.0
            for row in result_rows:
                if str(row.get("题号", "")).isdigit():
                    try:
                        score += float(row.get("本题得分", row.get("得分", "0")) or 0)
                    except ValueError:
                        pass
            report = {"dir": str(report_dir), "score": round(score, 2),
                      "passed": sum(1 for r in case_rows if r.get("结果") == "通过"),
                      "cases": len(case_rows),
                      "status": "全部通过" if case_rows and all(r.get("结果") == "通过" for r in case_rows) else "存在未通过"}
        rows.append({"id": document["id"], "filename": document["filename"],
                     "student_id": document["student_id"], "error": document.get("error"),
                     "review": document.get("review"), "issues": document.get("issues", []),
                     "candidates": [{"id": c["id"], "problem_id": c.get("problem_id"),
                                     "state": c.get("state"), "issues": c.get("issues", []),
                                     "compile": (c.get("compile") or {}).get("status")}
                                    for c in document.get("candidates", [])],
                     "report": report})
    return rows
