import csv
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from homework_import.ai_review import check_candidate, load_results, run_review, validate_requirements
from homework_import.workspace import read_json, write_json


class RequirementsPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.candidate = {'id': 'c1', 'problem_id': 1, 'state': 'approved',
                          'code': 'int main(){int sum=1+2; /* extra helper */}'}
        self.doc = {'id': 'd1', 'filename': 'student.docx', 'student_id': '123',
                    'candidates': [self.candidate]}
        self.problem = {'id': 1, 'requirements': [{'text': '实现整数求和'}], 'statement': ''}
        write_json(self.root / 'documents/d1.json', {'blocks': []})
        write_json(self.root / 'batch.json', {'documents': [self.doc]})
        write_json(self.root / 'assignment.json', {'problems': [self.problem]})

    def provider(self, data):
        calls = []
        def chat(system, user):
            calls.append((system, user))
            return data
        return types.SimpleNamespace(chat_json=chat, calls=calls)

    def check(self, data):
        provider = self.provider(data)
        result = check_candidate(provider, self.root, self.doc, self.candidate, self.problem)
        self.assertIn('只放入 warnings', provider.calls[0][0])
        return result

    def test_extra_content_is_a_warning_not_a_failed_requirement(self):
        result = self.check({'requirements': [{'index': 0, 'status': 'fail',
                             'reason_type': 'extra_only', 'evidence': '求和已实现，但额外输出说明'}],
                             'summary': '因为多余内容所以不符合'})
        self.assertEqual(result['verdict'], 'pass')
        self.assertEqual(result['requirements'][0]['status'], 'pass')
        self.assertIn('额外输出说明', result['warnings'][0])
        self.assertNotIn('所以不符合', result['ai_summary'])

    def test_missing_or_incorrect_requirement_still_fails_with_warnings(self):
        result = self.check({'requirements': [{'index': 0, 'status': 'fail',
                             'reason_type': 'requirement_gap', 'evidence': '要求求和，实际执行减法'}],
                             'warnings': ['还有额外提示输出']})
        self.assertEqual(result['verdict'], 'fail')
        self.assertIn('实际执行减法', result['requirements'][0]['ai']['evidence'])
        self.assertEqual(result['warnings'], ['还有额外提示输出'])

    def test_warnings_do_not_replace_missing_evidence_or_create_requirements(self):
        result = self.check({'requirements': [{'index': 99, 'status': 'pass',
                             'reason_type': 'extra_only', 'evidence': '额外功能'}],
                             'warnings': ['额外功能']})
        self.assertEqual(result['verdict'], 'unclear')
        self.assertEqual(len(result['requirements']), 1)
        normalized = validate_requirements({'requirements': [
            {'index': 0, 'status': 'fail', 'reason_type': [], 'evidence': '缺失求和功能'}]}, 1)
        self.assertEqual(normalized['requirements'][0]['status'], 'fail')

    def test_pass_with_warnings_is_exported_and_legacy_cache_is_recomputed(self):
        provider = self.provider({'requirements': [{'index': 0, 'status': 'pass',
                                  'reason_type': 'none', 'evidence': 'sum=1+2'}],
                                  'warnings': ['存在额外辅助代码']})
        def run():
            return run_review(self.root, provider, stages=('requirements',), only='all',
                              progress=lambda *a, **k: None)
        with patch('homework_import.ai_review.REQUIREMENTS_VERSION', 'requirements-v2'):
            self.assertEqual(run()['done'], 1)
        self.assertEqual(run()['done'], 1)
        self.assertEqual(run()['skipped'], 1)
        self.assertEqual(len(provider.calls), 2)
        stored = load_results(self.root)['items']['d1/c1']['requirements']
        self.assertEqual(stored['verdict'], 'pass')
        self.assertEqual(stored['warnings'], ['存在额外辅助代码'])
        with (self.root / 'ai-requirements.csv').open(encoding='utf-8-sig', newline='') as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row['题意结论'], '符合')
        self.assertEqual(row['警告（不影响题意结论）'], '存在额外辅助代码')
        self.assertIn('存在额外辅助代码', (self.root / 'ai-review.md').read_text(encoding='utf-8'))
        self.assertEqual(read_json(self.root / 'batch.json')['documents'][0]['candidates'][0]['state'], 'approved')

    def test_no_requirements_does_not_invent_checks(self):
        self.problem['requirements'] = []
        provider = self.provider({})
        result = check_candidate(provider, self.root, self.doc, self.candidate, self.problem)
        self.assertEqual(result['verdict'], 'unclear')
        self.assertEqual(result['warnings'], [])
        self.assertEqual(provider.calls, [])
