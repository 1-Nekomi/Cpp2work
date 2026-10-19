import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from homework_import.judge_batch import run_batch

ROOT = Path(__file__).resolve().parents[1]
JUDGE = Path(os.environ.get('JUDGE_TEST_BINARY', ROOT / 'build-windows' / 'judge.exe'))


@unittest.skipUnless(JUDGE.is_file() and shutil.which('g++'), 'requires built judge and g++')
class JudgeRuntimeTests(unittest.TestCase):
    def test_failures_are_results_and_do_not_stop_later_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code = base / 'exported' / 'student' / 'code'
            code.mkdir(parents=True)
            sources = [
                '#include <cstdio>\nint main(){puts("wrong");}',
                'int main(){return 7;}',
                'int main(){for(;;){}}',
                'this is not C++',
                '#include <cstdio>\nint main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b);}',
            ]
            for i, source in enumerate(sources, 1):
                (code / f'p{i}.cpp').write_text(source, encoding='utf-8')
                cases = base / 'tests' / f'p{i}'
                cases.mkdir(parents=True)
                (cases / 'case1.in').write_text('1 2\n')
                (cases / 'case1.exp').write_text('3\n')
            args = (base / 'exported', base / 'tests', base / 'reports', str(JUDGE))
            result = run_batch(*args, timeout_ms=200, progress=lambda *a, **k: None)
            self.assertEqual((result['cases'], result['passed']), (5, 1))
            target = base / 'reports' / 'student'
            with (target / 'cases.csv').open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r['结果'] for r in rows][:3], ['答案错误', '运行错误', '运行超时'])
            self.assertEqual(rows[3]['结果'], '缺少文件')
            self.assertEqual(rows[-1]['结果'], '通过')
            report = json.loads((base / 'reports' / 'batch-results.json').read_text(encoding='utf-8'))
            self.assertEqual(report['students'][0]['exit_code'], 1)
            # Completed failures must be reusable, just like successful submissions.
            again = run_batch(*args, timeout_ms=200, progress=lambda *a, **k: None)
            self.assertEqual((again['skipped'], again['cases'], again['passed']), (1, 5, 1))
            # Selecting a later problem still runs only that problem and returns success.
            completed = subprocess.run([str(JUDGE), '--root', str(ROOT), '--code', str(code),
                '--tests', str(base / 'tests'), '--out', str(base / 'single'),
                '--problem', '5', '--no-compile'], capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with (base / 'single' / 'cases.csv').open(encoding='utf-8-sig', newline='') as f:
                self.assertEqual(len(list(csv.DictReader(f))), 1)


@unittest.skipUnless(JUDGE.is_file() and shutil.which('g++'), 'requires built judge and g++')
class CompilerCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / '中文 编译目录'
        self.code = self.base / 'code'
        self.code.mkdir(parents=True)
        self.cases = self.base / 'tests' / 'p1'
        self.cases.mkdir(parents=True)
        (self.cases / 'case1.in').write_text('')
        (self.cases / 'case1.exp').write_text('3\n')
        self.source = self.code / 'p1.cpp'
        self.source.write_text('#include <cstdio>\n#include "value.h"\n'
                               'int main(){printf("%d\\n",VALUE);}\n')
        self.header = self.code / 'value.h'
        self.header.write_text('#define VALUE 3\n')

    def judge(self, *flags, expected=0):
        result = subprocess.run([str(JUDGE), '--root', str(ROOT), '--code', str(self.code),
            '--tests', str(self.base / 'tests'), '--out', str(self.base / 'report'), *flags],
            capture_output=True, timeout=30)
        output = result.stdout.decode('utf-8', errors='replace')
        self.assertEqual(result.returncode, expected, output + repr(result.stderr))
        log = self.base / 'report' / 'log' / 'p1.compile.log'
        return log.read_text(encoding='utf-8') if log.is_file() else ''

    def test_cache_reuse_header_invalidation_and_forced_rebuild(self):
        self.assertNotIn('编译缓存命中', self.judge())
        self.assertIn('编译缓存命中', self.judge())
        # Same size and timestamp must not hide a changed transitive dependency.
        before = self.header.stat()
        self.header.write_text('#define VALUE 4\n')
        os.utime(self.header, ns=(before.st_atime_ns, before.st_mtime_ns))
        (self.cases / 'case1.exp').write_text('4\n')
        self.assertNotIn('编译缓存命中', self.judge())
        self.assertIn('编译缓存命中', self.judge())
        self.assertNotIn('编译缓存命中', self.judge('--rebuild'))

    def test_source_and_object_changes_and_missing_dependency(self):
        self.judge()
        self.source.write_text(self.source.read_text().replace('VALUE);', 'VALUE+1);'))
        (self.cases / 'case1.exp').write_text('4\n')
        self.assertNotIn('编译缓存命中', self.judge())
        obj = self.code / 'build' / 'p1.unit0.o'
        obj.write_bytes(b'corrupted object')
        self.assertNotIn('编译缓存命中', self.judge())
        self.header.unlink()
        self.judge(expected=1)
        self.assertFalse((self.code / 'build' / 'p1.exe').exists())
        self.judge('--no-compile', expected=1)

    def test_multifile_link_failure_is_not_hidden_by_cached_objects(self):
        self.source.write_text('#include <cstdio>\nint value();\nint main(){printf("%d\\n",value());}')
        extra = self.code / '额外.cpp'
        extra.write_text('int value(){return 3;}')
        (self.code / 'p1.sources').write_text('额外.cpp\n', encoding='utf-8')
        self.judge()
        self.assertEqual(self.judge().count('编译缓存命中'), 2)
        (self.code / 'p1.sources').write_text('')
        log = self.judge(expected=1)
        self.assertIn('编译缓存命中', log)
        self.assertFalse((self.code / 'build' / 'p1.exe').exists())

    def test_gbk_source_preserves_output_after_preprocessing(self):
        self.source.write_bytes('#include <cstdio>\nint main(){puts("你好");}'.encode('gb18030'))
        (self.cases / 'case1.exp').write_text('你好\n', encoding='utf-8')
        self.judge()
        self.assertIn('编译缓存命中', self.judge())

    def test_serial_and_parallel_compilation_have_same_results(self):
        self.judge('--compile-jobs', '1')
        for i in (2, 3):
            shutil.copyfile(self.source, self.code / f'p{i}.cpp')
            shutil.copytree(self.cases, self.base / 'tests' / f'p{i}')
        self.judge('--compile-jobs', '1', '--rebuild')
        report = self.base / 'report' / 'cases.csv'
        serial = report.read_bytes()
        self.judge('--compile-jobs', '2', '--rebuild')
        self.assertEqual(report.read_bytes(), serial)
        self.judge('--compile-jobs', '0', expected=2)
        shutil.copytree(self.cases, self.base / 'tests' / 'problem1')
        self.judge(expected=2)  # Duplicate IDs must not race on the same executable.
