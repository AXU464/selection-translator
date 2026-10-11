"""Local JSON-lines history, tolerant of bad rows and atomic-write interruptions."""
import copy
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import threading

log = logging.getLogger(__name__)


def tags_from_text(value):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError('标签最多 500 个字符。')
    tags = list(dict.fromkeys(part.strip() for part in value.replace('，', ',').split(',') if part.strip()))
    if len(tags) > 20 or any(len(tag) > 40 or any(ord(c) < 32 for c in tag) for tag in tags):
        raise ValueError('最多 20 个标签，每个最多 40 字，不能包含换行。')
    return tags


class HistoryStore:
    def __init__(self, path, limit=500):
        self.path = Path(path)
        self.pending = self.path.with_suffix('.jsonl.pending')
        self.lock = threading.RLock()
        self.limit = self.bounded_limit(limit)
        self.error = ''
        self.read_failed = False
        self.bad_lines = []
        self.records = []
        self._load()

    @staticmethod
    def bounded_limit(value):
        try:
            return max(1, min(5000, int(value)))
        except (ValueError, TypeError, OverflowError):
            return 500

    @staticmethod
    def _valid(row):
        return (isinstance(row, dict) and type(row.get('id')) is int and row['id'] > 0
                and isinstance(row.get('original'), str) and isinstance(row.get('translation'), str)
                and row['original'].strip() and row['translation'].strip()
                and row.get('direction') in ('zh', 'en')
                and type(row.get('ts')) in (int, float) and math.isfinite(row['ts']) and 0 <= row['ts'] <= 253402300799
                and isinstance(row.get('meta', {}), dict)
                and isinstance(row.get('tags', []), list) and all(isinstance(tag, str) for tag in row.get('tags', [])))

    def _load(self):
        records = {}
        for path in (self.path, self.pending):
            try:
                with path.open('rb') as source:
                    for line in source:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line.decode('utf-8-sig'))
                            if not self._valid(row):
                                raise ValueError('bad history row')
                            json.dumps(row, allow_nan=False)
                            row.setdefault('tags', [])
                            records[row['id']] = row
                        except (ValueError, TypeError, UnicodeError):
                            if line not in self.bad_lines:
                                self.bad_lines.append(line)
            except FileNotFoundError:
                pass
            except OSError:
                self.read_failed = True
                self.error = '历史文件暂时无法读取；本次新记录仅保存在内存。'
                log.warning('History read failed; existing files preserved (contents omitted)')
        self.last_id = max(records, default=0)
        self.records = sorted(records.values(), key=lambda row: (row['ts'], row['id']), reverse=True)[:self.limit]
        if self.bad_lines:
            self.error = '已跳过损坏的历史行，原始数据保留在本地文件中。'
            log.warning('History contains invalid rows; original bytes retained (contents omitted)')

    def _persist(self, rows, bad_lines=None, *, clearing=False):
        if self.read_failed and not clearing:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.pending.open('wb') as target:
                for row in rows:
                    target.write((json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
                for line in self.bad_lines if bad_lines is None else bad_lines:
                    target.write(line.rstrip(b'\r\n') + b'\n')
                target.flush()
                os.fsync(target.fileno())
            self.pending.replace(self.path)
            self.error = '已跳过损坏的历史行，原始数据保留在本地文件中。' if self.bad_lines and not clearing else ''
            return True
        except (OSError, ValueError, TypeError):
            self.error = '历史暂时无法保存；现有文件保留，本次记录仍可在窗口中查看。'
            log.warning('History write failed; original file preserved (contents omitted)')
            return False

    def all(self):
        with self.lock:
            return copy.deepcopy(self.records)

    def add(self, row):
        with self.lock:
            if not self._valid(row):
                log.warning('Invalid history record skipped (contents omitted)')
                return
            self.last_id = max(self.last_id, row['id'])
            self.records = [copy.deepcopy(row)] + [r for r in self.records if r['id'] != row['id']]
            self.records = self.records[:self.limit]
            self._persist(self.records)

    def resize(self, limit):
        with self.lock:
            self.limit = self.bounded_limit(limit)
            if len(self.records) > self.limit:
                self.records = self.records[:self.limit]
                self._persist(self.records)

    def query(self, keyword='', tag=''):
        keyword, tag = str(keyword).strip().casefold(), str(tag).strip()
        return [row for row in self.all() if
                (not keyword or keyword in row['original'].casefold() or keyword in row['translation'].casefold())
                and (not tag or tag in row.get('tags', []))]

    def set_tags(self, entry_id, text):
        tags = tags_from_text(text)
        with self.lock:
            rows = copy.deepcopy(self.records)
            row = next((r for r in rows if r['id'] == int(entry_id)), None)
            if row is None:
                raise ValueError('这条历史已被移除，请刷新。')
            row['tags'] = tags
            # Keep edits visible even when disk is unavailable, just like completed translations.
            self.records = rows
            return self._persist(rows)

    def clear(self):
        with self.lock:
            if not self._persist([], [], clearing=True):
                return False
            self.records = []
            self.bad_lines = []
            self.read_failed = False
            return True

    def export(self, kind, keyword='', tag=''):
        rows = self.query(keyword, tag)
        if kind == 'markdown':
            parts = ['# 翻译历史\n']
            for row in rows:
                stamp = datetime.fromtimestamp(row['ts'], timezone.utc).isoformat(timespec='seconds')
                target = '中文' if row['direction'] == 'zh' else 'English'
                parts.extend([f'## {stamp} · → {target}', '标签：' + '、'.join(row.get('tags', [])),
                              '### 原文', row['original'], '### 译文', row['translation'], '\n---\n'])
            return '\n\n'.join(parts)
        if kind == 'tsv':
            def field(text):
                return ' '.join(str(text).replace('\x00', '').split())
            return ''.join(field(row['original']) + '\t' + field(row['translation'] +
                ('  [标签：' + ', '.join(row['tags']) + ']' if row.get('tags') else '')) + '\n' for row in rows)
        raise ValueError('不支持的导出格式。')
