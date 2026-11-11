"""Versioned, checksummed offline transfer; imports never overwrite a data directory."""
import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED
from .identity import device_id

MAX_FILE = 1024 * 1024 * 1024
MAX_TOTAL = 20 * MAX_FILE


def portable_path(value):
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or not path.parts:
        raise ValueError('Unsafe relative path')
    return path.as_posix()


def export_bundle(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    identity = device_id(root)
    if not (root / 'events.sqlite3').is_file():
        raise ValueError('No evidence database')
    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp)
        with sqlite3.connect(f'{(root / "events.sqlite3").as_uri()}?mode=ro', uri=True) as source:
            with sqlite3.connect(staging / 'events.sqlite3') as target:
                source.backup(target)
                screenshots = {json.loads(row[0]).get('screenshot') for row in target.execute('SELECT evidence FROM events')}
        files = {'events.sqlite3': staging / 'events.sqlite3'}
        for name in ('session.json', 'lifecycle.jsonl', 'frame-pairs.jsonl', 'frame-labels.jsonl',
                     'experiences/experiences.jsonl', 'experiences/training_candidates.jsonl',
                     'experiences/reviews.jsonl'):
            if (root / name).is_file():
                (staging / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / name, staging / name)
                files[name] = staging / name
        for screenshot in screenshots - {None, ''}:
            relative = portable_path(screenshot)
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('Missing or invalid screenshot: ' + relative)
            files[relative] = path
        manifest = {'schema_version': 1, 'device_id': identity,
                    'exported_at': datetime.now(timezone.utc).isoformat(),
                    'files': {name: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                     'size': path.stat().st_size} for name, path in files.items()}}
        manifest['bundle_id'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidental destruction of a previous package.
        with destination.open('xb') as handle:
            with ZipFile(handle, 'w', ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
                for name, path in files.items():
                    archive.write(path, name)
    return manifest


def import_bundle(bundle, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Import destination must be new; keep each device/batch separate')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp) / 'data'
        stage.mkdir()
        with ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)) or sum(i.file_size for i in infos) > MAX_TOTAL:
                raise ValueError('Duplicate entries or excessive bundle size')
            for item in infos:
                if portable_path(item.filename) != item.filename or item.file_size > MAX_FILE:
                    raise ValueError('Invalid bundle entry')
            if archive.getinfo('manifest.json').file_size > 10_000_000:
                raise ValueError('Manifest too large')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest.get('schema_version') != 1:
                raise ValueError('Unsupported bundle schema')
            expected = manifest['files']
            if set(names) != set(expected) | {'manifest.json'} or 'events.sqlite3' not in expected:
                raise ValueError('Manifest mismatch')
            for name, metadata in expected.items():
                data = archive.read(name)
                if len(data) != metadata['size'] or hashlib.sha256(data).hexdigest() != metadata['sha256']:
                    raise ValueError('Checksum mismatch: ' + name)
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (stage / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            (stage / 'device.json').write_text(json.dumps({'device_id': manifest['device_id']}), encoding='utf-8')
        with sqlite3.connect(f'{(stage / "events.sqlite3").as_uri()}?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Invalid database')
            for (raw,) in db.execute('SELECT evidence FROM events'):
                event = json.loads(raw)
                if event.get('screenshot') and portable_path(event['screenshot']) not in expected:
                    raise ValueError('Unbundled evidence')
        stage.rename(destination)
    return manifest
