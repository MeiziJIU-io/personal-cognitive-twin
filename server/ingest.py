"""Validate an immutable session snapshot before publishing it in PostgreSQL."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from zipfile import ZipFile
from psycopg.types.json import Jsonb
from collector.transport import import_bundle, portable_path


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def aware(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Timezone is required')
    return result


def ingest(bundle, digest, connection, storage):
    if sha256(bundle) != digest:
        raise ValueError('Bundle hash mismatch')
    # Limits apply to expanded data, not just compressed HTTP bytes.
    with ZipFile(bundle) as archive:
        if len(archive.infolist()) > 20000 or sum(i.file_size for i in archive.infolist()) > 1024**3:
            raise ValueError('Expanded bundle too large')
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / 'validated'
        manifest = import_bundle(bundle, root)
        meta = json.loads((root / 'session.json').read_text(encoding='utf-8'))
        sid, device = meta['session_id'], meta['device_id']
        if not isinstance(sid, str) or not isinstance(device, str) or not 1 <= len(sid) <= 128 or not 1 <= len(device) <= 128:
            raise ValueError('Invalid identity')
        if meta['status'] not in ('closed', 'interrupted'):
            raise ValueError('Close capture before uploading')
        started, ended = aware(meta['started_at']), aware(meta['ended_at'])
        if ended < started:
            raise ValueError('Invalid session times')
        if manifest['device_id'] != device:
            raise ValueError('Device mismatch')
        # Never trust executable schema objects from imported SQLite.
        with sqlite3.connect(f'{(root / "events.sqlite3").as_uri()}?mode=ro', uri=True) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            if db.execute("SELECT type FROM sqlite_master WHERE name='events'").fetchone() != ('table',):
                raise ValueError('events must be a table')
            rows = db.execute('SELECT id,evidence,status,result FROM events ORDER BY id').fetchall()
        validated, blobs = [], {}
        for local_id, raw, status, result in rows:
            event = json.loads(raw)
            if event.get('session_id') != sid or event.get('device_id') != device:
                raise ValueError('Mixed-session bundle')
            uid = event['event_uid']
            if not isinstance(uid, str) or not 1 <= len(uid) <= 128:
                raise ValueError('Invalid event identity')
            captured = aware(event['at'])
            # Clock adjustments can place observations outside wall-clock bounds; preserve raw time.
            image_hash = None
            if event.get('screenshot'):
                image = root / portable_path(event['screenshot'])
                image_hash = sha256(image)
                blobs[image_hash] = image
            validated.append((uid, local_id, captured, image_hash, event,
                              json.loads(result) if result else None))
        # Content addressed uploads can leave harmless unreferenced objects on DB failure.
        # No DB row is published until all referenced objects have been stored.
        artifacts = []
        for name in manifest['files']:
            if name.endswith(('.json', '.jsonl')):
                path = root / name
                file_hash = sha256(path)
                blobs[file_hash] = path
                artifacts.append((digest,name,file_hash))
        blobs[digest] = Path(bundle)
        objects = [(key, storage.put(path, key), path.stat().st_size) for key, path in blobs.items()]
        with connection.transaction():
            connection.execute('INSERT INTO devices(device_id) VALUES (%s) ON CONFLICT DO NOTHING', (device,))
            connection.execute('''INSERT INTO capture_sessions(session_id,device_id,started_at,ended_at,status,metadata)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (sid,device,started,ended,meta['status'],Jsonb(meta)))
            existing = connection.execute('SELECT device_id,metadata FROM capture_sessions WHERE session_id=%s FOR UPDATE', (sid,)).fetchone()
            if existing != (device, meta):
                raise ValueError('Session identity collision')
            for row in objects:
                connection.execute('INSERT INTO blobs VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', row)
            for uid, local_id, captured, image_hash, event, result in validated:
                connection.execute('''INSERT INTO capture_events VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING''', (uid,sid,local_id,captured,image_hash,Jsonb(event)))
                existing = connection.execute('SELECT session_id,local_id,evidence FROM capture_events WHERE event_uid=%s', (uid,)).fetchone()
                if existing != (sid, local_id, event):
                    raise ValueError('Event identity collision')
                if result is not None:
                    result_hash = hashlib.sha256((uid + json.dumps(result, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
                    connection.execute('INSERT INTO analysis_results(result_sha256,event_uid,result) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                                       (result_hash,uid,Jsonb(result)))
            connection.execute('INSERT INTO capture_bundles(sha256,session_id,manifest) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                               (digest,sid,Jsonb(manifest)))
            for artifact in artifacts:
                connection.execute('INSERT INTO dataset_artifacts VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',artifact)
        return {'sha256':digest, 'session_id':sid, 'events':len(validated)}
