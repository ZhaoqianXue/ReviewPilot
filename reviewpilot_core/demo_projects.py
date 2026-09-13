"""Protected showcase snapshots and isolated, locally reproducible copies."""
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import shutil

from .atomic_files import atomic_write_json
from .project_store import read_json
from .setup_revision import setup_revision


def is_example(root: Path, project_id: str) -> bool:
    # Resolve exactly the same three baseline selections used in the sidebar.
    from .state_projection import _demo_history_project_items
    return any(item['id'] == project_id for item in _demo_history_project_items(root, '') if item['id'])


def require_mutable(root: Path, project_id: str) -> None:
    if is_example(root, project_id):
        raise ValueError('This example is read-only. Create your own copy to edit or run it.')


def copy_example(root: Path, project_id: str) -> dict:
    if not is_example(root, project_id):
        raise ValueError('Select one of the three example projects.')
    source = root / project_id
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Example copies cannot contain symbolic links.')
    if any(path.name.endswith('_pending.json') or path.name == 'pending.json' for path in source.rglob('*.json')):
        raise ValueError('This example has an unfinished update and cannot be copied yet.')
    new_id = 'example-copy-' + secrets.token_hex(6)
    destination = root / new_id
    staging = root / '.copies' / new_id
    source_prefixes = (str(source.resolve()), str(source), f'output/{project_id}')

    def relocate(value):
        if isinstance(value, str):
            for prefix in source_prefixes:
                if value == prefix or value.startswith(prefix + '/'):
                    return str(destination.resolve()) + value[len(prefix):]
            return value
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, dict):
            return {name: relocate(item) for name, item in value.items()}
        return value

    try:
        shutil.copytree(source, staging)
        for path in staging.rglob('*'):
            if path.suffix == '.json':
                atomic_write_json(path, relocate(json.loads(path.read_text())))
            elif path.suffix == '.jsonl':
                records = [relocate(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
                path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records))
        config = read_json(staging / 'search_conditions.json', {})
        config['project_path'] = str(destination.resolve())
        config['setup_revision'] = setup_revision(config)
        atomic_write_json(staging / 'search_conditions.json', config)
        title = read_json(staging / 'session.json', {}).get('title') or config.get('project_name') or project_id
        atomic_write_json(staging / 'session.json', {'title': f'{title} — my copy'})
        atomic_write_json(staging / 'example_origin.json', {'source_project_id': project_id,
                          'copied_at': datetime.now(timezone.utc).isoformat(), 'mode': 'precomputed_snapshot'})
        # A live sample from a baseline is not a live computation in the new copy.
        (staging / 'review/live_sample.json').unlink(missing_ok=True)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'id': new_id, 'title': f'{title} — my copy'}
