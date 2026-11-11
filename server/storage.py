"""Private content-addressed object storage. No public buckets/URLs."""
import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4


def object_key(digest):
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid object hash')
    return 'objects/' + digest[:2] + '/' + digest


class DiskStorage:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, path, digest):
        key = object_key(digest)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        temp = target.with_name(target.name + '.' + uuid4().hex)
        try:
            shutil.copyfile(path, temp)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        return key

    def chunks(self, digest):
        with (self.root / object_key(digest)).open('rb') as f:
            while chunk := f.read(1024 * 1024):
                yield chunk


class S3Storage:
    def __init__(self):
        import boto3
        self.bucket = os.environ['CAPTURE_S3_BUCKET']
        self.client = boto3.client('s3', endpoint_url=os.environ.get('CAPTURE_S3_ENDPOINT') or None)

    def put(self, path, digest):
        key = object_key(digest)
        self.client.upload_file(str(path), self.bucket, key)
        return key

    def chunks(self, digest):
        body = self.client.get_object(Bucket=self.bucket, Key=object_key(digest))['Body']
        try:
            yield from body.iter_chunks(chunk_size=1024 * 1024)
        finally:
            body.close()


def configured_storage():
    return S3Storage() if os.environ.get('CAPTURE_S3_BUCKET') else DiskStorage(os.environ.get('CAPTURE_OBJECT_DIR', 'server-data/objects'))
