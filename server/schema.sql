-- PostgreSQL 16+. Private single-owner deployment; collectors never receive DB credentials.
BEGIN;
CREATE TABLE IF NOT EXISTS schema_versions (
 version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS devices (
 device_id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS capture_sessions (
 session_id text PRIMARY KEY,
 device_id text NOT NULL REFERENCES devices(device_id),
 started_at timestamptz NOT NULL, ended_at timestamptz NOT NULL,
 status text NOT NULL CHECK (status IN ('closed','interrupted')),
 metadata jsonb NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now(),
 CHECK (ended_at >= started_at)
);
CREATE TABLE IF NOT EXISTS blobs (
 sha256 text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
 object_key text UNIQUE NOT NULL, size_bytes bigint NOT NULL CHECK (size_bytes >= 0)
);
CREATE TABLE IF NOT EXISTS capture_events (
 event_uid text PRIMARY KEY,
 session_id text NOT NULL REFERENCES capture_sessions(session_id),
 local_id bigint NOT NULL,
 captured_at timestamptz NOT NULL,
 image_sha256 text REFERENCES blobs(sha256),
 evidence jsonb NOT NULL,
 UNIQUE (session_id, local_id)
);
CREATE INDEX IF NOT EXISTS events_session_time ON capture_events(session_id,captured_at);
CREATE TABLE IF NOT EXISTS analysis_results (
 result_sha256 text PRIMARY KEY,
 event_uid text NOT NULL REFERENCES capture_events(event_uid),
 result jsonb NOT NULL,
 uploaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS capture_bundles (
 sha256 text PRIMARY KEY REFERENCES blobs(sha256),
 session_id text NOT NULL REFERENCES capture_sessions(session_id),
 manifest jsonb NOT NULL,
 uploaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bundles_session_time ON capture_bundles(session_id,uploaded_at DESC);
CREATE TABLE IF NOT EXISTS dataset_artifacts (
 bundle_sha256 text NOT NULL REFERENCES capture_bundles(sha256),
 relative_path text NOT NULL,
 blob_sha256 text NOT NULL REFERENCES blobs(sha256),
 PRIMARY KEY (bundle_sha256, relative_path)
);
-- Lifecycle JSON and snapshot analysis results also remain in the checksummed bundle.
INSERT INTO schema_versions(version) VALUES (1) ON CONFLICT DO NOTHING;
COMMIT;
