"""Authenticated API for one owner's devices. Deploy behind HTTPS."""
import hmac
import os
from pathlib import Path
import sqlite3
from zipfile import BadZipFile
import tempfile
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
import psycopg
from .storage import configured_storage, object_key
from .ingest import ingest


def create_app(dsn=None, token=None, storage=None):
    dsn = dsn or os.environ.get('CAPTURE_DATABASE_URL')
    token = token or os.environ.get('CAPTURE_API_TOKEN')
    if not dsn or not token or len(token) < 32:
        raise RuntimeError('Configure CAPTURE_DATABASE_URL and a random CAPTURE_API_TOKEN (32+ characters)')
    storage = storage or configured_storage()
    app = FastAPI(title='Private capture dataset API', docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization.encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
            raise HTTPException(401, 'Unauthorized')

    @app.get('/health')
    def health():
        return {'status':'ok'}

    @app.put('/v1/bundles/{digest}', dependencies=[Depends(authorize)])
    async def upload(digest: str, request: Request):
        try:
            object_key(digest)
        except ValueError:
            raise HTTPException(400, 'Invalid SHA-256')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'upload.zip'
            size = 0
            with path.open('wb') as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 256 * 1024**2:
                        raise HTTPException(413, 'Bundle limit: 256 MiB')
                    f.write(chunk)
            def commit():
                with psycopg.connect(dsn) as db:
                    return ingest(path,digest,db,storage)
            try:
                return await run_in_threadpool(commit)
            except (ValueError, KeyError, FileNotFoundError, BadZipFile, sqlite3.DatabaseError):
                raise HTTPException(422, 'Invalid or conflicting session bundle')

    @app.get('/v1/sessions', dependencies=[Depends(authorize)])
    def sessions(limit: int = 50, offset: int = 0):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(400, 'Invalid pagination')
        with psycopg.connect(dsn) as db:
            rows = db.execute('''SELECT s.session_id,s.device_id,s.started_at,s.ended_at,
                (SELECT count(*) FROM capture_events e WHERE e.session_id=s.session_id),
                (SELECT b.sha256 FROM capture_bundles b WHERE b.session_id=s.session_id ORDER BY b.uploaded_at DESC LIMIT 1)
                FROM capture_sessions s ORDER BY s.started_at DESC,s.session_id LIMIT %s OFFSET %s''', (limit,offset)).fetchall()
        return [{'session_id':s,'device_id':d,'started_at':a,'ended_at':b,'events':n,'bundle_sha256':h} for s,d,a,b,n,h in rows]

    @app.get('/v1/bundles/{digest}', dependencies=[Depends(authorize)])
    def download(digest: str):
        try:
            object_key(digest)
        except ValueError:
            raise HTTPException(400, 'Invalid SHA-256')
        with psycopg.connect(dsn) as db:
            found = db.execute('SELECT 1 FROM capture_bundles WHERE sha256=%s', (digest,)).fetchone()
        if not found:
            raise HTTPException(404, 'Not found')
        return StreamingResponse(storage.chunks(digest), media_type='application/zip', headers={'Content-Disposition':f'attachment; filename="{digest}.zip"'})
    return app
