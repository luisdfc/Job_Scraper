from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .common import identity_aliases, now_iso, stable_id

STATES = {'pending', 'saved', 'applied', 'interview', 'discarded'}


class Store:
    """States and seen IDs are global; result sets remain independent immutable snapshots."""
    def __init__(self, root: Path):
        self.path = root / 'private' / 'job_search.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    first_run TEXT NOT NULL, last_run TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, job_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS states (
                    job_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
                    notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS state_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL, changed_at TEXT NOT NULL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=20000')
        return db

    def register(self, jobs: list[dict], run_id: str, when: str | None = None) -> list[dict]:
        when = when or now_iso()
        output = []
        with self.connect() as db:
            for original in jobs:
                j = dict(original)
                aliases = identity_aliases(j)
                existing = []
                for alias in aliases:
                    row = db.execute('SELECT job_id FROM aliases WHERE alias=?', (alias,)).fetchone()
                    if row:
                        existing.append(row['job_id'])
                ident = existing[0] if existing else stable_id(j)
                row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
                j['job_id'] = ident
                j['is_new'] = row is None
                j['first_seen'] = row['first_seen'] if row else when
                j['previous_seen'] = row['last_seen'] if row else ''
                if row:
                    db.execute('UPDATE jobs SET last_seen=?, last_run=? WHERE id=?', (when, run_id, ident))
                else:
                    db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (ident, when, when, run_id, run_id))
                for alias in aliases:
                    db.execute('INSERT OR IGNORE INTO aliases VALUES (?,?)', (alias, ident))
                # Never silently combine conflicting manual decisions on previously distinct IDs.
                if len(set(existing)) > 1:
                    j['identity_warning'] = 'La oferta enlaza identificadores con historiales distintos; revisar estados.'
                output.append(j)
        return output

    def states(self) -> dict:
        with self.connect() as db:
            return {r['job_id']: dict(r) for r in db.execute('SELECT * FROM states')}

    def set_state(self, ident: str, status: str, notes: str, revision: int | None = None) -> dict:
        if status not in STATES:
            raise ValueError('Estado no válido')
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError('Las notas deben ser texto de hasta 10.000 caracteres')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM jobs WHERE id=?', (ident,)).fetchone():
                raise ValueError('Identificador de oferta desconocido')
            row = db.execute('SELECT * FROM states WHERE job_id=?', (ident,)).fetchone()
            before = dict(row) if row else {'job_id': ident, 'status': 'pending', 'notes': '', 'revision': 0}
            if revision is not None and revision != before['revision']:
                raise RuntimeError('El estado cambió en otra pestaña. Recarga antes de guardar.')
            when = now_iso()
            after = {'job_id': ident, 'status': status, 'notes': notes, 'updated_at': when, 'revision': before['revision'] + 1}
            db.execute('INSERT INTO states VALUES (:job_id,:status,:notes,:updated_at,:revision) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, notes=excluded.notes, updated_at=excluded.updated_at, revision=excluded.revision', after)
            db.execute('INSERT INTO state_events(job_id,before_json,after_json,changed_at) VALUES (?,?,?,?)', (ident, json.dumps(before), json.dumps(after), when))
            return after

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
