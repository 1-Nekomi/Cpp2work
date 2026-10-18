import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from homework_import.test_cases import list_cases, read_case, update_case, tests_revision
from homework_import.judge_batch import run_batch

JUDGE = Path(__file__).resolve().parents[1] / 'build-windows' / 'judge.exe'


class TestCaseEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'tests'
        (self.root / 'p1').mkdir(parents=True)
        (self.root / 'p1' / 'case1.in').write_bytes(b'1 2\r\n')
        (self.root / 'p1' / 'case1.exp').write_bytes(b'3\r\n')

    def save(self, name='case1', **changes):
        payload = {**read_case(self.root, 'p1', name), 'action': 'save'}
        payload.update(changes)
        return update_case(self.root, payload)

    def test_roundtrip_description_does_not_change_original_data(self):
        result = self.save(description='普通正数求和', input='1 2\n', expected='3\n')
        self.assertFalse(result['data_changed'])
        self.assertEqual(tests_revision(self.root), '')
        self.assertEqual((self.root / 'p1' / 'case1.in').read_bytes(), b'1 2\r\n')
        catalog = list_cases(self.root, {'problems': [{'id': 2, 'title': '第二题'}]})
        self.assertEqual(len(catalog['problems']), 2)
        self.assertEqual(catalog['problems'][0]['cases'][0]['description'], '普通正数求和')

    def test_edit_changes_real_files_and_backs_up_original(self):
        result = self.save(input='0 -1\n', expected='-1\n', description='负数边界')
        self.assertTrue(result['data_changed'])
        self.assertTrue(tests_revision(self.root))
        self.assertEqual((self.root / 'p1' / 'case1.exp').read_bytes(), b'-1\n')
        self.assertEqual((Path(result['backup']) / 'case1.exp').read_bytes(), b'3\r\n')
        self.assertEqual(read_case(self.root, 'p1', 'case1')['description'], '负数边界')

    def test_create_delete_empty_case_and_preserve_backup(self):
        self.save('case2', input='', expected='', description='空输入')
        self.assertTrue((self.root / 'p1' / 'case2.in').is_file())
        payload = {**read_case(self.root, 'p1', 'case2'), 'action': 'delete'}
        result = update_case(self.root, payload)
        self.assertFalse((self.root / 'p1' / 'case2.in').exists())
        self.assertFalse((self.root / 'p1' / 'case2.exp').exists())
        self.assertTrue((Path(result['backup']) / 'case2.in').is_file())
        self.assertEqual(len(list_cases(self.root)['problems']), 1)

    def test_external_edit_conflict_does_not_overwrite(self):
        payload = {**read_case(self.root, 'p1', 'case1'), 'action': 'save'}
        (self.root / 'p1' / 'case1.exp').write_text('external', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '其他页面'):
            update_case(self.root, payload)
        self.assertEqual((self.root / 'p1' / 'case1.exp').read_text(), 'external')

    def test_alternate_expected_suffix_is_edited_in_place(self):
        (self.root / 'p1' / 'case1.exp').rename(self.root / 'p1' / 'case1.ans')
        result = self.save(expected='new\n')
        self.assertEqual(result['case']['expected_extension'], '.ans')
        self.assertFalse((self.root / 'p1' / 'case1.exp').exists())
        self.assertEqual((self.root / 'p1' / 'case1.ans').read_text(), 'new\n')

    def test_path_traversal_and_reserved_names_are_rejected(self):
        for problem, name in [('../outside', 'case1'), ('p1', '../file'), ('p1', 'CON'), ('p1', 'name:stream')]:
            with self.subTest(problem=problem, name=name), self.assertRaises(ValueError):
                read_case(self.root, problem, name)
        self.assertFalse((self.base / 'outside').exists())

    def test_edit_invalidates_existing_batch_skip(self):
        exported = self.base / 'exported'
        (exported / 'student' / 'code').mkdir(parents=True)
        out = self.base / 'reports'
        target = out / 'student'
        target.mkdir(parents=True)
        (target / 'summary.md').write_text('old summary')
        (target / 'result.csv').write_text('题号,本题得分\n1,100\n', encoding='utf-8-sig')
        judge = self.base / 'judge.exe'
        judge.touch()
        self.save(expected='42\n')
        with patch('homework_import.judge_batch.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            result = run_batch(exported, self.root, out, str(judge), progress=lambda *a, **k: None)
            self.assertEqual(result['skipped'], 0)
            self.assertEqual(run.call_count, 1)
            result = run_batch(exported, self.root, out, str(judge), progress=lambda *a, **k: None)
            self.assertEqual(result['skipped'], 1)
            self.assertEqual(run.call_count, 1)


    @unittest.skipUnless(JUDGE.is_file() and shutil.which('g++'), '需要已构建的 judge.exe 和 g++')
    def test_real_judge_reads_saved_files_and_reruns_after_data_change(self):
        exported = self.base / 'real-exported'
        (exported / 'student' / 'code').mkdir(parents=True)
        (exported / 'student' / 'code' / 'p1.cpp').write_text(
            '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b<<"\\n";}\n',
            encoding='utf-8')
        reports = self.base / 'real-reports'
        progress = lambda *args, **kwargs: None
        self.save(input='10 20\n', expected='30\n', description='真实判题器读取')
        first = run_batch(exported, self.root, reports, str(JUDGE), progress=progress)
        self.assertEqual((first['skipped'], first['cases'], first['passed']), (0, 1, 1))
        second = run_batch(exported, self.root, reports, str(JUDGE), progress=progress)
        self.assertEqual(second['skipped'], 1)
        self.save(input='7 8\n', expected='15\n')
        third = run_batch(exported, self.root, reports, str(JUDGE), progress=progress)
        self.assertEqual((third['skipped'], third['passed']), (0, 1))
        self.save(description='仅修改说明')
        fourth = run_batch(exported, self.root, reports, str(JUDGE), progress=progress)
        self.assertEqual(fourth['skipped'], 1)

    def test_http_editor_saves_and_blocks_writes_during_judging(self):
        import threading
        import urllib.request
        from homework_import.server import make_server
        from homework_import.workbench import save_settings
        from homework_import.workspace import write_json
        workspace = self.base / 'workspace'
        write_json(workspace / 'batch.json', {'documents': []})
        write_json(workspace / 'assignment.json', {'problems': [{'id': 1}]})
        release, started = threading.Event(), threading.Event()
        def fake_judge(*args):
            started.set()
            release.wait(3)
            return {}
        with patch('homework_import.workbench.global_settings_path', return_value=self.base / 'profile.json'):
            save_settings(workspace, {'tests': str(self.root)})
            server = make_server(workspace)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            token = server.workbench_url.split('token=')[1]
            def api(path, payload=None, extra=''):
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}?token={token}{extra}',
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request) as response:
                    return json.load(response)
            try:
                response = api('/api/ui/test-cases', extra='&problem=p1&name=case1')
                payload = {**response['case'], 'directory': response['directory'], 'expected': '9\n', 'action': 'save'}
                api('/api/ui/test-cases', payload)
                self.assertEqual((self.root / 'p1' / 'case1.exp').read_text(), '9\n')
                with patch('homework_import.workbench.dispatch', side_effect=fake_judge):
                    api('/api/ui/job', {'action': 'judge-batch', 'params': {}})
                    self.assertTrue(started.wait(1))
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        api('/api/ui/test-cases', payload)
                    self.assertEqual(error.exception.code, 400)
                    self.assertIn('等待任务结束', error.exception.read().decode('utf-8'))
                    error.exception.close()
            finally:
                release.set()
                server.shutdown()
                server.server_close()
                thread.join()
