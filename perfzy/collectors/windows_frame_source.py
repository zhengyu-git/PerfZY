"""Game-independent Windows frame profiles and verified source candidates.

The full PresentMon profile keeps kernel/display tracking for graphics APIs
without DXGI/D3D9 runtime events. A narrower source is never promoted just
because its process started: it must first produce valid target-process frames.
"""
from __future__ import annotations

import csv
import math
import queue
import subprocess
import threading
import time
import uuid

from .dxgi_etw import DxgiPresentSource
from ..domain.validation import MAX_FRAME_TIME_MS

FULL = 'full_display'
APPLICATION = 'application_present'
DXGI = 'dxgi_present'
PROFILES = (FULL, APPLICATION, DXGI)
PROBE_SECONDS = 6.0
MIN_PROBE_FRAMES = 3


def presentmon_command(executable, session, profile=FULL):
    if profile not in (FULL, APPLICATION):
        raise ValueError('not a PresentMon profile')
    command = [executable, '--output_stdout', '--no_console_stats', '--v2_metrics',
               '--no_track_input', '--set_circular_buffer_size', '32768',
               '--session_name', session, '--stop_existing_session']
    if profile == APPLICATION:
        command.extend(('--no_track_gpu', '--no_track_display'))
    return command


def csv_frame_rows(stream, invalid, message):
    headers = None
    for cells in csv.reader(stream, skipinitialspace=True):
        fields = [str(cell).lstrip('\ufeff').strip() for cell in cells]
        names = [field.casefold() for field in fields]
        if 'application' in names and ('processid' in names or 'process id' in names):
            headers = fields
        elif not headers:
            if any(fields):
                message(','.join(fields)[:400])
        elif not fields or not any(fields):
            continue
        elif len(fields) != len(headers):
            invalid()
        else:
            yield dict(zip(headers, fields))


def valid_target_frame(row, pids):
    normalized = {str(key).strip().casefold(): value for key, value in row.items()}
    try:
        pid = int(float(normalized.get('processid') or normalized.get('process id') or 0))
        duration = float(normalized.get('frametime') or normalized.get('msbetweenpresents') or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    frame_type = str(normalized.get('frametype') or '').strip().casefold()
    return (pid in pids and math.isfinite(duration) and 0 < duration <= MAX_FRAME_TIME_MS
            and frame_type not in {'intel xess-fg', 'amd afmf', 'repeated'})


def frame_event_time(row, started_at):
    normalized = {str(key).strip().casefold(): value for key, value in row.items()}
    for key, scale in (('cpustarttime', .001), ('timeinseconds', 1.0)):
        try:
            stamp = float(normalized.get(key, 'nan'))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(stamp):
            return started_at + stamp * scale
    return None


class FrameCandidate:
    """One owned source, bounded startup queue, and target-based readiness."""

    def __init__(self, profile, executable, pids, accepting=lambda: True):
        if profile not in PROFILES:
            raise ValueError('unknown Windows frame profile')
        self.profile, self.executable, self.pids = profile, executable, pids
        self.accepting = accepting
        self.accept_after = 0.0
        self.probe_started_at = 0.0
        self.session = 'PerfZY-Candidate-' + uuid.uuid4().hex[:12]
        self.started_at = 0.0
        self.process = None
        self.native = None
        self.rows = queue.Queue(maxsize=1024)
        self.csv_rows = self.parse_errors = self.valid_rows = self.buffer_dropped = 0
        self.stderr = []
        self.messages = []
        self._stopping = False
        self._workers = []
        self._last_valid_at = 0.0

    def start(self):
        self.started_at = time.monotonic()
        self.probe_started_at = self.started_at
        if self.profile == DXGI:
            self.native = DxgiPresentSource(self._accept, self.pids)
            self.native.start()
            self.started_at, self.session = self.native.started_at, self.native.session
        else:
            self.process = subprocess.Popen(presentmon_command(self.executable, self.session, self.profile),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding='utf-8', errors='replace', bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
            if self.process.stdout:
                self._workers.append(threading.Thread(target=self._read, daemon=True, name='PerfZY-FrameCandidate'))
            if self.process.stderr:
                self._workers.append(threading.Thread(target=self._read_errors, daemon=True, name='PerfZY-FrameCandidate-Errors'))
            for worker in self._workers:
                worker.start()

    def _invalid(self):
        self.parse_errors += 1

    def _message(self, text):
        self.messages.append(text)
        del self.messages[:-8]

    def _read(self):
        try:
            for row in csv_frame_rows(self.process.stdout, self._invalid, self._message):
                if self._stopping:
                    return
                self.csv_rows += 1
                self._accept(row)
        except (OSError, ValueError, csv.Error) as error:
            if not self._stopping:
                self._message(str(error)[:400])

    def _read_errors(self):
        try:
            for line in self.process.stderr:
                if self._stopping:
                    return
                self.stderr.append(line.strip()[:400])
                del self.stderr[:-8]
        except (OSError, ValueError):
            return

    def _accept(self, row):
        if self._stopping or not self.accepting() or not valid_target_frame(row, self.pids()):
            return
        event_time = frame_event_time(row, self.started_at)
        if self.accept_after and (event_time is None or event_time < self.accept_after):
            return
        self.valid_rows += 1
        self._last_valid_at = time.monotonic()
        try:
            self.rows.put_nowait(row)
        except queue.Full:
            try:
                self.rows.get_nowait()
                self.buffer_dropped += 1
                self.rows.put_nowait(row)
            except (queue.Empty, queue.Full):
                pass

    @property
    def ready(self):
        if not self.accepting() or not self.running or time.monotonic() - self._last_valid_at > 2:
            return False
        pids = self.pids()
        with self.rows.mutex:
            buffered = tuple(self.rows.queue)
        identities = set()
        for row in buffered:
            if not valid_target_frame(row, pids):
                continue
            normalized = {str(key).strip().casefold(): value for key, value in row.items()}
            try:
                stamp = float(normalized.get('cpustarttime', normalized.get('timeinseconds', 'nan')))
                pid = int(float(normalized.get('processid') or normalized.get('process id') or 0))
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(stamp):
                identities.add((pid, str(normalized.get('swapchainaddress', '')).casefold(), stamp))
        return len(identities) >= MIN_PROBE_FRAMES

    def reset_boundary(self, boundary):
        self.accept_after = boundary
        self.probe_started_at = time.monotonic()
        self._last_valid_at = 0.0
        with self.rows.mutex:
            self.rows.queue.clear()

    @property
    def running(self):
        if self._stopping:
            return False
        if self.native is not None:
            return bool(self.native.diagnostics().get('running'))
        return self.process is not None and self.process.poll() is None

    def take_rows(self):
        for _ in range(self.rows.qsize()):
            try:
                row = self.rows.get_nowait()
                if valid_target_frame(row, self.pids()):
                    yield row
            except queue.Empty:
                break

    def diagnostics(self):
        native = self.native.diagnostics() if self.native is not None else {}
        return {'profile': self.profile, 'session': self.session, 'running': self.running,
                'csv_rows': self.csv_rows, 'csv_parse_errors': self.parse_errors,
                'valid_target_rows': self.valid_rows, 'buffer_dropped': self.buffer_dropped,
                'stderr': list(self.stderr), 'startup_messages': list(self.messages),
                'native': native}

    def stop(self):
        self._stopping = True
        if self.native is not None:
            self.native.stop()
        process = self.process
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError) as error:
                self._message('source cleanup: ' + str(error)[:350])
            finally:
                pid = getattr(process, 'pid', None)
                if isinstance(pid, int) and pid > 0:
                    try:
                        subprocess.run(['logman', 'stop', self.session, '-ets'], capture_output=True,
                                       timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
                    except (OSError, subprocess.SubprocessError) as error:
                        self._message('owned ETW cleanup: ' + str(error)[:350])
        for worker in self._workers:
            if worker is not threading.current_thread():
                worker.join(timeout=.2)
