import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from collector.sessions import create_session,finish_session
from collector.sync import upload_session

class SyncTests(unittest.TestCase):
    def test_retry_then_receipt_and_skip_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root=create_session(temp);meta=finish_session(root)
            calls=[]
            class Opener:
                def open(self,request,timeout):
                    calls.append(request.full_url)
                    self_data=request.data.read()
                    assert self_data[:2]==b'PK'
                    if len(calls)==1:
                        raise HTTPError(request.full_url,503,'unavailable',{},None)
                    return io.BytesIO(json.dumps({'sha256':request.full_url.rsplit('/',1)[1],
                                                  'session_id':meta['session_id']}).encode())
            with patch('collector.sync.client',return_value=('https://example.invalid',{},Opener())),patch('collector.sync.time.sleep'):
                first=upload_session(root)
                second=upload_session(root)
            self.assertEqual(first,second)
            self.assertEqual(first['status'],'uploaded')
            self.assertEqual(len(calls),2)
            self.assertTrue((root/'events.sqlite3').exists())

    def test_active_session_not_uploaded_and_failure_retains_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=create_session(temp)
            with self.assertRaises(ValueError): upload_session(root)
            finish_session(root)
            class Opener:
                def open(self,*args,**kwargs): raise TimeoutError('offline')
            with patch('collector.sync.client',return_value=('https://example.invalid',{},Opener())),patch('collector.sync.time.sleep'):
                with self.assertRaises(TimeoutError): upload_session(root)
            self.assertEqual(json.loads((root/'sync.json').read_text())['status'],'failed')
            self.assertTrue((root/'events.sqlite3').exists())
