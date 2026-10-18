"""Edit the judge's real test files, with optimistic concurrency and backups."""
from pathlib import Path
import hashlib
import json
import os
import re
import uuid

from .locking import workspace_lock

EXPECTED = ('.exp', '.out', '.ans', '.expected')
MAX_TEXT = 1024 * 1024
REVISION_FILE = '.workbench-revision'


def tests_revision(root):
    path = Path(root) / REVISION_FILE
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def _component(value):
    if (not isinstance(value, str) or not value or len(value) > 100 or
            re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.startswith('.') or
            value.endswith(('.', ' ')) or
            re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value)):
        raise ValueError('题目目录和用例名称必须是有效文件名，不能包含路径分隔符')
    return value


def _folder(root, problem):
    root = Path(root).resolve()
    folder = root if problem == '.' else root / _component(problem)
    if not folder.resolve().is_relative_to(root):
        raise ValueError('用例路径超出当前测试目录')
    return folder


def _paths(root, problem, name):
    folder = _folder(root, problem)
    _component(name)
    paths = [folder / (name + ext) for ext in ('.in', *EXPECTED, '.case.json')]
    if any(not p.resolve().is_relative_to(Path(root).resolve()) for p in paths):
        raise ValueError('用例文件指向测试目录之外')
    return paths


def _bytes(path):
    if not path.exists():
        return None
    if path.stat().st_size > MAX_TEXT:
        raise ValueError('单个用例文件超过 1 MB，请在文件编辑器中处理')
    return path.read_bytes()


def _decode(raw):
    if raw is None:
        return ''
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('无法解码用例文本，请先转换为 UTF-8')


def _version(paths, contents):
    h = hashlib.sha256()
    for path, content in zip(paths, contents):
        h.update(path.name.encode('utf-8') + b'\x00')
        h.update(b'missing' if content is None else hashlib.sha256(content).digest())
    return h.hexdigest()


def read_case(root, problem, name):
    paths = _paths(root, problem, name)
    contents = [_bytes(p) for p in paths]
    expected_index = next((i for i in range(1, 5) if contents[i] is not None), 1)
    metadata = json.loads(_decode(contents[-1])) if contents[-1] else {}
    if not isinstance(metadata, dict) or not isinstance(metadata.get('description', ''), str):
        raise ValueError('用例说明文件格式错误，请检查 .case.json')
    return {'name': name, 'problem': problem, 'input': _decode(contents[0]),
            'expected': _decode(contents[expected_index]), 'description': metadata.get('description', ''),
            'expected_extension': paths[expected_index].suffix,
            'missing_input': contents[0] is None, 'missing_expected': contents[expected_index] is None,
            'exists': any(c is not None for c in contents), 'version': _version(paths, contents)}


def list_cases(root, assignment=None):
    root = Path(root).resolve()
    problems = {}
    for p in (assignment or {}).get('problems', []):
        key = 'p' + str(p['id'])
        _component(key)
        problems[key] = {'id': key, 'title': p.get('title') or ('第 ' + str(p['id']) + ' 题'),
                         'statement': p.get('statement', ''), 'cases': []}
    folders = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')] if root.is_dir() else []
    if root.is_dir() and any(p.suffix in ('.in', *EXPECTED) for p in root.iterdir() if p.is_file()):
        folders.append('.')
    for folder in set(folders) | set(problems):
        path = _folder(root, folder)
        names = sorted({p.stem for p in path.iterdir() if p.is_file() and p.suffix in ('.in', *EXPECTED)}) if path.is_dir() else []
        if not names and folder not in problems:
            continue
        entry = problems.setdefault(folder, {'id': folder, 'title': folder if folder != '.' else '根目录用例', 'statement': '', 'cases': []})
        for name in names:
            try:
                case = read_case(root, folder, name)
                entry['cases'].append({k: case[k] for k in ('name', 'description', 'missing_input', 'missing_expected')})
            except (ValueError, OSError) as exc:
                entry['cases'].append({'name': name, 'error': str(exc)})
    return {'directory': str(root), 'revision': tests_revision(root),
            'problems': sorted(problems.values(), key=lambda p: (int(re.search(r'\d+', p['id'])[0]) if re.search(r'\d+', p['id']) else 99999, p['id']))}


def _atomic(path, content):
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_bytes(content)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def update_case(root, payload):
    root = Path(root).resolve()
    action = payload.get('action', 'save')
    if action not in {'save', 'delete'}:
        raise ValueError('未知的用例操作')
    problem, name = payload.get('problem'), payload.get('name')
    paths = _paths(root, problem, name)
    with workspace_lock(root):
        before = [_bytes(p) for p in paths]
        version = _version(paths, before)
        if payload.get('version') != version:
            raise ValueError('用例已被其他页面或文件编辑器修改，请重新加载后再保存')
        if action == 'delete' and not any(c is not None for c in before):
            raise ValueError('用例不存在或已删除')
        after = list(before)
        if action == 'delete':
            after = [None] * len(paths)
        else:
            for key in ('input', 'expected', 'description'):
                if not isinstance(payload.get(key), str) or len(payload[key].encode('utf-8')) > MAX_TEXT:
                    raise ValueError('输入、预期输出及说明必须是文本，单项不超过 1 MB')
            index = next((i for i in range(1, 5) if before[i] is not None), 1)
            for i, key in ((0, 'input'), (index, 'expected')):
                if before[i] is None or payload[key] != _decode(before[i]).replace('\r\n', '\n'):
                    after[i] = payload[key].encode('utf-8')
            after[-1] = (json.dumps({'description': payload['description']}, ensure_ascii=False) + '\n').encode('utf-8')
        if before == after:
            return {'changed': False, 'case': read_case(root, problem, name)}
        data_changed = before[:-1] != after[:-1]
        backup = root / '.workbench-history' / uuid.uuid4().hex
        if not backup.resolve().is_relative_to(root):
            raise ValueError('备份路径超出测试目录')
        backup.mkdir(parents=True)
        for path, content in zip(paths, before):
            if content is not None:
                (backup / path.name).write_bytes(content)
        (backup / 'location.json').write_text(json.dumps({'problem': problem, 'name': name}), encoding='utf-8')
        paths[0].parent.mkdir(parents=True, exist_ok=True)
        try:
            for path, content in zip(paths, after):
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic(path, content)
            if data_changed:
                _atomic(root / REVISION_FILE, uuid.uuid4().hex.encode('utf-8'))
        except OSError:
            for path, content in zip(paths, before):
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic(path, content)
            raise
        return {'changed': True, 'data_changed': data_changed, 'backup': str(backup),
                'case': None if action == 'delete' else read_case(root, problem, name)}
