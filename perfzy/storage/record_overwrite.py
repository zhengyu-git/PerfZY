"""Replace a completed recording with rollback and one backup outside history."""
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import re
import shutil
import sys

from .sessions import SUMMARY_NAME, _session_directory, _write_json_atomic, new_session_id


def _slot(root, session_id):
    source = _session_directory(root,session_id)
    data = (Path(root)/"data").resolve()
    parent = (data/"session-edit-backups").resolve()
    slot = (parent/source.name).resolve()
    if parent.parent != data or slot.parent != parent:
        raise ValueError("记录回退备份目录越界")
    return source,slot


@contextmanager
def _lock(slot):
    slot.mkdir(parents=True,exist_ok=True)
    path = slot/"editing.lock"
    if path.resolve().parent != slot:
        raise ValueError("记录保存锁路径越界")
    stream = path.open("a+b")
    locked = False
    try:
        if not stream.seek(0,2):
            stream.write(b"\0");stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            raise BlockingIOError("这条记录正在被另一个保存操作使用，请稍后重试。") from error
        locked = True
        yield
    finally:
        if locked:
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()


def source_revision(source):
    """Ignore only the derived history cache; annotations and assets must match."""
    result = []
    for path in sorted(source.rglob("*")):
        if path.is_file() and path != source/SUMMARY_NAME:
            stat = path.stat()
            result.append((path.relative_to(source).as_posix(),stat.st_size,stat.st_mtime_ns))
    return tuple(result)


def _staging(source, token):
    if not re.fullmatch(r"[A-Za-z0-9_-]+",str(token)):
        raise ValueError("记录保存事务编号无效")
    staging = (source.parent/("_edit_"+token)).resolve()
    if staging.parent != source.parent or staging == source:
        raise ValueError("记录保存临时目录越界")
    return staging


def _clean_staging(source, staging, token):
    checked = _staging(source,token)
    if checked != staging.resolve():
        raise ValueError("记录保存临时目录不匹配")
    if not staging.exists(): return
    owner = json.loads((staging/"edit-owner.json").read_text(encoding="utf-8"))
    if owner != dict(session_id=source.name,token=token):
        raise ValueError("记录保存临时目录归属不匹配")
    shutil.rmtree(staging)


def _recover_locked(source, slot):
    journal = slot/"operation.json"
    if not journal.is_file(): return
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state.get("session_id") != source.name:
        raise ValueError("记录保存恢复信息不匹配")
    token = state["token"]
    staging = _staging(source,token)
    previous,older = slot/"previous",staging/"older-backup"
    if staging.exists():
        owner = json.loads((staging/"edit-owner.json").read_text(encoding="utf-8"))
        if owner != dict(session_id=source.name,token=token):
            raise ValueError("记录保存恢复目录归属不匹配")
    for path,parent in ((previous,slot),(older,staging)):
        if path.exists() and path.resolve().parent != parent.resolve():
            raise ValueError("记录回退备份路径越界")
    if state.get("action") == "undo":
        displaced = staging / "undo-current"
        if not source.exists():
            if not displaced.is_dir() or displaced.resolve().parent != staging:
                raise OSError(f"撤销剪辑中断，未找到完整记录。请检查回退备份：{slot}")
            displaced.rename(source)
    else:
        if not source.exists():
            if not previous.is_dir():
                raise OSError(f"记录保存中断，未找到完整原记录。请检查回退备份：{slot}")
            previous.rename(source)
        if older.exists() and not previous.exists():
            older.rename(previous)
    # The source is now a complete old or committed new directory. A journal
    # left after successful publication never rolls back an already saved edit.
    journal.unlink()
    try:
        _clean_staging(source,staging,token)
    except OSError:
        logging.getLogger(__name__).warning("记录已恢复，临时文件稍后可清理：%s",staging)
    return source/"session.json"


class RecordOverwrite:
    def __init__(self,root,session_id):
        self.source,self.slot = _slot(root,session_id)
        self.token = new_session_id()
        self._guard = None

    def __enter__(self):
        self._guard = _lock(self.slot)
        self._guard.__enter__()
        try:
            _recover_locked(self.source,self.slot)
            if not (self.source/"session.json").is_file():
                raise FileNotFoundError("原采集记录不存在，未执行覆盖。")
            self.revision = source_revision(self.source)
            return self
        except BaseException:
            self._guard.__exit__(*sys.exc_info())
            raise

    def __exit__(self,*args):
        return self._guard.__exit__(*args)

    @property
    def pending(self):
        return (self.slot/"operation.json").exists()

    def publish(self,staging,directory):
        if source_revision(self.source) != self.revision:
            raise ValueError("原记录在编辑期间发生变化，请重新打开记录后再保存。")
        if staging.resolve() != _staging(self.source,self.token) or directory.resolve() != (staging/self.source.name).resolve():
            raise ValueError("记录覆盖的临时目录不匹配")
        previous,older = self.slot/"previous",staging/"older-backup"
        journal = self.slot/"operation.json"
        _write_json_atomic(journal,dict(session_id=self.source.name,token=self.token))
        try:
            if previous.exists(): previous.rename(older)
            self.source.rename(previous)
            directory.rename(self.source)
        except BaseException:
            _recover_locked(self.source,self.slot)
            raise
        # Publication succeeded. A cleanup error must not report a successful
        # replacement as failed; the journal permits cleanup on the next launch.
        try:
            journal.unlink()
        except OSError:
            logging.getLogger(__name__).warning("记录已保存，回退事务将在下次启动时清理。")



def has_overwrite_backup(root, session_id) -> bool:
    try:
        source, slot = _slot(root, session_id)
        previous = slot / "previous"
        return ((source / "session.json").is_file() and previous.resolve().parent == slot
                and (previous / "session.json").is_file() and not (slot / "operation.json").exists())
    except (OSError, ValueError):
        return False


def undo_last_overwrite(root, session_id) -> Path:
    """Restore the last pre-cut snapshot by moving complete directories under the edit lock."""
    source, slot = _slot(root, session_id)
    with _lock(slot):
        _recover_locked(source, slot)
        previous = slot / "previous"
        if previous.resolve().parent != slot or not (source / "session.json").is_file():
            raise ValueError("采集记录或回退路径无效。")
        if not (previous / "session.json").is_file():
            raise FileNotFoundError("这条记录没有可撤销的覆盖剪辑。")

        token = new_session_id()
        staging = _staging(source, token)
        staging.mkdir()
        journal = slot / "operation.json"
        _write_json_atomic(staging / "edit-owner.json", dict(session_id=source.name, token=token))
        try:
            _write_json_atomic(journal, dict(session_id=source.name, token=token, action="undo"))
            source.rename(staging / "undo-current")
            previous.rename(source)
        except BaseException:
            if journal.exists():
                _recover_locked(source, slot)
            else:
                _clean_staging(source, staging, token)
            raise

        try:
            _recover_locked(source, slot)
        except OSError:
            logging.getLogger(__name__).warning("剪辑已撤销，回退事务将在下次启动时清理。")
        return source / "session.json"


def recover_pending_overwrites(root):
    parent = (Path(root)/"data"/"session-edit-backups")
    if not parent.is_dir(): return []
    recovered = []
    for candidate in parent.iterdir():
        if not candidate.is_dir() or not (candidate/"operation.json").is_file(): continue
        source,slot = _slot(root,candidate.name)
        try:
            with _lock(slot):
                path = _recover_locked(source,slot)
                if path is not None: recovered.append(path)
        except BlockingIOError:
            continue  # Another live process still owns this operation.
    return recovered
