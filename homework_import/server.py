"""Loopback-only HTTP layer for the workbench and manual review page.

No uploads to an external service. State/job logic lives in workbench.py.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .ai import AIError
from .ai_review import attach_ai_to_candidates
from .secrets import SecretError
from .workbench import (JobManager, choose_path, empty_summary, has_batch, load_settings,
                        public_settings, read_csv, save_settings, start_local_ai, stop_local_ai,
                        student_detail, student_overview)
from .workspace import decide, export_sources, read_json, summary
from .managed import managed_config, folder_signature, sync_folder, managed_export
from .test_cases import list_cases, read_case, update_case


def make_server(root: Path, port=0, sync_interval=2):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    launch_root = root
    remembered = load_settings(root).get('bound_workspace')
    if (root / 'work' / 'batch.json').is_file():
        root = root / 'work'
    elif not managed_config(root) and remembered and (Path(remembered) / 'batch.json').is_file():
        root = Path(remembered).resolve()
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    active = {"root": root}
    workbench = (Path(__file__).parent / "workbench.html").read_text(encoding="utf-8")
    review = (Path(__file__).parent / "review.html").read_text(encoding="utf-8")
    jobs = JobManager(root)
    stop_watcher = threading.Event()
    sync_status = {'status': 'idle', 'message': ''}

    def tests_root(root):
        value = str(load_settings(root).get('tests') or '').strip()
        if not value:
            raise ValueError('请先在设置中选择测试用例目录')
        return Path(value).expanduser().resolve()

    def completed(job, result):
        if job['action'] == 'prepare' and job['params'].get('managed'):
            with lock:
                active['root'] = Path(result['workspace'])
                jobs.root = active['root']
                save_settings(launch_root, {'bound_workspace': result['workspace']})
                sync_status.update(status='watching', message='已绑定，正在监测学生报告目录')
    jobs.on_complete = completed

    def watch():
        observed = applied = None
        while not stop_watcher.wait(sync_interval):
            with lock:
                try:
                    current = active['root']
                    if not managed_config(current):
                        sync_status.update(status='idle', message='')
                        observed = applied = None
                        continue
                    if jobs.running():
                        sync_status.update(status='waiting', message='任务运行中；目录变化将在任务结束后同步')
                        continue
                    signature = (str(current), folder_signature(current))
                    if signature != observed:
                        observed = signature
                        sync_status.update(status='settling', message='检测到目录变化，等待文件写入稳定')
                        continue
                    if signature != applied:
                        result = sync_folder(current)
                        applied = signature
                        sync_status.update(status='watching', message='自动同步已启用：新增 {added}，更新 {updated}，移除 {removed}'.format(**result))
                    else:
                        if sync_status['status'] != 'watching':
                            sync_status.update(status='watching', message='自动同步已启用，正在监测学生报告目录')
                except (OSError, ValueError, KeyError) as exc:
                    sync_status.update(status='error', message='同步暂未完成，将自动重试：' + str(exc))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, content, content_type="application/json; charset=utf-8", status=200):
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False).encode("utf-8")
            elif isinstance(content, str):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)

        def authorized(self, query):
            return secrets.compare_digest(query.get("token", [""])[0], token)

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if not self.authorized(query):
                return self.respond({"error": "请使用启动命令输出的完整本地链接"}, status=403)
            try:
                with lock:
                    root = active["root"]
                    if parsed.path in {"/", "/review"}:
                        page = workbench if parsed.path == "/" else review
                        return self.respond(page.replace("__TOKEN__", token), "text/html; charset=utf-8")
                    if parsed.path == "/api/ui/state":
                        settings = load_settings(root)
                        if not has_batch(root):
                            return self.respond({"sync": dict(sync_status), "workspace": str(root), "summary": empty_summary(),
                                                 "assignment_status": "missing",
                                                 "settings": public_settings(settings),
                                                 "students": [], "jobs": jobs.all(), "job": jobs.last(), "missing": True})
                        state = read_json(root / "batch.json")
                        return self.respond({"sync": dict(sync_status), "workspace": str(root), "summary": summary(state),
                                             "assignment_status": read_json(root / "assignment.json").get("status"),
                                             "settings": public_settings(settings),
                                             "students": student_overview(root, settings),
                                             "jobs": jobs.all(), "job": jobs.last()})
                    if parsed.path == '/api/ui/test-cases':
                        tests = tests_root(root)
                        problem, name = query.get('problem', [''])[0], query.get('name', [''])[0]
                        if problem and name:
                            return self.respond({'directory': str(tests), 'case': read_case(tests, problem, name)})
                        assignment = read_json(root / 'assignment.json') if has_batch(root) else {}
                        return self.respond(list_cases(tests, assignment))
                    if parsed.path == "/api/ui/student":
                        if not has_batch(root):
                            raise ValueError("当前工作目录还没有导入批次，请先使用“导入”")
                        ident = query.get("id", [""])[0]
                        return self.respond(student_detail(root, load_settings(root), ident))
                    if parsed.path == "/api/ui/job":
                        job_id = query.get("id", [""])[0]
                        return self.respond(jobs.get(job_id) if job_id else {"jobs": jobs.all()})
                    if parsed.path == "/api/ui/settings":
                        return self.respond(public_settings(load_settings(root)))
                    if parsed.path == "/api/ui/artifact":
                        name = query.get("name", [""])[0]
                        if name not in {"ai-review", "ai-requirements", "ai-output"}:
                            raise ValueError("未知的结果类型")
                        settings = load_settings(root)
                        path = (root / (name + ".csv") if name in {"ai-review", "ai-requirements"}
                                else Path(settings.get("reports", "")) / "ai-output.csv")
                        return self.respond({"name": name, "path": str(path), "rows": read_csv(path)})
                    if parsed.path in {"/api/state", "/api/document", "/asset"} and not has_batch(root):
                        raise ValueError("当前工作目录还没有导入批次，请先使用“导入”")
                    if parsed.path == "/api/state":
                        state = read_json(root / "batch.json")
                        docs = []
                        for d in state["documents"]:
                            docs.append({k: v for k, v in d.items() if k != "candidates"})
                            docs[-1]["candidates"] = [{k: c[k] for k in ("id", "problem_id", "state", "issues")} for c in d["candidates"]]
                        return self.respond({"revision": state["revision"], "summary": summary(state), "documents": docs,
                                             "assignment": read_json(root / "assignment.json"), "workspace": str(root)})
                    if parsed.path == "/api/document":
                        ident = query.get("id", [""])[0]
                        state = read_json(root / "batch.json")
                        if ident == "teacher":
                            return self.respond({"original": read_json(root / "documents" / "teacher.json")})
                        d = next((d for d in state["documents"] if d["id"] == ident), None)
                        if d is None:
                            return self.respond({"error": "不存在的文档"}, status=404)
                        attach_ai_to_candidates(root, d)
                        original = read_json(root / "documents" / (ident + ".json")) if not d["error"] else {"blocks": []}
                        return self.respond({"submission": d, "original": original})
                    if parsed.path == "/asset":
                        ident = query.get("document", [""])[0]
                        name = query.get("name", [""])[0]
                        state = read_json(root / "batch.json")
                        if ident not in {"teacher", *(d["id"] for d in state["documents"])}:
                            raise ValueError("图片所属文档无效")
                        doc = read_json(root / "documents" / (ident + ".json"))
                        approved = {im["file"] for b in doc["blocks"] for im in b["images"] if im["preview"]}
                        if name not in approved:
                            raise ValueError("图片不存在或格式不支持预览")
                        path = root / "assets" / ident / name
                        return self.respond(path.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream")
                return self.respond({"error": "不存在的地址"}, status=404)
            except (ValueError, OSError, KeyError) as exc:
                return self.respond({"error": str(exc)}, status=400)

        def do_POST(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if not self.authorized(query):
                return self.respond({"error": "访问令牌无效"}, status=403)
            origin = self.headers.get("Origin")
            expected_origin = "http://127.0.0.1:" + str(self.server.server_port)
            if origin and origin != expected_origin:
                return self.respond({"error": "不允许跨站写入"}, status=403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond({"error": "必须发送JSON"}, status=415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 5 * 1024 * 1024:
                    raise ValueError("请求大小无效")
                payload = json.loads(self.rfile.read(length))
                root = active["root"]
                # Native dialogs and model startup can take a long time; do not
                # hold the shared state lock while they run.
                if parsed.path == "/api/ui/select-path":
                    path = choose_path(payload.get("mode", "file"), payload.get("title", "选择文件"),
                                       payload.get("filter", ""))
                    return self.respond({"path": path})
                if parsed.path == "/api/ui/local-ai":
                    if payload.get("action") == "stop":
                        return self.respond(stop_local_ai())
                    return self.respond(start_local_ai(root, load_settings(root)))
                with lock:
                    if parsed.path == "/api/ui/workspace":
                        if jobs.running():
                            raise ValueError("已有任务在运行，请等待完成后再切换工作目录")
                        target = Path(str(payload.get("path", "")).strip()).expanduser().resolve()
                        if not str(payload.get("path", "")).strip() or not target.is_dir():
                            raise ValueError("工作目录不存在：" + str(target))
                        if not has_batch(target):
                            raise ValueError("该目录没有导入批次（缺少 batch.json）：" + str(target))
                        active["root"] = target
                        jobs.root = target
                        return self.respond({"workspace": str(target)})
                    if parsed.path == "/api/ui/job":
                        action = payload.get("action", "")
                        params = payload.get("params", {})
                        if not isinstance(params, dict):
                            raise ValueError("参数格式错误")
                        if action not in {"prepare", "test-ai"} and not has_batch(root):
                            raise ValueError("当前工作目录还没有导入批次，请先使用“导入”")
                        if action == 'prepare' and not params.get('out'):
                            params['managed'] = True
                        if managed_config(root) and action != 'prepare':
                            params['workspace'] = str(root)
                        job_id = jobs.start(action, params)
                        return self.respond({"job": jobs.get(job_id)})
                    if parsed.path == "/api/ui/settings":
                        return self.respond({"settings": save_settings(root, payload)})
                    if parsed.path == '/api/ui/test-cases':
                        if any(j['action'] in {'judge-batch', 'ai-output'} and j['status'] in {'queued', 'running'} for j in jobs.all()):
                            raise ValueError('批量判题或 AI 输出判定正在使用测试用例，请等待任务结束再保存')
                        tests = tests_root(root)
                        if payload.get('directory') != str(tests):
                            raise ValueError('测试目录已切换，请重新打开测试用例模块')
                        return self.respond(update_case(tests, payload))

                    if parsed.path in {"/api/decision", "/api/export"} and not has_batch(root):
                        raise ValueError("当前工作目录还没有导入批次，请先使用“导入”")
                    if parsed.path == "/api/decision":
                        result = decide(root, payload)
                    elif parsed.path == "/api/export" and managed_config(root):
                        result = managed_export(root)
                    elif parsed.path == "/api/export":
                        from datetime import datetime
                        folder = root / "exports" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3))
                        result = {**export_sources(root, folder), "path": str(folder)}
                        save_settings(root, {"exported": str(folder)})
                    else:
                        return self.respond({"error": "不存在的操作"}, status=404)
                return self.respond(result)
            except (ValueError, OSError, KeyError, TypeError, AIError, SecretError) as exc:
                return self.respond({"error": str(exc)}, status=400)

    class WorkbenchServer(ThreadingHTTPServer):
        def server_close(self):
            stop_watcher.set()
            watcher.join(timeout=5)
            super().server_close()

    server = WorkbenchServer(("127.0.0.1", port), Handler)
    watcher = threading.Thread(target=watch, name='report-folder-sync', daemon=True)
    watcher.start()
    server.daemon_threads = True
    server.review_url = f"http://127.0.0.1:{server.server_port}/review?token={token}"
    server.workbench_url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    return server


def serve(root, port=0, open_browser=False):
    server = make_server(root, port)
    print("工作台：" + server.workbench_url, flush=True)
    print("人工复核：" + server.review_url, flush=True)
    print("仅本机访问；停止服务请按 Ctrl+C。", flush=True)
    if not has_batch(Path(root).resolve()):
        print("提示：当前工作目录还没有导入批次，请在工作台“导入”菜单中导入学生报告。", flush=True)
    if open_browser:
        webbrowser.open(server.workbench_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_local_ai()
        server.server_close()
