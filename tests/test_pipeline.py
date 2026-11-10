import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from collector.core import Store
from collector.transport import export_bundle, import_bundle
from collector.experiences import build, review, export_training
from collector.analyzer import analyze

class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self):
        return json.dumps({'done_reason':'stop', 'message': {'content': json.dumps({
            'summary':'窗口显示测试文档', 'observations':['可见测试文字'], 'uncertainties':['无法判断意图']})}}).encode()

class PipelineTests(unittest.TestCase):
    def populate(self, root):
        s = Store(root)
        (root / 'screenshots').mkdir()
        (root / 'screenshots' / 'a.png').write_bytes(b'fixture-image')
        for i, at in enumerate(('2026-11-10T01:00:00+00:00', '2026-11-10T01:00:03+00:00', '2026-11-10T01:05:00+00:00')):
            s.append({'at':at, 'device_id':'test-device', 'session_id':'session-one',
                      'event_uid': f'event-{i}', 'screenshot':'screenshots\\a.png',
                      'window':{'app':'notepad.exe'}})
        s.db.close()

    def test_transfer_analysis_review_training(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source, imported = temp / 'capture', temp / 'imported'
            self.populate(source)
            export_bundle(source, temp / 'transfer.zip')
            import_bundle(temp / 'transfer.zip', imported)
            self.assertEqual((imported / 'screenshots/a.png').read_bytes(), b'fixture-image')
            seen = []
            class Opener:
                def open(self, request, timeout):
                    seen.append(json.loads(request.data))
                    return Response()
            with patch('collector.analyzer.urllib.request.build_opener', return_value=Opener()):
                analyze(imported)
            self.assertEqual(len(seen), 3)
            self.assertFalse(seen[0]['think'])
            self.assertEqual(seen[0]['options']['num_ctx'], 16384)
            self.assertTrue(seen[0]['messages'][1]['images'])
            out = imported / 'experiences'
            drafts = build(imported, out)
            self.assertEqual(len(drafts), 2)
            self.assertEqual(len(drafts[0]['timeline']), 2)
            self.assertEqual(drafts[0]['started_at'], '2026-11-10T01:00:00+00:00')
            with self.assertRaises(ValueError):
                export_training(out, temp / 'unreviewed.jsonl')
            review(out, drafts[0]['episode_id'], '我确认当时打开了测试文档。', 'owner')
            self.assertEqual(export_training(out, temp / 'train.jsonl'), 1)
            data = json.loads((temp / 'train.jsonl').read_text())
            self.assertIn('2026-11-10', data['messages'][0]['content'])
            self.assertEqual(data['messages'][1]['content'], '我确认当时打开了测试文档。')
            with self.assertRaises(ValueError):
                import_bundle(temp / 'transfer.zip', imported)

    def test_timeline_exists_before_first_model_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'data'
            self.populate(root)
            class Unavailable:
                def open(inner, request, timeout):
                    self.assertTrue((root / 'experiences/timeline.html').exists())
                    drafts = (root / 'experiences/experiences.jsonl').read_text(encoding='utf-8')
                    self.assertIn('pending', drafts)
                    raise TimeoutError('test model unavailable')
            with patch('collector.analyzer.urllib.request.build_opener', return_value=Unavailable()):
                analyze(root, limit=1)
            self.assertTrue((root / 'experiences/timeline.html').exists())
            self.assertEqual((root / 'experiences/training_candidates.jsonl').read_text(), '')

    def test_tamper_rejected_and_no_partial_import(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            self.populate(temp / 'source')
            export_bundle(temp / 'source', temp / 'good.zip')
            with ZipFile(temp / 'good.zip') as good, ZipFile(temp / 'bad.zip', 'w') as bad:
                for name in good.namelist():
                    bad.writestr(name, b'changed' if name == 'screenshots/a.png' else good.read(name))
            with self.assertRaises(ValueError):
                import_bundle(temp / 'bad.zip', temp / 'dest')
            self.assertFalse((temp / 'dest').exists())

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with ZipFile(temp / 'bad.zip', 'w') as z:
                z.writestr('../escape', b'no')
            with self.assertRaises(ValueError):
                import_bundle(temp / 'bad.zip', temp / 'dest')

if __name__ == '__main__':
    unittest.main()
