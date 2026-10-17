"""Decaying local formula preferences and per-view final observations."""
import logging
import math
from pathlib import Path
import sqlite3
import threading
import time

log = logging.getLogger(__name__)
HALF_LIFE = 30 * 86400
STYLES = {'sup','sub','both','sup-sub','sub-sup','nested','plain','original','custom',
          'both-reversed','sup-sub-reversed','sub-sup-reversed','nested-reversed'}


class FormulaPreferences:
    def __init__(self, path, clock=time.time):
        self.path=Path(path)
        self.clock=clock
        self.lock=threading.RLock()
        self.disabled=False
        self.session=None
        self.epoch=0

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db=sqlite3.connect(self.path,timeout=.3)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS scores (level TEXT, unit TEXT, style TEXT, score REAL, used REAL, PRIMARY KEY(level,unit,style))')
            db.execute('CREATE TABLE IF NOT EXISTS custom (unit TEXT PRIMARY KEY, tex TEXT, used REAL)')
            return db
        except Exception:
            db.close()
            raise

    def snapshot(self):
        with self.lock:
            if self.disabled: return {'content':{},'structure':{},'epoch':self.epoch}
            db=None
            try:
                db=self._open(); result={'content':{},'structure':{},'epoch':self.epoch}
                now=self.clock()
                for level,key,style,score,used in db.execute('SELECT level,unit,style,score,used FROM scores'):
                    if level not in result or style not in STYLES or not math.isfinite(score) or not math.isfinite(used): continue
                    result[level].setdefault(key,{})[style]=score*2**(-max(0,now-used)/HALF_LIFE)
                result['custom']={key:tex for key,tex in db.execute('SELECT unit,tex FROM custom') if isinstance(tex,str) and len(tex)<=4000}
                return result
            except (OSError,sqlite3.Error,ValueError,TypeError):
                self.disabled=True
                log.warning('Formula preferences unavailable. Automatic learning is disabled for this session.')
                return {'content':{},'structure':{},'epoch':self.epoch}
            finally:
                if db: db.close()

    def observe(self, entry, units, epoch):
        with self.lock:
            if epoch!=self.epoch or not isinstance(units,list) or len(units)>2000: return
            valid=[]
            for u in units:
                if (not isinstance(u,dict) or not isinstance(u.get('content'),str) or len(u['content'])>200
                        or not isinstance(u.get('structure'),str) or len(u['structure'])>30
                        or u.get('selected') not in STYLES): continue
                corrected=[x for x in u.get('corrected',[]) if x in STYLES][:50]
                valid.append({'content':u['content'],'structure':u['structure'], 'selected':u['selected'],'corrected':corrected,'tex':str(u.get('tex',''))[:4000]})
            if self.session and self.session[0]!=entry: self.settle()
            self.session=(entry,valid)

    def settle(self):
        with self.lock:
            session,self.session=self.session,None
            if not session or self.disabled: return
            db=None
            try:
                db=self._open();now=self.clock()
                with db:
                    for u in session[1]:
                        if u['selected']=='custom' and u['tex']:
                            db.execute('INSERT OR REPLACE INTO custom VALUES (?,?,?)',(u['content'],u['tex'],now))
                        updates=[(style,-1) for style in u['corrected']]+[(u['selected'],1)]
                        for level,key in [('content',u['content']),('structure',u['structure'])]:
                            for style,delta in updates:
                                row=db.execute('SELECT score,used FROM scores WHERE level=? AND unit=? AND style=?',(level,key,style)).fetchone()
                                score=(row[0]*2**(-max(0,now-row[1])/HALF_LIFE) if row else 0)+delta
                                db.execute('INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)',(level,key,style,score,now))
                    db.execute('DELETE FROM scores WHERE rowid NOT IN (SELECT rowid FROM scores ORDER BY used DESC LIMIT 20000)')
                    db.execute('DELETE FROM custom WHERE rowid NOT IN (SELECT rowid FROM custom ORDER BY used DESC LIMIT 2000)')
                db.execute('VACUUM')
            except (OSError,sqlite3.Error,ValueError,TypeError):
                self.disabled=True
                log.warning('Formula learning could not be saved. Existing data is preserved.')
            finally:
                if db: db.close()

    def clear(self):
        with self.lock:
            db=None
            try:
                db=self._open()
                with db:
                    db.execute('DELETE FROM scores')
                    db.execute('DELETE FROM custom')
                db.execute('VACUUM')
                self.session=None; self.epoch+=1; self.disabled=False
                return True
            except (OSError,sqlite3.Error):
                log.warning('Formula learning could not be cleared.')
                return False
            finally:
                if db:db.close()

    def evict_oldest(self):
        with self.lock:
            db=None
            try:
                db=self._open()
                with db:
                    count=db.execute('SELECT count(*) FROM scores').fetchone()[0]
                    db.execute('DELETE FROM scores WHERE rowid IN (SELECT rowid FROM scores ORDER BY used LIMIT ?)',(max(1,count//4),))
                    customs=db.execute('SELECT count(*) FROM custom').fetchone()[0]
                    db.execute('DELETE FROM custom WHERE rowid IN (SELECT rowid FROM custom ORDER BY used LIMIT ?)',(max(1,customs//4),))
                db.execute('VACUUM')
                return bool(count or customs)
            except (OSError,sqlite3.Error):return False
            finally:
                if db:db.close()
