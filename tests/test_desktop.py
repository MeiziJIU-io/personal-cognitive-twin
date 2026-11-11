import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from collector import desktop
from collector.analyzer import analyze
from collector.core import Store

class DesktopTests(unittest.TestCase):
    def test_settings_ignore_secrets_and_default_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.json'
            with patch.object(desktop,'SETTINGS',path):
                self.assertEqual(desktop.load_settings()['provider'],'ollama')
                path.write_text(json.dumps({'data':tmp,'api_key':'must-not-load','openai_model':'vision'}))
                config=desktop.load_settings()
                self.assertNotIn('api_key',config)
                self.assertEqual(config['openai_model'],'vision')

    def test_openai_image_payload_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root)
            (root/'shot.png').write_bytes(b'synthetic')
            store.append({'at':'2026-11-11T00:00:00+00:00','screenshot':'shot.png'})
            store.db.close()
            requests=[]
            class Response:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self):
                    return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
                        'summary':'screen','observations':['text'],'uncertainties':[]})}}]}).encode()
            class Opener:
                def open(self,request,timeout):requests.append(request);return Response()
            with patch('collector.analyzer.urllib.request.build_opener',return_value=Opener()),patch.dict('os.environ',{'COLLECTOR_AGENT_KEY':'synthetic-key'}):
                analyze(root,model='vision-model',endpoint='https://example.invalid/v1/chat/completions',provider='openai')
            payload=json.loads(requests[0].data)
            self.assertNotIn('think',payload)
            self.assertNotIn('options',payload)
            self.assertEqual(payload['messages'][1]['content'][1]['type'],'image_url')
            store=Store(root)
            result=json.loads(store.db.execute('SELECT result FROM events').fetchone()[0])
            self.assertEqual(result['provider'],'openai')
            self.assertIsNone(result['context_tokens'])
            store.db.close()

    def test_invalid_api_url_fails_before_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);store.db.close()
            with patch.dict('os.environ',{'COLLECTOR_AGENT_KEY':'synthetic-key'}):
                with self.assertRaises(ValueError):
                    analyze(tmp,endpoint='https://example.invalid/',provider='openai')

if __name__=='__main__':unittest.main()
