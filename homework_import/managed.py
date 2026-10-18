"""Managed student folders and incremental, non-destructive synchronization."""
from pathlib import Path
import uuid
import shutil

from .documents import sha
from .locking import workspace_lock
from .workspace import (prepare, read_json, write_json, import_submission,
                        summary, write_summary, export_sources, now)

MARKER = 'managed-folder.json'


def managed_config(root):
    path = root / MARKER
    if not path.is_file():
        return None
    config = read_json(path)
    folder = Path(config['submissions']).resolve()
    if root.resolve() != folder / 'work' or Path(config['output']).resolve() != folder / 'output':
        raise ValueError('绑定目录配置不一致，work 和 output 必须位于学生报告目录内')
    return config


def report_files(root):
    config = managed_config(root)
    folder = Path(config['submissions'])
    if not folder.is_dir():
        raise ValueError('学生报告目录暂时无法访问，已保留当前批次：' + str(folder))
    teacher = Path(read_json(root / 'documents' / 'teacher.json')['path']).resolve()
    files = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if (path.is_file() and not path.name.startswith('~$') and
                path.suffix.lower() in {'.doc', '.docx'} and path.resolve() != teacher):
            files.append(path)
    return files


def folder_signature(root):
    return tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in report_files(root))


def bind_folder(submissions, teacher=None):
    folder = Path(submissions).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError('学生报告目录不存在：' + str(folder))
    root, output = folder / 'work', folder / 'output'
    # Never follow a work/output junction into another directory.
    for path in (root, output):
        if path.resolve() != path:
            raise ValueError('work/output 不能是指向其他目录的链接：' + str(path))
    if (root / 'batch.json').is_file():
        state = read_json(root / 'batch.json')
        if Path(state['submissions_directory']).resolve() != folder:
            raise ValueError('已有 work 属于其他学生目录，请保留原数据并换用正确目录')
    else:
        if not teacher:
            raise ValueError('首次绑定此目录需要选择教师模板/任务书')
        prepare(Path(teacher), folder, root)
    output.mkdir(parents=True, exist_ok=True)
    config = managed_config(root)
    if not config:
        config = {'submissions': str(folder), 'output': str(output), 'generation': 0}
        write_json(root / MARKER, config)
    sync_folder(root)
    return {'workspace': str(root), 'output': str(output), **summary(read_json(root / 'batch.json'))}


def sync_folder(root):
    """Keep untouched documents verbatim; changed documents must be reviewed again."""
    with workspace_lock(root):
        config = managed_config(root)
        before = folder_signature(root)
        state = read_json(root / 'batch.json')
        expected = [p['id'] for p in read_json(root / 'assignment.json')['problems']]
        old = {d['filename']: d for d in state['documents']}
        documents, changed_ids = [], set()
        added = updated = 0
        for path in report_files(root):
            digest = sha(path.read_bytes())
            previous = old.pop(path.name, None)
            if previous and digest == (previous.get('observed_sha256') or previous.get('sha256')):
                documents.append(previous)
                continue
            item = import_submission(path, root, expected)
            item['observed_sha256'] = digest
            documents.append(item)
            changed_ids.add(item['id'])
            added += previous is None
            updated += previous is not None
        removed = len(old)
        changed_ids.update(d['id'] for d in old.values())
        if not changed_ids:
            return {'added': 0, 'updated': 0, 'removed': 0}
        if folder_signature(root) != before:
            raise ValueError('报告文件仍在写入，将在稳定后自动重试')
        # Re-evaluate duplicate identities, including resolution after deletion.
        groups = {}
        for doc in documents:
            doc['issues'] = [s for s in doc['issues'] if not s.startswith('同一学号存在多份提交')]
            if doc['student_id']:
                groups.setdefault(doc['student_id'], []).append(doc)
        for sid, group in groups.items():
            if len(group) > 1:
                for doc in group:
                    doc['issues'].append('同一学号存在多份提交，请核对版本：' + sid)
        state['documents'] = documents
        state['revision'] += 1
        state['events'].append({'at': now(), 'action': 'folder_sync', 'added': added,
                                'updated': updated, 'removed': removed})
        # Carry forward only unchanged students' reports; changed/deleted ones
        # remain in the previous generation for reference, never as live results.
        previous_reports = Path(config['output']) / 'reports' / ('batch-' + str(config['generation']))
        next_reports = previous_reports.parent / ('batch-' + str(config['generation'] + 1))
        for doc in documents:
            if doc['id'] in changed_ids:
                continue
            name = (doc['student_id'] or 'unknown') + '-' + doc['id']
            source, target = previous_reports / name, next_reports / name
            if source.is_dir():
                if not source.resolve().is_relative_to(previous_reports.resolve()) or not target.resolve().is_relative_to(next_reports.resolve()):
                    raise ValueError('学生报告输出路径越界')
                shutil.copytree(source, target, dirs_exist_ok=True)
        write_json(root / 'batch.json', state)
        write_summary(root, state)
        config['generation'] += 1
        config['synced_at'] = now()
        write_json(root / MARKER, config)
        # Retire advice for removed or replaced reports; never show stale advice.
        from .ai_review import load_results, write_review_summary
        results = load_results(root)
        results['items'] = {k: v for k, v in results['items'].items()
                            if v.get('document_id') not in changed_ids}
        write_json(root / 'ai-results.json', results)
        write_review_summary(root, results)
        return {'added': added, 'updated': updated, 'removed': removed}


def managed_export(root):
    config = managed_config(root)
    folder = Path(config['output']) / 'sources' / ('batch-' + uuid.uuid4().hex[:12])
    result = export_sources(root, folder)
    from .workbench import save_settings
    save_settings(root, {'exported': str(folder), 'export_generation': config['generation']})
    return {**result, 'path': str(folder)}
