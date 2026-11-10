"""Platform-independent scheduling and durable evidence queue."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Policy:
    idle_seconds: float = 60
    text_interval: float = 3
    screenshot_interval: float = 5
    visual_audit_interval: float = 30

class Scheduler:
    def __init__(self, policy):
        self.policy = policy
        self.last_text = self.last_shot = float('-inf')
        self.window = None
        self.inactive = True

    def step(self, now, window, idle, paused=False, locked=False, reading=False):
        if paused or locked or (idle >= self.policy.idle_seconds and not reading):
            self.inactive = True
            return False
        changed = window != self.window or self.inactive
        self.window, self.inactive = window, False
        if changed or now - self.last_text >= self.policy.text_interval:
            self.last_text = now
            return True
        return False

    def screenshot_due(self, now, incomplete):
        interval = (self.policy.screenshot_interval if incomplete
                    else self.policy.visual_audit_interval)
        return now - self.last_shot >= interval

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'events.sqlite3', timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, at TEXT NOT NULL, evidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', result TEXT, error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0)''')
        self.db.commit()
        self.last_signature = None

    def append(self, event):
        signature = hashlib.sha256(json.dumps(
            {k:v for k,v in event.items() if k not in ('at', 'idle_seconds')},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        # Keep each observation timestamp, even when content repeats.
        with self.db:
            cursor = self.db.execute('INSERT INTO events(at,evidence) VALUES (?,?)',
                                     (event['at'], json.dumps(event, ensure_ascii=False)))
        self.last_signature = signature
        return cursor.lastrowid
