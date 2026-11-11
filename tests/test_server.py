"""Run against a disposable PostgreSQL database via TEST_DATABASE_URL."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'requires disposable PostgreSQL')
class ServerTests(unittest.TestCase):
    def test_authenticated_idempotent_round_trip_and_collision(self):
        import psycopg
        from fastapi.testclient import TestClient
        from server.app import create_app
        from server.storage import DiskStorage
        from collector.sessions import create_session,finish_session
        from collector.core import Store
        from collector.transport import export_bundle,import_bundle
        dsn=os.environ['TEST_DATABASE_URL']
        with psycopg.connect(dsn,autocommit=True) as db:
            db.execute(Path('server/schema.sql').read_text())
            db.execute(Path('server/schema.sql').read_text())
        token='synthetic-test-token-'+'a'*32
        with tempfile.TemporaryDirectory() as tmp:
            temp=Path(tmp);root=create_session(temp/'local')
            meta=json.loads((root/'session.json').read_text())
            (root/'screenshots').mkdir();(root/'screenshots/a.png').write_bytes(b'synthetic')
            store=Store(root)
            store.append({'at':meta['started_at'],'event_uid':meta['session_id']+'-e1',
                'device_id':meta['device_id'],'session_id':meta['session_id'],'screenshot':'screenshots/a.png'})
            store.db.close();finish_session(root)
            (root/'experiences').mkdir()
            (root/'experiences/training_candidates.jsonl').write_text('{"synthetic":true}\n')
            export_bundle(root,temp/'batch.zip')
            data=(temp/'batch.zip').read_bytes();digest=hashlib.sha256(data).hexdigest()
            app=create_app(dsn,token,DiskStorage(temp/'objects'))
            with TestClient(app) as client:
                url='/v1/bundles/'+digest
                self.assertEqual(client.put(url,content=data).status_code,401)
                headers={'Authorization':'Bearer '+token}
                for _ in range(2):
                    response=client.put(url,content=data,headers=headers)
                    self.assertEqual(response.status_code,200,response.text)
                sessions=client.get('/v1/sessions',headers=headers).json()
                self.assertEqual(next(x for x in sessions if x['session_id']==meta['session_id'])['events'],1)
                result=client.get(url,headers=headers)
                self.assertEqual(hashlib.sha256(result.content).hexdigest(),digest)
                (temp/'download.zip').write_bytes(result.content)
                import_bundle(temp/'download.zip',temp/'restored')
                self.assertEqual((temp/'restored/screenshots/a.png').read_bytes(),b'synthetic')
                self.assertEqual((temp/'restored/experiences/training_candidates.jsonl').read_text(),'{"synthetic":true}\n')
                with psycopg.connect(dsn) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM dataset_artifacts WHERE bundle_sha256=%s AND relative_path=%s',
                        (digest,'experiences/training_candidates.jsonl')).fetchone()[0],1)
                # Tampered bytes cannot be uploaded under an existing digest.
                self.assertEqual(client.put(url,content=b'bad',headers=headers).status_code,422)
                # Same event ID with changed evidence must roll back and retain the original.
                store=Store(root)
                event=json.loads(store.db.execute('SELECT evidence FROM events').fetchone()[0]);event['changed']=True
                store.db.execute('UPDATE events SET evidence=?',(json.dumps(event),));store.db.commit();store.db.close()
                export_bundle(root,temp/'changed.zip')
                changed=(temp/'changed.zip').read_bytes();changed_hash=hashlib.sha256(changed).hexdigest()
                self.assertEqual(client.put('/v1/bundles/'+changed_hash,content=changed,headers=headers).status_code,422)
                with psycopg.connect(dsn) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM capture_events WHERE session_id=%s',(meta['session_id'],)).fetchone()[0],1)
                    self.assertEqual(db.execute('SELECT count(*) FROM capture_bundles WHERE sha256=%s',(changed_hash,)).fetchone()[0],0)

if __name__=='__main__': unittest.main()
