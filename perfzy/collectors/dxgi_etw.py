"""Read successful DXGI Present calls when PresentMon's analytic path is silent.

This is application presentation timing, never displayed FPS or GPU timing.
Only the logging Present start/stop pair is enabled, in our own ETW session.
No DLL is injected and no existing tracing session is stopped.
"""
from __future__ import annotations

import ctypes as C
import os
import struct
import threading
import time
import uuid

U8, U16, U32, U64, I64 = C.c_uint8, C.c_uint16, C.c_uint32, C.c_uint64, C.c_int64
DXGI_GUID = uuid.UUID('ca11c036-0102-4a2d-a6ad-f03cfed5d3c9').bytes_le
PRESENT_START, PRESENT_STOP = 178, 179


class PresentPairs:
    """Pair calls by PID/thread, then time consecutive successful swapchain calls."""

    def __init__(self, frequency: int, origin: int, emit):
        if frequency <= 0:
            raise ValueError('QPC frequency must be positive')
        self.frequency, self.origin, self.emit = frequency, origin, emit
        self.pending = {}
        self.previous = {}
        self.paired = self.rejected = self.unmatched = 0

    def reset_intervals(self):
        self.pending.clear()
        self.previous.clear()

    def accept(self, event_id, pid, tid, stamp, payload, pointer_size=8):
        thread = (pid, tid)
        if event_id == PRESENT_START:
            expected = pointer_size + 8
            if len(payload) != expected:
                old = self.pending.pop(thread, None)
                if old is not None:
                    self.previous.pop((pid, old[0]), None)
                self.rejected += 1
                return
            address, _sync, flags = struct.unpack('<QII' if pointer_size == 8 else '<III', payload)
            if thread in self.pending:
                old = self.pending[thread]
                self.previous.pop((pid, old[0]), None)
                self.unmatched += 1
            if len(self.pending) >= 256 or len(self.previous) >= 256:
                self.reset_intervals()
            self.pending[thread] = (address, stamp, flags)
        elif event_id == PRESENT_STOP:
            start = self.pending.pop(thread, None)
            if start is None:
                self.unmatched += 1
                return
            address, begin, flags = start
            stream = (pid, address)
            # S_OK only: tests, occluded/minimized presents, and failed calls
            # must not be turned into a frame, a zero FPS, or a long-frame spike.
            if len(payload) != 4 or struct.unpack('<I', payload)[0] != 0 or flags & 1 or stamp < begin:
                self.previous.pop(stream, None)
                self.rejected += 1
                return
            self.paired += 1
            last = self.previous.get(stream)
            if last is not None and begin <= last:
                self.rejected += 1
                return
            self.previous[stream] = begin
            if last is None:
                return
            frame_ms = (begin - last) * 1000.0 / self.frequency
            self.emit({'Application': 'DXGI', 'ProcessID': str(pid),
                       'SwapChainAddress': hex(address), 'FrameTime': str(frame_ms),
                       'CPUStartTime': str((begin - self.origin) * 1000.0 / self.frequency)})


class _Guid(C.Structure):
    _fields_ = [('data', U8 * 16)]


class _Wnode(C.Structure):
    _fields_ = [('BufferSize', U32), ('ProviderId', U32), ('History', U64),
                ('TimeStamp', I64), ('Guid', _Guid), ('ClientContext', U32), ('Flags', U32)]


class _Properties(C.Structure):
    _fields_ = [('Wnode', _Wnode)] + [(name, U32) for name in (
        'BufferSize', 'MinimumBuffers', 'MaximumBuffers', 'MaximumFileSize',
        'LogFileMode', 'FlushTimer', 'EnableFlags', 'AgeLimit', 'NumberOfBuffers',
        'FreeBuffers', 'EventsLost', 'BuffersWritten', 'LogBuffersLost', 'RealTimeBuffersLost')
    ] + [('LoggerThreadId', C.c_void_p), ('LogFileNameOffset', U32), ('LoggerNameOffset', U32)]


class _Descriptor(C.Structure):
    _fields_ = [('Id', U16), ('Version', U8), ('Channel', U8), ('Level', U8),
                ('Opcode', U8), ('Task', U16), ('Keyword', U64)]


class _Header(C.Structure):
    _fields_ = [('Size', U16), ('HeaderType', U16), ('Flags', U16), ('EventProperty', U16),
                ('ThreadId', U32), ('ProcessId', U32), ('TimeStamp', I64), ('ProviderId', _Guid),
                ('Descriptor', _Descriptor), ('ProcessorTime', U64), ('ActivityId', _Guid)]


class _Record(C.Structure):
    _fields_ = [('Header', _Header), ('BufferContext', U32), ('ExtendedDataCount', U16),
                ('UserDataLength', U16), ('ExtendedData', C.c_void_p),
                ('UserData', C.c_void_p), ('UserContext', C.c_void_p)]


class _LegacyHeader(C.Structure):
    _fields_ = [('Size', U16), ('Flags', U16), ('Version', U32), ('ThreadId', U32),
                ('ProcessId', U32), ('TimeStamp', I64), ('Guid', _Guid), ('ProcessorTime', U64)]


class _LegacyEvent(C.Structure):
    _fields_ = [('Header', _LegacyHeader), ('InstanceId', U32), ('ParentInstanceId', U32),
                ('ParentGuid', _Guid), ('MofData', C.c_void_p), ('MofLength', U32), ('Context', U32)]


class _SystemTime(C.Structure):
    _fields_ = [(name, U16) for name in ('year', 'month', 'dayOfWeek', 'day', 'hour', 'minute', 'second', 'ms')]


class _TimeZone(C.Structure):
    _fields_ = [('Bias', U32), ('StandardName', C.c_wchar * 32), ('StandardDate', _SystemTime),
                ('StandardBias', U32), ('DaylightName', C.c_wchar * 32), ('DaylightDate', _SystemTime),
                ('DaylightBias', U32)]


class _LogHeader(C.Structure):
    _fields_ = [(name, U32) for name in ('BufferSize', 'Version', 'ProviderVersion', 'Processors')
    ] + [('EndTime', I64)] + [(name, U32) for name in ('TimerResolution', 'MaximumFileSize', 'LogFileMode', 'BuffersWritten')
    ] + [('InstanceGuid', _Guid), ('LoggerName', C.c_wchar_p), ('LogFileName', C.c_wchar_p),
         ('TimeZone', _TimeZone), ('BootTime', I64), ('PerfFreq', I64), ('StartTime', I64),
         ('ReservedFlags', U32), ('BuffersLost', U32)]


_Callback = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)(None, C.POINTER(_Record))


class _LogFile(C.Structure):
    _fields_ = [('LogFileName', C.c_wchar_p), ('LoggerName', C.c_wchar_p), ('CurrentTime', I64),
                ('BuffersRead', U32), ('ProcessTraceMode', U32), ('CurrentEvent', _LegacyEvent),
                ('LogfileHeader', _LogHeader), ('BufferCallback', C.c_void_p), ('BufferSize', U32),
                ('Filled', U32), ('EventsLost', U32), ('EventRecordCallback', _Callback),
                ('IsKernelTrace', U32), ('Context', C.c_void_p)]


class _Filter(C.Structure):
    _fields_ = [('Ptr', U64), ('Size', U32), ('Type', U32)]


class _Enable(C.Structure):
    _fields_ = [('Version', U32), ('EnableProperty', U32), ('ControlFlags', U32), ('SourceId', _Guid),
                ('EnableFilterDesc', C.POINTER(_Filter)), ('FilterDescCount', U32)]


class DxgiPresentSource:
    """Small, bounded real-time ETW consumer with explicit ownership/teardown."""

    def __init__(self, emit, pids):
        if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
            raise OSError('DXGI 兼容采集需要 64 位 Windows')
        assert (C.sizeof(_Properties), C.sizeof(_Record), C.sizeof(_LogFile)) == (120, 112, 448)
        self._api = C.WinDLL('advapi32', use_last_error=True)
        signatures = {
            'StartTraceW': ([C.POINTER(U64), C.c_wchar_p, C.POINTER(_Properties)], U32),
            'ControlTraceW': ([U64, C.c_wchar_p, C.POINTER(_Properties), U32], U32),
            'EnableTraceEx2': ([U64, C.POINTER(_Guid), U32, U8, U64, U64, U32, C.POINTER(_Enable)], U32),
            'OpenTraceW': ([C.POINTER(_LogFile)], U64),
            'ProcessTrace': ([C.POINTER(U64), U32, C.c_void_p, C.c_void_p], U32),
            'CloseTrace': ([U64], U32),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._api, name)
            function.argtypes, function.restype = arguments, result
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.QueryPerformanceCounter.argtypes = [C.POINTER(I64)]
        kernel.QueryPerformanceFrequency.argtypes = [C.POINTER(I64)]
        frequency, origin = I64(), I64()
        if not kernel.QueryPerformanceFrequency(C.byref(frequency)) or not kernel.QueryPerformanceCounter(C.byref(origin)):
            raise OSError('无法读取 Windows 高精度计时器')
        self.started_at = time.monotonic()
        self._pairs = PresentPairs(frequency.value, origin.value, emit)
        self._pids = pids
        self.session = 'PerfZY-DXGI-' + uuid.uuid4().hex[:12]
        self._trace = U64()
        self._consumer = None
        self._thread = None
        self._stopping = False
        self._control_lock = threading.Lock()
        self._stop_code = self._close_code = None
        self._properties_buffer = C.create_string_buffer(C.sizeof(_Properties) + 2048)
        self._properties = C.cast(self._properties_buffer, C.POINTER(_Properties))
        properties = self._properties.contents
        properties.Wnode.BufferSize = C.sizeof(self._properties_buffer)
        properties.Wnode.Flags = 0x20000  # WNODE_FLAG_TRACED_GUID
        properties.Wnode.ClientContext = 1  # QPC, also used by Python's monotonic clock
        properties.BufferSize = 64  # KB per ETW buffer, at most 8 MB total
        properties.MinimumBuffers, properties.MaximumBuffers = 64, 128
        properties.LogFileMode, properties.FlushTimer = 0x100, 1  # real-time, one-second flush
        properties.LoggerNameOffset = C.sizeof(_Properties)
        self._guid = _Guid((U8 * 16).from_buffer_copy(DXGI_GUID))
        self._callback = _Callback(self._on_event)
        self._log = _LogFile()
        self._log.LoggerName = self.session
        self._log.ProcessTraceMode = 0x10001100  # EVENT_RECORD | REAL_TIME | RAW_TIMESTAMP
        self._log.EventRecordCallback = self._callback
        self._events = self._lost = self._callback_errors = 0
        self.error = ''

    def start(self):
        try:
            self._check(self._api.StartTraceW(C.byref(self._trace), self.session, self._properties), '启动 DXGI ETW')
            consumer = self._api.OpenTraceW(C.byref(self._log))
            if consumer == 0xffffffffffffffff:
                raise OSError(C.get_last_error(), '打开 DXGI ETW 失败')
            self._consumer = U64(consumer)
            # Filter at the provider so other DXGI API calls do not enter Python.
            event_ids = struct.pack('<BBHHH', 1, 0, 2, PRESENT_START, PRESENT_STOP)
            self._event_filter = C.create_string_buffer(event_ids)
            descriptor = _Filter(C.addressof(self._event_filter), len(event_ids), 0x80000200)
            parameters = _Enable(Version=2, EnableFilterDesc=C.pointer(descriptor), FilterDescCount=1)
            self._check(self._api.EnableTraceEx2(self._trace, C.byref(self._guid), 1, 5,
                0x4000000000000000, 0, 0, C.byref(parameters)), '订阅 DXGI Present')
            self._thread = threading.Thread(target=self._consume, args=(self._consumer,), name='PerfZY-DXGI-Presents', daemon=True)
            self._thread.start()
        except BaseException:
            self.stop()
            raise

    @staticmethod
    def _check(code, operation):
        if code:
            raise OSError(code, f'{operation}失败（Windows 错误 {code}）')

    def _consume(self, consumer):
        code = self._api.ProcessTrace(C.byref(consumer), 1, None, None)
        if code and not self._stopping:
            self.error = f'DXGI 兼容帧源已停止（Windows 错误 {code}）'

    def _on_event(self, pointer):
        try:
            record = pointer.contents
            header = record.Header
            if self._stopping or bytes(header.ProviderId.data) != DXGI_GUID:
                return
            # This pair's version/payload were verified against the Windows
            # provider manifest. Unknown schemas remain unavailable.
            if header.Descriptor.Version != 0 or header.Descriptor.Id not in (PRESENT_START, PRESENT_STOP):
                return
            if header.ProcessId not in self._pids():
                return
            self._events += 1
            if record.UserDataLength > 32 or not record.UserData:
                self._pairs.rejected += 1
                return
            self._pairs.accept(header.Descriptor.Id, header.ProcessId, header.ThreadId, header.TimeStamp,
                C.string_at(record.UserData, record.UserDataLength), 4 if header.Flags & 0x20 else 8)
        except Exception:
            # A callback must never let a Python exception cross the Win32 ABI.
            self._callback_errors += 1

    def diagnostics(self):
        with self._control_lock:
            if self._trace.value and not self._stopping:
                code = self._api.ControlTraceW(self._trace, self.session, self._properties, 0)
                if code == 0:
                    properties = self._properties.contents
                    lost = int(properties.EventsLost + properties.RealTimeBuffersLost)
                    if lost > self._lost:
                        self._pairs.reset_intervals()
                    self._lost = max(lost, self._lost)
        return {'session': self.session, 'events': self._events, 'paired_presents': self._pairs.paired,
                'rejected_presents': self._pairs.rejected, 'unmatched_events': self._pairs.unmatched,
                'callback_errors': self._callback_errors, 'events_or_buffers_lost': self._lost,
                'running': bool(self._thread and self._thread.is_alive()), 'status': self.error,
                'stop_code': self._stop_code, 'close_code': self._close_code,
                'frame_type': 'not identified; generated/repeated frames cannot be classified',
                'definition': 'successful DXGI Present start-to-start interval; not displayed FPS'}

    def stop(self):
        with self._control_lock:
            self._stopping = True
            if self._trace.value:
                self._stop_code = int(self._api.ControlTraceW(self._trace, self.session, self._properties, 1))
                if self._stop_code in (0, 4201):
                    self._trace.value = 0
                else:
                    self.error = f'DXGI 采集通道关闭失败（Windows 错误 {self._stop_code}）'
            if self._consumer is not None:
                self._close_code = int(self._api.CloseTrace(self._consumer))
                self._consumer = None
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
