import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents(kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS events(run_id TEXT, seq INTEGER, data TEXT, PRIMARY KEY(run_id,seq));
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable events'); END;
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, data TEXT);
            CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots BEGIN SELECT RAISE(ABORT,'immutable snapshot'); END;
            ''')
    def connect(self):
        return sqlite3.connect(self.path, timeout=10)
    def put(self, kind, obj):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?)', (kind,obj['id'],json.dumps(obj)))
    def get(self, kind, ident):
        with self.connect() as db:
            row=db.execute('SELECT data FROM documents WHERE kind=? AND id=?',(kind,ident)).fetchone()
        return json.loads(row[0]) if row else None
    def list(self, kind):
        with self.connect() as db:
            rows=db.execute('SELECT data FROM documents WHERE kind=? ORDER BY rowid DESC',(kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]
    def event(self, run_id, event_type, **fields):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?',(run_id,)).fetchone()[0]
            obj=dict(id=f'{run_id}:{seq}',run_id=run_id,sequence_number=seq,timestamp=now(),event_type=event_type,**fields)
            db.execute('INSERT INTO events VALUES(?,?,?)',(run_id,seq,json.dumps(obj)))
        return obj
    def events(self, run_id):
        with self.connect() as db:
            rows=db.execute('SELECT data FROM events WHERE run_id=? ORDER BY seq',(run_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]
    def snapshot(self, obj):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?)',(obj['snapshot_id'],json.dumps(obj)))
    def get_snapshot(self, ident):
        with self.connect() as db:
            row=db.execute('SELECT data FROM snapshots WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0]) if row else None
