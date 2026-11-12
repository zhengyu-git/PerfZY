from __future__ import annotations

from collections import deque
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import threading

from ..domain.validation import MAX_FRAME_TIME_MS

DEFAULT_OPTIONS = dict(enabled=True, cooldown=30., max_events=50, context=5.,
                       hold=2., fps_below=30., frame_above=50., cpu_above=90.,
                       memory_jump=100., interval=1.)
MAX_FRAMES = 2_000_000
MAX_FRAME_BYTES = 128 * 1024 * 1024


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def read_json(path, default=None):
    fallback = {} if default is None else default
    try:
        path = Path(path)
        if path.stat().st_size > 4 * 1024 * 1024:
            return fallback
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,type(fallback)) else fallback
    except (OSError, ValueError):
        return fallback


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def evidence_options(root):
    stored = read_json(Path(root) / 'data' / 'studio' / 'evidence-options.json')
    result = dict(DEFAULT_OPTIONS)
    for key, lower, upper in (('cooldown', 5, 3600), ('max_events', 1, 200),
            ('context', 1, 30), ('hold', 0, 60), ('fps_below', 1, 1000),
            ('frame_above', 1, 5000), ('cpu_above', 1, 100), ('memory_jump', 1, 100000),
            ('interval', .5, 10)):
        value = number(stored.get(key)) if isinstance(stored, dict) else None
        if value is not None and lower <= value <= upper:
            result[key] = int(value) if key == 'max_events' else value
    if isinstance(stored, dict) and isinstance(stored.get('enabled'), bool):
        result['enabled'] = stored['enabled']
    return result


class EvidenceMonitor:
    """Bounded, gap-aware threshold evidence; does not diagnose a root cause."""
    def __init__(self, options=None):
        self.options = dict(DEFAULT_OPTIONS, **(options or {}))
        self.events, self.active = [], {}
        self.recent = deque(maxlen=256)
        self.last_time, self.last_trigger = None, -math.inf

    def observe(self, elapsed, values):
        t = number(elapsed)
        if t is None or t < 0 or (self.last_time is not None and t <= self.last_time):
            return
        if self.last_time is not None and t - self.last_time > self.options['interval'] * 3:
            self.active.clear()
            self.recent.clear()
        self.last_time = t
        valid = {key: number(value) for key, value in values.items()}
        self.recent.append((t, valid))
        if not self.options['enabled']:
            return
        rules = [('fps', '低 FPS', self.options['fps_below'], lambda a, b: a < b),
                 ('frame_time', '高帧耗时', self.options['frame_above'], lambda a, b: a > b),
                 ('app_cpu', '持续高 CPU', self.options['cpu_above'], lambda a, b: a > b)]
        hits = []
        for key, label, threshold, condition in rules:
            value = valid.get(key)
            if value is not None and condition(value, threshold):
                start = self.active.setdefault(key, t)
                if t - start >= self.options['hold']:
                    hits.append(dict(metric=key, reason=label, value=value, threshold=threshold))
            else:
                self.active.pop(key, None)
        for key in ('memory', 'ios_physical_footprint', 'private_memory'):
            value = valid.get(key)
            previous = next(((stamp, row.get(key)) for stamp, row in self.recent
                             if 0 < t - stamp <= 10 and row.get(key) is not None), None)
            if value is not None and previous and value - previous[1] >= self.options['memory_jump']:
                hits.append(dict(metric=key, reason='短时内存增长', value=value,
                                 delta=value-previous[1], threshold=self.options['memory_jump']))
        if hits and len(self.events) < self.options['max_events'] and t - self.last_trigger >= self.options['cooldown']:
            self.events.append(dict(id=len(self.events)+1, time=t,
                start=max(0., t-self.options['context']), end=t+self.options['context'],
                reasons=hits, note='', semantics='threshold_evidence_not_root_cause'))
            self.last_trigger = t


class RecordExtras:
    """Bounded raw-frame SQLite sidecar and lightweight online evidence index."""
    def __init__(self, root, directory):
        self.directory = Path(directory)
        self.monitor = EvidenceMonitor(evidence_options(root))
        self.connection = None
        self.frame_count = self.frames_dropped = 0
        self.frame_bytes = self._last_event_count = 0
        self.lock = threading.RLock()
        self.ticks = 0

    def append(self, elapsed, values, frames=()):
        with self.lock:
            self.monitor.observe(elapsed, values)
            accepted = []
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                x, duration = number(frame.get('x')), number(frame.get('frame_ms'))
                if x is None or duration is None or x < 0 or not 0 < duration <= MAX_FRAME_TIME_MS:
                    continue
                if self.frame_count + len(accepted) >= MAX_FRAMES:
                    self.frames_dropped += 1
                    continue
                clean = {key: str(frame.get(key) or '')[:100] for key in ('source','swapchain','clock')}
                clean.update(pid=number(frame.get('pid')),source_time_s=number(frame.get('source_time_s')))
                clean.update(x=x, frame_ms=duration,
                    cpu_busy=number(frame.get('cpu_busy')), gpu_busy=number(frame.get('gpu_busy')),
                    dropped=bool(frame['dropped']) if frame.get('dropped') is not None else None)
                for key, limit in (('jank_contract', 80), ('layer', 500), ('scope', 200),
                                   ('stream_epoch', 100), ('source_session_id', 100)):
                    if key in frame:
                        clean[key] = str(frame[key])[:limit]
                for key in ('jank_valid', 'jank', 'big_jank'):
                    if key in frame:
                        clean[key] = frame[key] if isinstance(frame[key], bool) else None
                if 'target_pid' in frame:
                    clean['target_pid'] = number(frame['target_pid'])
                encoded=json.dumps(clean,ensure_ascii=False,allow_nan=False)
                cost=len(encoded.encode('utf-8'))+192
                if self.frame_bytes+cost > MAX_FRAME_BYTES:
                    self.frames_dropped+=1
                    continue
                self.frame_bytes+=cost
                accepted.append((x,duration,encoded))
            if accepted:
                self.directory.mkdir(parents=True, exist_ok=True)
                if self.connection is None:
                    self.connection = sqlite3.connect(self.directory / 'frames.sqlite3', check_same_thread=False)
                    self.connection.execute('CREATE TABLE IF NOT EXISTS frames (seq INTEGER PRIMARY KEY, x REAL NOT NULL, ms REAL NOT NULL, payload TEXT NOT NULL)')
                    self.connection.execute('CREATE INDEX IF NOT EXISTS frames_time ON frames(x)')
                    self.connection.execute('CREATE INDEX IF NOT EXISTS frames_slowest ON frames(ms DESC,x)')
                self.connection.executemany('INSERT INTO frames(x,ms,payload) VALUES (?,?,?)', accepted)
                self.connection.commit()
                self.frame_count += len(accepted)
            self.ticks += 1
            if self.ticks % 5 == 0 or len(self.monitor.events)!=self._last_event_count:
                self.flush()
                self._last_event_count=len(self.monitor.events)

    def flush(self):
        with self.lock:
            if self.monitor.events:
                write_json(self.directory / 'evidence.json', self.monitor.events)
            if self.frame_count or self.frames_dropped:
                write_json(self.directory / 'frame-retention.json', dict(count=self.frame_count,
                    dropped_by_limit=self.frames_dropped, limit=MAX_FRAMES,
                    byte_budget=MAX_FRAME_BYTES,estimated_bytes=self.frame_bytes,
                    missing_frames_not_reconstructed=True))

    def close(self):
        with self.lock:
            self.flush()
            if self.connection is not None:
                self.connection.close()
                self.connection = None


def frame_window(directory, start, end, limit=2000, center=None):
    path = Path(directory) / 'frames.sqlite3'
    if not path.is_file():
        return dict(rows=[], total=0, truncated=False, reason='该记录没有真实逐帧数据；采样曲线不会转换成逐帧记录。')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        count = connection.execute('SELECT COUNT(*) FROM frames WHERE x>=? AND x<=?', (start, end)).fetchone()[0]
        maximum=max(1,min(int(limit),10000))
        if number(center) is not None and start<=center<=end:
            rows=connection.execute('SELECT payload FROM frames WHERE x>=? AND x<=? ORDER BY ABS(x-?),x,seq LIMIT ?',
                                    (start,end,center,maximum)).fetchall()
        else:
            rows=connection.execute('SELECT payload FROM frames WHERE x>=? AND x<=? ORDER BY x,seq LIMIT ?',
                                    (start,end,maximum)).fetchall()
    decoded=sorted((json.loads(row[0]) for row in rows),key=lambda row:row['x'])
    return dict(rows=decoded, total=count,
                truncated=count > len(rows), reason='' if count else '所选区间未留存逐帧数据。')


def slow_frames(directory, threshold=50, limit=200):
    path = Path(directory) / 'frames.sqlite3'
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        rows = connection.execute('SELECT payload FROM frames WHERE ms>=? ORDER BY ms DESC,x LIMIT ?',
                                  (threshold, min(500, max(1, limit)))).fetchall()
    return [json.loads(row[0]) for row in rows]
