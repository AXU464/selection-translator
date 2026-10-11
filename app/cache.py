"""Bounded local translation cache. Corruption disables caching, never translation."""
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time

log = logging.getLogger(__name__)


class TranslationCache:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.disabled = False

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=.25)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, value TEXT NOT NULL, used REAL NOT NULL)')
            return db
        except Exception:
            db.close()
            raise

    def _failure(self):
        self.disabled = True
        log.warning('Local translation cache unavailable; caching disabled for this session (contents omitted)')

    def get(self, key, limit):
        with self.lock:
            if self.disabled:
                return None
            db = None
            try:
                db = self._open()
                with db:
                    self._trim(db, limit)
                    row = db.execute('SELECT value FROM translations WHERE key=?', (key,)).fetchone()
                    if row is None:
                        return None
                    data = json.loads(row[0])
                    if not isinstance(data, dict) or not isinstance(data.get('text'), str) or not data['text'].strip() or not isinstance(data.get('model'), str):
                        raise ValueError('invalid cached record')
                    db.execute('UPDATE translations SET used=? WHERE key=?', (time.time(), key))
                return data
            except (OSError, sqlite3.Error, ValueError, TypeError):
                self._failure()
                return None
            finally:
                if db is not None:
                    db.close()

    @staticmethod
    def _trim(db, limit):
        db.execute('DELETE FROM translations WHERE key NOT IN (SELECT key FROM translations ORDER BY used DESC LIMIT ?)', (max(1, int(limit)),))

    def put(self, key, text, model, limit):
        with self.lock:
            if self.disabled:
                return
            db = None
            try:
                db = self._open()
                with db:
                    db.execute('INSERT OR REPLACE INTO translations VALUES (?, ?, ?)',
                               (key, json.dumps({'text': text, 'model': model}, ensure_ascii=False), time.time()))
                    self._trim(db, limit)
                # Reclaim old pages so large evictions cannot leave a growing file behind.
                db.execute('VACUUM')
            except (OSError, sqlite3.Error, ValueError, TypeError):
                self._failure()
            finally:
                if db is not None:
                    db.close()
