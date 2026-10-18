import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from test_import import docx, p
from homework_import.managed import bind_folder, sync_folder, managed_export
from homework_import.server import make_server
from homework_import.workbench import load_settings
from homework_import.workspace import read_json, write_json, decide


class ManagedFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        profile = patch('homework_import.workbench.global_settings_path', return_value=self.base / 'profile.json')
        profile.start()
        self.addCleanup(profile.stop)
        self.folder = self.base / 'students'
        self.folder.mkdir()
        self.teacher = self.base / 'teacher.docx'
        docx(self.teacher, p('二、实验任务') + p('1. 输出一个整数。') + p('三、实验结果'))
        self.add_report('2026111111学生.docx')

    def add_report(self, name, code='int main(){return 0;}'):
        docx(self.folder / name, p('三、实验结果') + p('第1题') + p(code))

    def bind(self):
        result = bind_folder(self.folder, self.teacher)
        return Path(result['workspace'])

    def test_binding_defaults_and_preserves_existing_workspace(self):
        root = self.bind()
        self.assertEqual(root, self.folder / 'work')
        self.assertTrue((self.folder / 'output').is_dir())
        settings = load_settings(root)
        self.assertEqual(settings['workspace'], str(root))
        self.assertTrue(Path(settings['reports']).is_relative_to(self.folder / 'output'))
        state = read_json(root / 'batch.json')
        state['documents'][0]['review'] = {'action': 'document_approve', 'note': 'keep'}
        write_json(root / 'batch.json', state)
        bind_folder(self.folder)
        self.assertEqual(read_json(root / 'batch.json')['documents'][0]['review']['note'], 'keep')

    def test_incremental_add_modify_delete_and_ignore_generated_folders(self):
        root = self.bind()
        state = read_json(root / 'batch.json')
        state['documents'][0]['candidates'][0]['state'] = 'approved'
        write_json(root / 'batch.json', state)
        self.add_report('2026222222学生.docx')
        (self.folder / '~$temp.docx').write_bytes(b'temp')
        (self.folder / 'output' / 'not-a-submission.docx').write_bytes(b'ignore')
        self.assertEqual(sync_folder(root), {'added': 1, 'updated': 0, 'removed': 0})
        state = read_json(root / 'batch.json')
        self.assertEqual(state['documents'][0]['candidates'][0]['state'], 'approved')
        self.add_report('2026111111学生.docx', 'int main(){return 1;}')
        self.assertEqual(sync_folder(root)['updated'], 1)
        state = read_json(root / 'batch.json')
        self.assertNotEqual(state['documents'][0]['candidates'][0]['state'], 'approved')
        (self.folder / '2026222222学生.docx').unlink()
        self.assertEqual(sync_folder(root)['removed'], 1)
        self.assertEqual(len(read_json(root / 'batch.json')['documents']), 1)
        self.assertEqual(sync_folder(root), {'added': 0, 'updated': 0, 'removed': 0})

    def test_generated_export_and_invalidation_keep_previous_output(self):
        root = self.bind()
        state = read_json(root / 'batch.json')
        config = read_json(root / 'assignment.json')
        config['problems'][0]['format_policy'] = 'results_only'
        decide(root, {'revision': state['revision'], 'action': 'assignment', 'assignment': config, 'confirm': True})
        result = managed_export(root)
        self.assertTrue(Path(result['path']).is_relative_to(self.folder / 'output'))
        self.assertEqual(load_settings(root)['exported'], result['path'])
        previous_reports = load_settings(root)['reports']
        document = read_json(root / 'batch.json')['documents'][0]
        report = Path(previous_reports) / (document['student_id'] + '-' + document['id'])
        report.mkdir(parents=True)
        (report / 'result.csv').write_text('retained result', encoding='utf-8')
        self.add_report('2026222222学生.docx')
        sync_folder(root)
        self.assertEqual(load_settings(root)['exported'], '')
        self.assertNotEqual(load_settings(root)['reports'], previous_reports)
        self.assertTrue(Path(result['path']).is_dir())
        retained = Path(load_settings(root)['reports']) / report.name / 'result.csv'
        self.assertEqual(retained.read_text(encoding='utf-8'), 'retained result')
        self.add_report('2026111111学生.docx', 'int main(){return 2;}')
        sync_folder(root)
        self.assertFalse((Path(load_settings(root)['reports']) / report.name).exists())

    def test_unavailable_source_does_not_empty_batch(self):
        root = self.bind()
        marker = read_json(root / 'managed-folder.json')
        marker['submissions'] = str(self.base / 'missing')
        write_json(root / 'managed-folder.json', marker)
        with self.assertRaises(ValueError):
            sync_folder(root)
        self.assertEqual(len(read_json(root / 'batch.json')['documents']), 1)

    def test_http_bind_watch_and_restore_last_folder(self):
        launch = self.base / 'launch'
        server = make_server(launch, sync_interval=.05)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        token = server.workbench_url.split('token=')[1]
        def api(path, data=None):
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}?token={token}',
                data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(.05)
            self.fail('folder watcher did not synchronize')
        try:
            api('/api/ui/job', {'action': 'prepare', 'params': {'teacher': str(self.teacher), 'submissions': str(self.folder)}})
            wait_for(lambda: api('/api/ui/state')['workspace'] == str(self.folder / 'work'))
            self.add_report('2026222222学生.docx')
            wait_for(lambda: len(api('/api/ui/state')['students']) == 2)
            started, release = threading.Event(), threading.Event()
            def busy_job(*args):
                started.set()
                release.wait(3)
                return {}
            with patch('homework_import.workbench.dispatch', side_effect=busy_job):
                try:
                    api('/api/ui/job', {'action': 'audit', 'params': {}})
                    self.assertTrue(started.wait(1))
                    (self.folder / '2026111111学生.docx').unlink()
                    time.sleep(.2)
                    self.assertEqual(len(api('/api/ui/state')['students']), 2)
                finally:
                    release.set()
                wait_for(lambda: len(api('/api/ui/state')['students']) == 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        reopened = make_server(launch, sync_interval=.05)
        try:
            self.assertEqual(load_settings(launch)['bound_workspace'], str(self.folder / 'work'))
        finally:
            reopened.server_close()
