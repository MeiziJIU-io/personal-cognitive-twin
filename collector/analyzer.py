"""Local Ollama analysis, usable independently of the Windows capture process."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request
from .agent import PROMPT, endpoint_options, validate_result
from .core import Store
from .identity import device_id
from .transport import portable_path


@contextmanager
def exclusive_worker(root):
    handle = open(Path(root) / 'analysis.lock', 'a+b')
    handle.seek(0)
    if not handle.read(1):
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def evidence_key(event):
    # Include observed time: no reuse of a previous interpretation across time.
    return hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def analyze(root, model='collector-qwen:16k', endpoint='http://127.0.0.1:11434/api/chat',
            event_id=None, limit=0, watch=False, timeout=600, provider='ollama'):
    if not Path(root, 'events.sqlite3').is_file():
        raise ValueError('No collected/imported database')
    local, headers = endpoint_options(endpoint, os.environ.get('COLLECTOR_AGENT_KEY', ''))
    if provider not in ('ollama', 'openai'):
        raise ValueError('Unknown provider')
    if provider == 'ollama' and not endpoint.endswith('/api/chat'):
        raise ValueError('Use the Ollama native /api/chat endpoint')
    if provider == 'openai' and not endpoint.endswith('/chat/completions'):
        raise ValueError('Use a complete OpenAI-compatible /chat/completions URL')
    store = Store(root)
    device = device_id(root)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    handlers = [NoRedirect()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    processed = 0
    try:
        with exclusive_worker(root):
            from .experiences import build
            report_dir = Path(root) / 'experiences'
            build(root, report_dir)
            print(f'Timeline: {(report_dir / "timeline.html").resolve()}', flush=True)
            while True:
                if event_id is not None:
                    row = store.db.execute('SELECT id,evidence FROM events WHERE id=?', (event_id,)).fetchone()
                    if row is None:
                        raise ValueError('Unknown event ID')
                else:
                    row = store.db.execute("SELECT id,evidence FROM events WHERE status='pending' AND attempts<3 ORDER BY attempts,id LIMIT 1").fetchone()
                if row is None:
                    if not watch:
                        break
                    time.sleep(2)
                    continue
                current_id, raw = row
                event = json.loads(raw)
                start = time.monotonic()
                print(f'Analyzing event {current_id}, captured {event["at"]}', flush=True)
                try:
                    user = {'role': 'user', 'content': raw}
                    if event.get('screenshot'):
                        path = (store.root / portable_path(event['screenshot'])).resolve()
                        if not path.is_relative_to(store.root.resolve()):
                            raise ValueError('Invalid image path')
                        user['images'] = [base64.b64encode(path.read_bytes()).decode()]
                    payload = {'model': model, 'stream': False, 'think': False, 'format': 'json',
                               'options': {'num_ctx': 16384, 'num_predict': 2048},
                               'messages': [{'role': 'system', 'content': PROMPT}, user]}
                    if provider == 'openai':
                        content = [{'type': 'text', 'text': raw}]
                        for image_data in user.get('images', []):
                            content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image_data}})
                        payload = {'model': model, 'messages': [{'role': 'system', 'content': PROMPT},
                                   {'role': 'user', 'content': content}], 'response_format': {'type': 'json_object'}}
                    request = urllib.request.Request(endpoint, json.dumps(payload).encode(), headers)
                    with opener.open(request, timeout=timeout) as response:
                        result = json.load(response)
                    folder = store.root / 'analysis-responses'
                    folder.mkdir(exist_ok=True)
                    (folder / f'{current_id}-{time.time_ns()}.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
                    if provider == 'openai':
                        choice = result['choices'][0]
                        reason, answer = choice.get('finish_reason'), choice['message']['content']
                    else:
                        reason, answer = result.get('done_reason'), result['message']['content']
                    if reason == 'length':
                        raise ValueError('Output truncated; increase budget or reduce input')
                    output = validate_result(json.loads(answer))
                    output.update({'event_id': current_id,
                                   'event_uid': event.get('event_uid', f'{device}:{current_id}'),
                                   'device_id': event.get('device_id', device),
                                   'session_id': event.get('session_id', 'legacy-unknown'),
                                   'captured_at': event['at'], 'local_time': event.get('local_time'),
                                   'analyzed_at': datetime.now(timezone.utc).isoformat(),
                                   'latency_seconds': round(time.monotonic() - start, 3),
                                   'model': model, 'kind': 'agent_interpretation',
                                   'evidence_sha256': evidence_key(event), 'provider': provider,
                                   'thinking': False if provider == 'ollama' else None,
                                   'context_tokens': 16384 if provider == 'ollama' else None, 'review_status': 'unreviewed'})
                    with store.db:
                        store.db.execute("UPDATE events SET status='done',result=?,error=NULL WHERE id=?",
                                         (json.dumps(output, ensure_ascii=False), current_id))
                    from .experiences import build
                    build(root, Path(root) / 'experiences')
                    print(f'Done event {current_id}: {output["latency_seconds"]} seconds', flush=True)
                except Exception as exc:
                    with store.db:
                        store.db.execute("UPDATE events SET attempts=attempts+1,error=?,status=CASE WHEN attempts>=2 THEN 'failed' ELSE 'pending' END WHERE id=?",
                                         (str(exc)[:500], current_id))
                    build(root, report_dir)
                    print(f'Failed event {current_id}: {exc}', flush=True)
                processed += 1
                if event_id is not None or (limit and processed >= limit):
                    break
    finally:
        store.db.close()
