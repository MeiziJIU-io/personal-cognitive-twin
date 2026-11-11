"""Optional authenticated sync. Offline capture never depends on server availability."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from .agent import endpoint_options
from .sessions import atomic_json, utc_now
from .transport import export_bundle, import_bundle


def client():
    url = os.environ.get('CAPTURE_SERVER_URL', '').rstrip('/')
    token = os.environ.get('CAPTURE_API_TOKEN', '')
    local, headers = endpoint_options(url, token)
    if len(token) < 32:
        raise ValueError('Set CAPTURE_API_TOKEN (32+ characters)')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    handlers = [NoRedirect()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))
    headers['Authorization'] = 'Bearer ' + token
    return url, headers, urllib.request.build_opener(*handlers)


def revision(root):
    digest = hashlib.sha256()
    with sqlite3.connect(f'{(root / "events.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as db:
        for row in db.execute('SELECT * FROM events ORDER BY id'):
            digest.update(json.dumps(row, ensure_ascii=False).encode())
    for name in ('session.json','lifecycle.jsonl','frame-pairs.jsonl','frame-labels.jsonl',
                 'experiences/experiences.jsonl','experiences/training_candidates.jsonl','experiences/reviews.jsonl'):
        path = root / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def upload_session(root):
    root = Path(root).resolve()
    meta = json.loads((root / 'session.json').read_text(encoding='utf-8'))
    if meta['status'] not in ('closed','interrupted'):
        raise ValueError('Close this capture session before syncing')
    url, headers, opener = client()
    source_revision = revision(root)
    state_path = root / 'sync.json'
    if state_path.exists():
        old = json.loads(state_path.read_text(encoding='utf-8'))
        if old.get('revision') == source_revision and old.get('server') == url and old.get('status') == 'uploaded':
            return old
    state = {'revision':source_revision, 'status':'pending', 'server':url, 'session_id':meta['session_id']}
    atomic_json(state_path, state)
    try:
        with tempfile.TemporaryDirectory(dir=root) as temp:
            bundle = Path(temp) / 'session.zip'
            export_bundle(root, bundle)
            if bundle.stat().st_size > 256 * 1024**2:
                raise ValueError('Bundle exceeds 256 MiB upload limit; keep local archive and use shorter sessions')
            with bundle.open('rb') as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest()
            headers.update({'Content-Type':'application/zip', 'Content-Length':str(bundle.stat().st_size)})
            for attempt in range(3):
                try:
                    with bundle.open('rb') as f:
                        request = urllib.request.Request(url + '/v1/bundles/' + digest, data=f, headers=headers, method='PUT')
                        with opener.open(request, timeout=300) as response:
                            result = json.load(response)
                    if result.get('sha256') != digest or result.get('session_id') != meta['session_id']:
                        raise ValueError('Upload receipt mismatch')
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code not in (429,500,502,503,504):
                        raise
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
        state.update(status='uploaded', sha256=digest, uploaded_at=utc_now())
    except Exception as exc:
        state.update(status='failed', error=type(exc).__name__, attempted_at=utc_now())
        atomic_json(state_path, state)
        raise
    atomic_json(state_path, state)
    return state


def list_remote():
    url, headers, opener = client()
    results, offset = [], 0
    while True:
        with opener.open(urllib.request.Request(url + f'/v1/sessions?limit=200&offset={offset}', headers=headers), timeout=30) as response:
            page = json.load(response)
        results.extend(page)
        if len(page) < 200:
            return results
        offset += 200


def download_bundle(digest, destination):
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid SHA-256')
    url, headers, opener = client()
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'download.zip'
        check = hashlib.sha256()
        size = 0
        with opener.open(urllib.request.Request(url + '/v1/bundles/' + digest, headers=headers), timeout=300) as response, path.open('wb') as f:
            while chunk := response.read(1024**2):
                size += len(chunk)
                if size > 256 * 1024**2:
                    raise ValueError('Download exceeds size limit')
                check.update(chunk)
                f.write(chunk)
        if check.hexdigest() != digest:
            raise ValueError('Downloaded bundle checksum mismatch')
        return import_bundle(path, destination)


def auto_sync(root):
    if os.environ.get('CAPTURE_AUTO_SYNC') != '1':
        return
    try:
        result = upload_session(root)
        print('Online sync:', result['status'], flush=True)
    except Exception as exc:
        print('Online sync deferred; local evidence retained:', type(exc).__name__, flush=True)
