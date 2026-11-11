"""One start/stop run = one independently portable capture session."""
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4, uuid5, NAMESPACE_URL
from .identity import device_id
from .core import Store


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def create_session(base):
    base = Path(base).resolve()
    identity = device_id(base)
    sid = str(uuid4())
    local = datetime.now().astimezone()
    root = base / 'sessions' / local.strftime('%Y-%m-%d') / sid
    root.mkdir(parents=True)
    atomic_json(root / 'device.json', {'device_id': identity})
    atomic_json(root / 'session.json', {'schema_version': 1, 'session_id': sid,
        'device_id': identity, 'started_at': utc_now(), 'local_started_at': local.isoformat(),
        'ended_at': None, 'status': 'recording'})
    store = Store(root)
    store.db.close()
    record_state(root, 'recording')
    update_index(base)
    return root


def record_state(root, state):
    path = Path(root) / 'lifecycle.jsonl'
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'at': utc_now(), 'state': state}) + '\n')


def finish_session(root, interrupted=False):
    root = Path(root)
    metadata = json.loads((root / 'session.json').read_text(encoding='utf-8'))
    if metadata['status'] != 'recording':
        return metadata
    metadata.update(ended_at=utc_now(), status='interrupted' if interrupted else 'closed')
    with sqlite3.connect(root / 'events.sqlite3') as db:
        metadata['event_count'] = db.execute('SELECT count(*) FROM events').fetchone()[0]
    record_state(root, metadata['status'])
    atomic_json(root / 'session.json', metadata)
    return metadata


def session_roots(base):
    base = Path(base).resolve()
    if (base / 'session.json').is_file():
        return [base]
    return sorted(p.parent for p in (base / 'sessions').glob('*/*/session.json'))


def update_index(base):
    base = Path(base).resolve()
    if (base / 'session.json').exists():
        return
    entries = []
    for root in session_roots(base):
        meta = json.loads((root / 'session.json').read_text(encoding='utf-8'))
        relative = root.relative_to(base).as_posix()
        label = f"{meta['started_at']} · {meta['session_id']} · {meta['status']}"
        entries.append('<li>' + html.escape(label) + ' <a href="' + relative + '/experiences/timeline.html">时间线</a> · ' + html.escape(relative) + '</li>')
    (base / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>采集批次</title><h1>采集批次</h1><p>每次启动独立保存；尚未分析时可先运行 build-all 生成时间线。</p><ul>' + ''.join(entries) + '</ul>', encoding='utf-8')


def migrate_legacy(base):
    """Copy old evidence by known session ID; never delete or rewrite the original DB."""
    from .transport import portable_path
    base = Path(base).resolve()
    path = base / 'events.sqlite3'
    if not path.exists():
        return []
    identity = device_id(base)
    groups = {}
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,at,evidence,status,result,error,attempts FROM events').fetchall()
    for row in rows:
        event = json.loads(row[2])
        key = (event.get('device_id', identity), event.get('session_id') or 'legacy-unknown')
        groups.setdefault(key, []).append(row)
    destinations = []
    for (device, old_sid), rows in groups.items():
        sid = str(uuid5(NAMESPACE_URL, 'capture-migration:' + device + ':' + old_sid))
        root = base / 'sessions' / 'legacy' / sid
        if root.exists():
            destinations.append(root)
            continue
        # Staging rename makes interrupted migrations retryable.
        stage = base / 'sessions' / 'legacy' / (sid + '.staging-' + uuid4().hex)
        stage.mkdir(parents=True)
        try:
            store = Store(stage)
            try:
                for i, at, raw, status, result, error, attempts in rows:
                    event = json.loads(raw)
                    event['legacy_session_id'] = event.get('session_id')
                    event.update(session_id=sid, device_id=device)
                    event.setdefault('event_uid', str(uuid5(NAMESPACE_URL, f'{device}:{old_sid}:{i}')))
                    if event.get('screenshot'):
                        relative = portable_path(event['screenshot'])
                        source = (base / relative).resolve()
                        if not source.is_relative_to(base) or not source.is_file():
                            raise ValueError('Missing legacy screenshot')
                        dest = stage / relative
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, dest)
                        event['screenshot'] = relative
                    store.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                                     (i, at, json.dumps(event, ensure_ascii=False), status, result, error, attempts))
                store.db.commit()
            finally:
                store.db.close()
            times = sorted(row[1] for row in rows)
            atomic_json(stage / 'device.json', {'device_id': device})
            atomic_json(stage / 'session.json', {'schema_version':1, 'session_id':sid, 'device_id':device,
                'started_at':times[0], 'ended_at':times[-1], 'status':'closed', 'event_count':len(rows),
                'legacy_session_id':old_sid, 'time_semantics':'observation bounds; actual start/stop unknown'})
            stage.rename(root)
            destinations.append(root)
        except Exception:
            shutil.rmtree(stage)
            raise
    update_index(base)
    return destinations
