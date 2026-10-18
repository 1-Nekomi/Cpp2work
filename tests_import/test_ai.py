import csv
import json
import os
import tempfile
import threading
import types
import unittest
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

from homework_import.ai import AIConfig, AIProvider
from homework_import.ai_review import (attach_ai_to_candidates, deterministic_checks, load_results,
                                       run_output_review, run_review)
from homework_import.server import make_server
from homework_import.workbench import (load_settings, public_settings, save_settings,
                                       settings_path, student_detail)
from homework_import.workspace import prepare, read_json, write_json

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def p(text):
    return '<w:p><w:r>' + '<w:br/>'.join('<w:t xml:space="preserve">' + escape(line) + '</w:t>' for line in text.split('\n')) + '</w:r></w:p>'


def docx(path, body):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')


class FakeProvider:
    def __init__(self):
        self.config = types.SimpleNamespace(model="fake-model")
        self.calls = []

    def chat_json(self, system, user):
        self.calls.append((system, user))
        if "判断下面这段提取结果" in system:
            return {"verdict": "likely_student_error", "confidence": 0.9,
                    "suggested_action": "approve_extraction",
                    "evidence": ["源码中有注释和 const"], "suggested_range": None,
                    "notes": "测试"}
        if "教师题目要求检查" in system or "教师题目要求" in system:
            return {"requirements": [{"index": 0, "status": "pass", "confidence": 0.8,
                                      "evidence": "找到 const 和注释"}], "summary": "满足要求"}
        if "学生程序输出与教师预期输出" in system:
            return {"verdict": "format_only", "confidence": 0.8,
                    "evidence": ["只是提示语不同"], "notes": "数字一致"}
        raise AssertionError("unexpected prompt: " + system[:80])


class CachedProvider(AIProvider):
    def __init__(self, cache_dir):
        super().__init__(AIConfig("http://127.0.0.1:1/v1", "fake-model"), cache_dir)
        self.calls = 0

    def _request(self, system, user):
        self.calls += 1
        return {"ok": True}


def json_server(fail_json_mode=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if fail_json_mode and "response_format" in body:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"response_format unsupported"}')
                return
            content = json.dumps({"verdict": "unclear", "confidence": 0.5})
            payload = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 5}}
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class AITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def create_workspace(self):
        teacher = self.root / "teacher.docx"
        docx(teacher, p("二、实验任务") + p("1. 输出整数，要求写注释和符号常量。") + p("三、实验结果"))
        submissions = self.root / "students"
        submissions.mkdir()
        docx(submissions / "2026111111学生.docx",
             p("三、实验结果") + p("第1题：") + p("// 注释") + p("const int N=1;") + p("int main(){return 0;}"))
        root = self.root / "workspace"
        prepare(teacher, submissions, root)
        return root

    def test_deterministic_checks_find_constant_comment_and_precision(self):
        problem = {"requirements": [{"text": "要求写注释"}, {"text": "必须使用符号常量"},
                                    {"text": "输出保留2位小数"}]}
        result = deterministic_checks(problem, "// 注释\nconst double RATE=1; int main(){cout<<fixed<<setprecision(2);}")
        self.assertEqual(result["checks"]["comments"]["status"], "pass")
        self.assertEqual(result["checks"]["symbol_constant"]["status"], "pass")
        self.assertEqual(result["checks"]["precision_2"]["status"], "pass")
        self.assertEqual(result["checks"]["main"]["status"], "pass")

    def test_ai_review_stores_advice_without_changing_state(self):
        root = self.create_workspace()
        before = read_json(root / "batch.json")["documents"][0]["candidates"][0]["state"]
        provider = FakeProvider()
        result = run_review(root, provider, only="all", progress=lambda *args, **kwargs: None)
        self.assertEqual(result["done"], 1)
        self.assertEqual(read_json(root / "batch.json")["documents"][0]["candidates"][0]["state"], before)
        stored = load_results(root)["items"]
        self.assertEqual(len(stored), 1)
        item = next(iter(stored.values()))
        self.assertEqual(item["triage"]["verdict"], "likely_student_error")
        self.assertTrue((root / "ai-review.md").is_file())
        self.assertTrue((root / "ai-review.csv").is_file())
        document = read_json(root / "batch.json")["documents"][0]
        attach_ai_to_candidates(root, document)
        self.assertEqual(document["candidates"][0]["ai"]["triage"]["confidence"], 0.9)

    def test_ai_output_review_reads_failed_cases_and_writes_advice(self):
        workspace = self.create_workspace()
        problem = read_json(workspace / "assignment.json")["problems"][0]
        reports = self.root / "reports"
        student = reports / "2026111111-demo"
        (student / "actual").mkdir(parents=True)
        expected = student / "expected.txt"
        actual = student / "actual" / "p1_case1.txt"
        input_file = student / "input.txt"
        expected.write_text("结果是：25.52\n", encoding="utf-8")
        actual.write_text("结果是 25.52\n", encoding="utf-8")
        input_file.write_text("1.72\n75.5\n", encoding="utf-8")
        with (student / "cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["题号", "用例", "输入文件", "预期文件", "结果", "比对档位", "退出码", "说明",
                             "实际输出", "标准错误", "差异说明"])
            writer.writerow([problem["id"], "case1", str(input_file), str(expected), "答案错误", "", "0", "",
                             str(actual), "", ""])
        result = run_output_review(workspace, reports, self.root / "tests", FakeProvider())
        self.assertEqual(result["cases"], 1)
        self.assertTrue((reports / "ai-output.csv").is_file())
        payload = json.loads((student / "ai-output.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["items"]["p1/case1"]["verdict"], "format_only")
        document_id = read_json(workspace / "batch.json")["documents"][0]["id"]
        detail = student_detail(workspace, {"reports": str(reports)}, document_id)
        case = next(c for c in detail["report"]["cases"] if c["题号"] == "1" and c["用例"] == "case1")
        self.assertEqual(case["ai_output"]["verdict"], "format_only")
        self.assertEqual(case["ai_output"]["suggested_accept"], True)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_api_key_is_encrypted_at_rest_and_roundtrips(self):
        root = self.root / "workspace"
        root.mkdir()
        save_settings(root, {"api_key": "secret-value-123"})
        stored = settings_path(root).read_text(encoding="utf-8")
        self.assertNotIn("secret-value-123", stored)
        payload = json.loads(stored)
        self.assertNotIn("api_key", payload)
        self.assertTrue(payload["api_key_enc"].startswith("dpapi:"))
        self.assertEqual(load_settings(root)["api_key"], "secret-value-123")
        public = public_settings(load_settings(root))
        self.assertTrue(public["api_key_set"])
        self.assertNotIn("api_key_enc", public)
        self.assertEqual(public["api_key"], "")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_legacy_plaintext_key_is_migrated(self):
        root = self.root / "workspace"
        root.mkdir()
        write_json(settings_path(root), {"api_key": "legacy-secret"})
        settings = load_settings(root)
        self.assertEqual(settings["api_key"], "legacy-secret")
        stored = settings_path(root).read_text(encoding="utf-8")
        self.assertNotIn("legacy-secret", stored)
        payload = json.loads(stored)
        self.assertNotIn("api_key", payload)
        self.assertTrue(payload["api_key_enc"].startswith("dpapi:"))

    def test_provider_cache_reuses_response(self):
        cache = self.root / "cache"
        provider = CachedProvider(cache)
        self.assertEqual(provider.chat_json("s", "u"), {"ok": True})
        self.assertEqual(provider.chat_json("s", "u"), {"ok": True})
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(list(cache.glob("*.json"))), 1)

    def test_server_exposes_ai_advice_for_the_review_page(self):
        root = self.create_workspace()
        run_review(root, FakeProvider(), only="all", progress=lambda *args, **kwargs: None)
        server = make_server(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            token = server.review_url.split("token=", 1)[1]
            document_id = read_json(root / "batch.json")["documents"][0]["id"]
            url = f"http://127.0.0.1:{server.server_port}/api/document?id={document_id}&token={token}"
            with urllib.request.urlopen(url) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["submission"]["candidates"][0]["ai"]["triage"]["verdict"],
                             "likely_student_error")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_provider_reads_openai_compatible_http_json(self):
        server, thread = json_server()
        try:
            provider = AIProvider(AIConfig(f"http://127.0.0.1:{server.server_port}/v1", "fake"),
                                  self.root / "cache")
            result = provider.chat_json("system", "user")
            self.assertEqual(result["verdict"], "unclear")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_provider_falls_back_when_json_mode_is_rejected(self):
        server, thread = json_server(fail_json_mode=True)
        try:
            provider = AIProvider(AIConfig(f"http://127.0.0.1:{server.server_port}/v1", "fake"),
                                  self.root / "cache")
            result = provider.chat_json("system", "user")
            self.assertEqual(result["verdict"], "unclear")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
