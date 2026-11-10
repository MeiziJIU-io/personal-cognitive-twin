"""Evidence-linked experience drafts, human corrections, and SFT export."""
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sqlite3
from .identity import device_id


def timestamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return dt


def build(root, output, gap_seconds=120, max_seconds=300):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    device = device_id(root)
    with sqlite3.connect(f'{(root / "events.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as db:
        rows = [(i, json.loads(e), status, json.loads(r) if r else None)
                for i, e, status, r in db.execute('SELECT id,evidence,status,result FROM events')]
    rows.sort(key=lambda row: (row[1].get('device_id', device),
                              row[1].get('session_id', 'legacy-unknown'), timestamp(row[1]['at'])))
    groups, group = [], []
    for row in rows:
        event = row[1]
        if group:
            previous, first = group[-1][1], group[0][1]
            boundary = (event.get('device_id', device), event.get('session_id'), event.get('window', {}).get('app'))
            old = (previous.get('device_id', device), previous.get('session_id'), previous.get('window', {}).get('app'))
            if (boundary != old or (timestamp(event['at']) - timestamp(previous['at'])).total_seconds() > gap_seconds
                or (timestamp(event['at']) - timestamp(first['at'])).total_seconds() >= max_seconds):
                groups.append(group)
                group = []
        group.append(row)
    if group:
        groups.append(group)
    drafts = []
    for group in groups:
        timeline = []
        for i, event, status, result in group:
            timeline.append({'event_id': i, 'event_uid': event.get('event_uid', f'{device}:{i}'),
                             'at': event['at'], 'local_time': event.get('local_time'),
                             'capture_finished_at': event.get('capture_finished_at'),
                             'screenshot_captured_at': event.get('screenshot_captured_at'),
                             'screenshot': event.get('screenshot'), 'analysis_status': status,
                             'summary': result['summary'] if result else None,
                             'observations': result.get('observations', []) if result else [],
                             'uncertainties': result.get('uncertainties', []) if result else ['Not analyzed']})
        first, last = group[0][1], group[-1][1]
        episode = {'schema_version': 1, 'device_id': first.get('device_id', device),
                   'session_id': first.get('session_id', 'legacy-unknown'),
                   'started_at': first['at'], 'last_observed_at': last['at'],
                   'time_semantics': 'Observation bounds; not proof of continuous activity',
                   'review_status': 'draft', 'timeline': timeline,
                   'intent': None, 'actions': [], 'outcome': None,
                   'uncertainties': ['User intent, actions and outcome require independent evidence or user confirmation.'],
                   'analysis_complete': all(row[2] == 'done' for row in group)}
        fingerprint = json.dumps(episode, sort_keys=True, ensure_ascii=False)
        episode['episode_id'] = hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
        episode['draft_sha256'] = hashlib.sha256(fingerprint.encode()).hexdigest()
        drafts.append(episode)
    (output / 'experiences.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in drafts), encoding='utf-8')
    # HTML is escaped and contains no executable model-generated markup.
    sections = []
    for episode in drafts:
        lines = []
        for point in episode['timeline']:
            lines.append('<li><time>' + html.escape(point['at']) + '</time><p>' + html.escape(point['summary'] or '尚未分析') + '</p><pre>' + html.escape(json.dumps(point, ensure_ascii=False, indent=2)) + '</pre></li>')
        sections.append('<section><h2>' + episode['episode_id'] + '</h2><p>经历草稿 · ' + html.escape(episode['started_at']) + '</p><ul>' + ''.join(lines) + '</ul></section>')
    (output / 'timeline.html').write_text('<!doctype html><meta charset="utf-8"><title>经历时间线</title><style>body{max-width:1000px;margin:40px auto;font:16px system-ui}pre{white-space:pre-wrap}section{border-bottom:1px solid #ccc;padding:20px}time{color:#555}</style><h1>经历时间线</h1><p>时间来自原始采集记录；草稿不代表用户确认的经历。图片路径相对于采集数据目录。</p>' + ''.join(sections), encoding='utf-8')
    candidates = []
    for episode in drafts:
        if not episode['analysis_complete']:
            continue
        answer = '\n'.join(point['at'] + '：' + point['summary'] for point in episode['timeline'])
        candidates.append({'messages': [
            {'role': 'user', 'content': f"根据采集记录，回顾 {episode['started_at']} 至 {episode['last_observed_at']} 屏幕显示的内容。"},
            {'role': 'assistant', 'content': '以下是未经人工核验的屏幕观察整理，不代表已确认的用户行为：\n' + answer}],
            'metadata': {'episode_id': episode['episode_id'], 'review_status': 'unreviewed',
                         'source_event_uids': [point['event_uid'] for point in episode['timeline']],
                         'purpose': 'screen_observation_recall'}})
    (output / 'training_candidates.jsonl').write_text(
        ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in candidates), encoding='utf-8')
    return drafts


def read_drafts(directory):
    return [json.loads(line) for line in (Path(directory) / 'experiences.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]


def review(directory, episode_id, experience, reviewer, reject=False):
    drafts = read_drafts(directory)
    episode = next((item for item in drafts if item['episode_id'] == episode_id), None)
    if episode is None:
        raise ValueError('Unknown episode')
    if not reject and (not experience.strip() or not reviewer.strip() or not episode['analysis_complete']):
        raise ValueError('Approval requires complete analysis, reviewer and corrected experience text')
    record = {'episode_id': episode_id, 'draft_sha256': episode['draft_sha256'],
              'status': 'rejected' if reject else 'approved', 'experience': experience,
              'reviewer': reviewer, 'reviewed_at': datetime.now(timezone.utc).isoformat()}
    with (Path(directory) / 'reviews.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return record


def export_training(directory, destination):
    directory, destination = Path(directory), Path(destination)
    reviews = {}
    path = directory / 'reviews.jsonl'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            reviews[record['episode_id']] = record
    records, provenance = [], []
    for episode in read_drafts(directory):
        accepted = reviews.get(episode['episode_id'])
        if not accepted or accepted['status'] != 'approved' or accepted['draft_sha256'] != episode['draft_sha256']:
            continue
        # Memory QA is an explicit training objective, not imitation of inferred user actions.
        question = f"请回顾我在 {episode['started_at']} 至 {episode['last_observed_at']} 的这段已确认经历（编号 {episode['episode_id']}）。"
        records.append({'messages': [{'role': 'user', 'content': question},
                                     {'role': 'assistant', 'content': accepted['experience']}]})
        provenance.append({'row': len(records) - 1, 'episode': episode, 'review': accepted})
    if not records:
        raise ValueError('No approved, current experiences; no training file written')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    destination.with_suffix(destination.suffix + '.provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    return len(records)
