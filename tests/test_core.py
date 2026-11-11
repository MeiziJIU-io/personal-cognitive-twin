import json
import tempfile
import unittest
from collector.core import Policy, Scheduler, Store
from collector.model_protocol import validate_result, endpoint_options

class PolicyTests(unittest.TestCase):
    def test_idle_resume_and_window_switch(self):
        s = Scheduler(Policy())
        self.assertTrue(s.step(0, 'a', 0))
        self.assertFalse(s.step(1, 'a', 0))
        self.assertTrue(s.step(2, 'b', 0))
        self.assertFalse(s.step(3, 'b', 60))
        self.assertTrue(s.step(3.1, 'b', 0))

    def test_reading_never_overrides_lock_or_pause(self):
        s = Scheduler(Policy())
        self.assertTrue(s.step(0, 'a', 100, reading=True))
        self.assertFalse(s.step(5, 'a', 100, reading=True, locked=True))
        self.assertFalse(s.step(8, 'a', 0, reading=True, paused=True))

    def test_screenshot_budget_and_visual_audit(self):
        s = Scheduler(Policy())
        self.assertTrue(s.screenshot_due(0, True))
        s.last_shot = 0
        self.assertFalse(s.screenshot_due(4, True))
        self.assertTrue(s.screenshot_due(5, True))
        self.assertFalse(s.screenshot_due(29, False))
        self.assertTrue(s.screenshot_due(30, False))

    def test_durable_evidence_and_dedup(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            original = {'at': '2026-11-10T00:00:00Z', 'text': '原始文字'}
            self.assertEqual(s.append(original), 1)
            self.assertEqual(s.append(dict(original, at='2026-11-10T00:00:03Z')), 2)
            s.db.execute("UPDATE events SET result='agent interpretation' WHERE id=1")
            s.db.commit()
            s.db.close()
            reopened = Store(root)
            raw, result = reopened.db.execute('SELECT evidence,result FROM events').fetchone()
            self.assertEqual(json.loads(raw), original)
            self.assertEqual(result, 'agent interpretation')
            reopened.db.close()

    def test_local_endpoint_without_key(self):
        for host in ('localhost', '127.0.0.1', '[::1]'):
            local, headers = endpoint_options(f'http://{host}:11434/v1/chat/completions', '')
            self.assertTrue(local)
            self.assertNotIn('Authorization', headers)
        for url in ('http://example.com/v1/chat/completions',
                    'https://example.com/v1/chat/completions',
                    'http://localhost.evil.com/v1/chat/completions'):
            with self.assertRaises(ValueError):
                endpoint_options(url, '')

    def test_invalid_agent_output_is_rejected(self):
        for value in ([], {}, {'summary': 'x', 'observations': 'fake', 'uncertainties': []}):
            with self.assertRaises(ValueError):
                validate_result(value)
        self.assertEqual(validate_result({'summary':'未知', 'observations':[], 'uncertainties':['无法判断']} )['summary'], '未知')

if __name__ == '__main__':
    unittest.main()
