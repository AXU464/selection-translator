"""Budget for automatic local data. User exports are measured separately."""
import logging
from pathlib import Path
import threading

log=logging.getLogger(__name__)
LIMIT=1024**3


def recycle(path, root):
    from win32com.shell import shell, shellcon
    target=Path(path).resolve(strict=True)
    scope=Path(root).resolve(strict=True)
    if target==scope or not target.is_relative_to(scope) or not target.is_file():
        raise OSError('Invalid cleanup target')
    result,aborted=shell.SHFileOperation((0,shellcon.FO_DELETE,str(target),None,
        shellcon.FOF_ALLOWUNDO|shellcon.FOF_NOCONFIRMATION|shellcon.FOF_SILENT,None,None))
    if result or aborted:raise OSError('Cleanup failed')


class StorageBudget:
    def __init__(self, root, cache, history, formulas, limit=LIMIT):
        self.root=Path(root);self.cache=cache;self.history=history;self.formulas=formulas
        self.limit=limit;self.lock=threading.RLock();self.error=''

    @staticmethod
    def files(directory):
        if not directory.is_dir():return []
        result=[]
        try:
            for path in directory.rglob('*'):
                try:
                    if path.is_symlink() or not path.is_file():continue
                    stat=path.stat();result.append((path,stat.st_size,stat.st_mtime))
                except OSError:continue
        except OSError:pass
        return result

    def status(self):
        logs=self.files(self.root/'logs')
        data=self.files(self.root/'local-data')
        export_root=(self.root/'local-data'/'exports').resolve()
        exports=[row for row in data if row[0].resolve().is_relative_to(export_root)]
        # WebView and Python caches are runtime data too. Live browser files are
        # measured, but cleanup never touches a running browser profile.
        runtime=self.files(self.root/'.webview')+self.files(self.root/'__pycache__')+self.files(self.root/'app'/'__pycache__')
        return {'total':sum(size for _,size,_ in logs+data+runtime)-sum(size for _,size,_ in exports),
                'exports':sum(size for _,size,_ in exports),'limit':self.limit,'error':self.error}

    def enforce(self, before_browser_start=False):
        try:
            self._enforce(before_browser_start)
        except Exception:
            self.error='自动清理暂未完成，翻译仍可使用。请检查本地目录的访问权限。'
            log.warning('Storage maintenance failed. Translation remains available.')

    def _enforce(self, before_browser_start):
        with self.lock:
            # Leave room for atomic history replacements and in-flight output.
            if self.status()['total']<=int(self.limit*.94):return
            target=int(self.limit*.90)
            self.error=''
            # Old diagnostics are the first disposable category.
            for path,_,_ in sorted(self.files(self.root/'logs'),key=lambda r:r[2]):
                if self.status()['total']<=target:break
                try:
                    active=[]
                    for handler in logging.getLogger().handlers:
                        if getattr(handler,'baseFilename',None) and Path(handler.baseFilename).resolve()==path.resolve():active.append(handler)
                    if active:
                        for handler in active:
                            handler.acquire()
                            try:
                                if handler.stream:handler.stream.flush();handler.stream.close();handler.stream=None
                                try:recycle(path,self.root/'logs')
                                finally:handler.stream=handler._open()
                            finally:handler.release()
                    else:recycle(path,self.root/'logs')
                except OSError:
                    self.error='部分自动数据无法清理，请检查写入权限。'
            for store in (self.cache,self.history,self.formulas):
                if store is self.cache:
                    # An unopened browser profile can be recycled safely. During
                    # use, only disposable browser cache files are attempted.
                    for path,_,_ in sorted(self.files(self.root/'.webview'),key=lambda r:r[2]):
                        if self.status()['total']<=target:break
                        if not before_browser_start and not any(part.lower() in {'cache','code cache','gpucache','cache_data','crashpad'} for part in path.parts):continue
                        try:recycle(path,self.root/'.webview')
                        except OSError:pass
                while self.status()['total']>target:
                    if not store.evict_oldest():break
                store_path=getattr(store,'path',None)
                unavailable=(getattr(store,'disabled',False) is True or getattr(store,'read_failed',False) is True)
                if self.status()['total']>target and unavailable and isinstance(store_path,Path):
                    # Corrupt cache/learning files remain recoverable in the
                    # Recycle Bin rather than blocking the budget indefinitely.
                    for path,_,_ in self.files(self.root/'local-data'):
                        if path.name.startswith(store_path.name) and path.parent==self.root/'local-data':
                            try:recycle(path,self.root/'local-data')
                            except OSError:pass
            if self.status()['total']>self.limit:
                self.error='自动数据仍超出预算，部分文件被占用或无法写入。请关闭应用后清理运行缓存。'
                log.warning('Automatic storage cleanup could not reach its budget.')
            else:
                log.info('Automatic storage cleanup completed.')
