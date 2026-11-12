"""Identity-checked Windows process-tree resources, not frame accounting.

Discovery and a sample are not atomic OS operations. An incomplete known tree
therefore produces missing aggregate metrics rather than an understated sum.
No-error discovery is still not proof that every short-lived child was seen.
"""
from __future__ import annotations

import math

import psutil


CPU_KEYS = frozenset({'app_cpu', 'app_user_cpu', 'app_kernel_cpu'})
IO_FIELDS = {'disk_read': ('read_bytes', 1024.), 'disk_write': ('write_bytes', 1024.),
             'disk_read_iops': ('read_count', 1.), 'disk_write_iops': ('write_count', 1.)}
MEMORY_FIELDS = {'memory': 'rss', 'private_memory': 'private', 'virtual_memory': 'vms'}
PROCESS_KEYS = CPU_KEYS | frozenset(IO_FIELDS) | frozenset(MEMORY_FIELDS) | {
    'page_faults', 'thread_count', 'handle_count'}


def _number(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid process counter')
    return value


class ProcessTreeResources:
    def __init__(self, cpu_count):
        self.cpu_count = max(1, cpu_count)
        self._states = {}
        self._enabled = frozenset()
        self.last = {}
        self.identities = {}
        self.raw_children = ()

    def reset(self):
        self._states.clear()
        self._enabled = frozenset()
        self.last = {}
        self.identities = {}
        self.raw_children = ()

    def configure(self, enabled):
        """A disabled interval must not become a large delta after re-enable."""
        for state in self._states.values():
            if not (CPU_KEYS & enabled):
                state.pop('cpu', None)
            if not (set(IO_FIELDS) & enabled):
                state.pop('io', None)
            if 'page_faults' not in enabled:
                state.pop('faults', None)
        self._enabled = frozenset(enabled)

    def _resolve(self, root, children=None):
        processes, parents, identities, failed = {}, {}, {}, set()
        candidates = {int(root.pid)}
        discovery_failed = False
        try:
            self.raw_children = tuple(root.children(recursive=True) if children is None else children)
        except (psutil.Error, OSError):
            self.raw_children = ()
            discovery_failed = True
        for process in (root, *self.raw_children):
            try:
                pid = int(process.pid)
                if pid <= 0:
                    continue
                candidates.add(pid)
                created = _number(process.create_time())
                if not process.is_running():
                    raise ValueError('process exited or PID reused')
                parent = int(process.ppid()) if pid != root.pid else None
                processes[pid], parents[pid], identities[pid] = process, parent, created
            except (psutil.Error, OSError, ValueError, TypeError, AttributeError):
                if getattr(process, 'pid', None) is not None:
                    failed.add(int(process.pid))
        # A recursive discovery list is not itself a permanent authorization:
        # revalidate a complete current parent chain back to the selected root.
        verified = {int(root.pid)} if int(root.pid) in processes else set()
        pending = set(processes) - verified
        for _ in range(len(pending)):
            accepted = {pid for pid in pending if parents[pid] in verified
                        and identities[pid] >= identities[parents[pid]]}
            if not accepted:
                break
            verified.update(accepted)
            pending.difference_update(accepted)
        failed.update(pending)
        return ({pid: processes[pid] for pid in verified},
                {pid: identities[pid] for pid in verified},
                {pid: parents[pid] for pid in verified}, candidates, failed, discovery_failed)

    def sample(self, root, now, enabled, children=None):
        processes, identities, parents, candidates, failed, discovery_failed = self._resolve(root, children)
        enabled = frozenset(enabled)
        requested = enabled & PROCESS_KEYS
        per_metric = {key: {} for key in requested}
        warming = {key: set() for key in requested}
        next_states = {}
        errors = {}
        for pid, process in processes.items():
            state = dict(self._states.get((pid, identities[pid]), {}))
            local = {}
            def problem(family, error):
                errors.setdefault(pid, []).append(family + ': ' + type(error).__name__)
            if CPU_KEYS & requested:
                try:
                    cpu = process.cpu_times()
                    current = (_number(cpu.user), _number(cpu.system))
                    old = state.get('cpu') if CPU_KEYS & self._enabled else None
                    state['cpu'] = (current, now)
                    if old and now > old[1] and all(a >= b for a, b in zip(current, old[0])):
                        delta = (now - old[1]) * self.cpu_count
                        user, kernel = ((a-b) / delta * 100. for a, b in zip(current, old[0]))
                        for key, value in [('app_user_cpu', user), ('app_kernel_cpu', kernel),
                                           ('app_cpu', user+kernel)]:
                            if key in requested:
                                local[key] = min(100., value)
                    else:
                        for key in requested & CPU_KEYS:
                            warming[key].add(pid)
                except (psutil.Error, OSError, AttributeError, TypeError, ValueError) as error:
                    state.pop('cpu', None); problem('CPU', error)
            memory = None
            if (set(MEMORY_FIELDS) | {'page_faults'}) & requested:
                try:
                    memory = process.memory_info()
                except (psutil.Error, OSError, AttributeError, TypeError, ValueError) as error:
                    state.pop('faults', None); problem('memory', error)
            if memory is not None:
                for key, field in MEMORY_FIELDS.items():
                    if key not in requested:
                        continue
                    try:
                        local[key] = _number(getattr(memory, field)) / 1048576.
                    except (AttributeError, TypeError, ValueError) as error:
                        problem(key, error)
                if 'page_faults' in requested:
                    try:
                        faults = _number(memory.num_page_faults)
                        old = state.get('faults') if 'page_faults' in self._enabled else None
                        state['faults'] = (faults, now)
                        if old and now > old[1] and faults >= old[0]:
                            local['page_faults'] = (faults-old[0]) / (now-old[1])
                        else:
                            warming['page_faults'].add(pid)
                    except (AttributeError, TypeError, ValueError) as error:
                        state.pop('faults', None); problem('page_faults', error)
            for key, method in [('thread_count', 'num_threads'), ('handle_count', 'num_handles')]:
                if key in requested:
                    try:
                        local[key] = _number(getattr(process, method)())
                    except (psutil.Error, OSError, AttributeError, TypeError, ValueError) as error:
                        problem(key, error)
            if set(IO_FIELDS) & requested:
                try:
                    io = process.io_counters()
                    current = {key: _number(getattr(io, field)) for key, (field, _) in IO_FIELDS.items() if key in requested}
                    old = state.get('io') if set(IO_FIELDS) & self._enabled else None
                    state['io'] = (current, now)
                    for key, value in current.items():
                        if old and key in old[0] and now > old[1] and value >= old[0][key]:
                            local[key] = (value-old[0][key]) / IO_FIELDS[key][1] / (now-old[1])
                        else:
                            warming[key].add(pid)
                except (psutil.Error, OSError, AttributeError, TypeError, ValueError) as error:
                    state.pop('io', None); problem('I/O', error)
            # Discard all values if the same PID died or was reused during reads.
            try:
                valid = (process.is_running() and process.create_time() == identities[pid]
                         and (pid == root.pid or process.ppid() == parents[pid]))
            except (psutil.Error, OSError, AttributeError, TypeError, ValueError):
                valid = False
            if not valid:
                failed.add(pid)
                continue
            next_states[(pid, identities[pid])] = state
            for key, value in local.items():
                per_metric[key][pid] = value
        self._states, self._enabled = next_states, enabled
        surviving = {pid for pid, _created in next_states}
        # If a parent died during reads, descendants must not be authorized by
        # an identity which did not survive this sample.
        for pid in tuple(surviving):
            current = pid
            seen = set()
            while current != root.pid:
                if current not in surviving or current in seen:
                    failed.add(pid)
                    break
                seen.add(current)
                current = parents[current]
            if root.pid not in surviving:
                failed.add(pid)
            if pid in failed:
                for received in per_metric.values():
                    received.pop(pid, None)
        surviving.difference_update(failed)
        self.identities = {pid: identities[pid] for pid in surviving}
        self._states = {key: state for key, state in next_states.items() if key[0] in surviving}
        coverage, values = {}, {}
        for key, received in per_metric.items():
            missing = candidates - set(received)
            complete = not discovery_failed and not missing
            coverage[key] = {'received_pids': sorted(received), 'missing_pids': sorted(missing),
                             'warming_pids': sorted(warming[key]), 'complete': complete}
            if complete:
                total = sum(received.values())
                values[key] = min(100., total) if key in CPU_KEYS else total
        self.last = {'resource_pids': sorted(surviving), 'resource_candidate_pids': sorted(candidates),
                     'resource_failed_pids': sorted(failed | set(errors)),
                     'resource_discovery_failed': discovery_failed,
                     'resource_metric_coverage': coverage,
                     'resource_errors': {str(pid): items for pid, items in errors.items()},
                     'resource_completeness': 'not_proven',
                     'resource_note': '只汇总已验证父子链及身份的目标进程；任一已发现进程指标缺失时该聚合指标保持缺失。快照无法证明捕获全部短命子进程；工作集相加可能重复计算共享页。'}
        return values, surviving
