import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from homework_import.workbench import JobManager, student_detail
from homework_import.workspace import write_json
from homework_import.ai_review import check_candidate
from homework_import.locking import report_lock


class WorkbenchRegressionTests(unittest.TestCase):
    def test_independent_jobs_overlap_and_duplicate_is_rejected(self):
        release = threading.Event()
        entered = {a: threading.Event() for a in ('judge-batch', 'ai-output')}
        def dispatch(root, action, params, progress):
            entered[action].set()
            release.wait(3)
            return {'ok': True}
        manager = JobManager(Path('.'))
        with patch('homework_import.workbench.dispatch', dispatch):
            try:
                first = manager.start('judge-batch', {})
                second = manager.start('ai-output', {})
                self.assertTrue(all(e.wait(1) for e in entered.values()))
                self.assertTrue(manager.running())
                with self.assertRaises(ValueError):
                    manager.start('judge-batch', {})
                with self.assertRaises(ValueError):
                    manager.start('prepare', {})
                self.assertEqual(len(manager.all()), 2)
            finally:
                release.set()
                deadline = time.monotonic() + 3
                while manager.running() and time.monotonic() < deadline:
                    time.sleep(.01)
        self.assertEqual(manager.get(first)['status'], 'done')
        self.assertEqual(manager.get(second)['status'], 'done')

    def test_batch_compilation_log_without_syntax_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'batch.json', {'documents': [{'id': 'demo', 'student_id': '123', 'candidates': []}]})
            write_json(root / 'assignment.json', {'problems': []})
            report = root / 'reports' / '123-demo'
            (report / 'log').mkdir(parents=True)
            (report / 'result.csv').write_text('题号,状态\n1,编译失败\n', encoding='utf-8-sig')
            (report / 'log' / 'p1.compile.log').write_text('error: missing semicolon', encoding='utf-8')
            result = student_detail(root, {'reports': str(root / 'reports')}, 'demo')
            self.assertIn('missing semicolon', result['report']['compilation']['1']['log'])

    def test_semantic_failure_cannot_be_overridden_by_comment_presence(self):
        class Provider:
            def chat_json(self, system, user):
                return {'requirements': [{'index': 0, 'status': 'fail', 'evidence': 'wrong algorithm'}]}
        context = {'problem': {'requirements': ['整体题意：求和并写注释'], 'statement': '求和并写注释'}, 'code': '// comment\nint main(){}'}
        with patch('homework_import.ai_review._candidate_context', return_value=context):
            result = check_candidate(Provider(), Path('.'), {}, {'code': context['code']}, {'statement': '求和并写注释'})
        self.assertEqual(result['verdict'], 'fail')
        self.assertEqual(result['requirements'][0]['ai']['evidence'], 'wrong algorithm')

    def test_report_lock_serializes_same_student_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            self.assertIs(report_lock(path), report_lock(path / '.'))
            self.assertIsNot(report_lock(path), report_lock(path / 'other'))
            entered = threading.Event()
            def read_report():
                with report_lock(path):
                    entered.set()
            with report_lock(path):
                worker = threading.Thread(target=read_report)
                worker.start()
                self.assertFalse(entered.wait(.05))
            worker.join(1)
            self.assertTrue(entered.is_set())
