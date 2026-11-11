import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from collector.sessions import create_session,finish_session,record_state,migrate_legacy,session_roots
from collector.core import Store
from collector.transport import export_bundle,import_bundle

class SessionTests(unittest.TestCase):
    def test_two_runs_and_bundle_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            a,b = create_session(base),create_session(base)
            self.assertNotEqual(a,b)
            ma=json.loads((a/'session.json').read_text())
            mb=json.loads((b/'session.json').read_text())
            self.assertEqual(ma['device_id'],mb['device_id'])
            record_state(a,'paused');record_state(a,'active')
            finish_session(a)
            self.assertEqual(json.loads((a/'session.json').read_text())['status'],'closed')
            self.assertEqual(json.loads((b/'session.json').read_text())['status'],'recording')
            export_bundle(a,base/'a.zip');import_bundle(base/'a.zip',base/'import')
            self.assertEqual(json.loads((base/'import/session.json').read_text())['session_id'],ma['session_id'])
            self.assertIn('paused',(base/'import/lifecycle.jsonl').read_text())

    def test_migration_preserves_original_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);store=Store(base)
            for sid in ('a','b',None):
                store.append({'at':'2026-11-11T00:00:00+00:00','session_id':sid,'screenshot':None})
            store.db.close()
            before=(base/'events.sqlite3').read_bytes()
            paths=migrate_legacy(base)
            self.assertEqual(len(paths),3)
            self.assertEqual(paths,migrate_legacy(base))
            self.assertEqual((base/'events.sqlite3').read_bytes(),before)
            self.assertEqual(len(session_roots(base)),3)
            for path in paths:
                with sqlite3.connect(path/'events.sqlite3') as db:
                    raw=db.execute('SELECT evidence FROM events').fetchone()[0]
                    self.assertTrue(json.loads(raw)['event_uid'])

if __name__=='__main__': unittest.main()
