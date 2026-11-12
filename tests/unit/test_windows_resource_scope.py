"""Offline identity, population and delta checks; never operate user processes."""
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from perfzy.collectors.process_resources import ProcessTreeResources, PROCESS_KEYS
from perfzy.collectors.windows import WindowsCollector
from perfzy.domain.collection_quality import collection_quality_notes
from perfzy.domain.provenance import metric_provenance
from test_windows_collector import make_collector, frame_row


class Process:
    def __init__(self, pid=4242, parent=0, created=1., size=10.):
        self.pid, self.parent, self.created = pid, parent, created
        self.size, self.user, self.kernel = size, 0., 0.
        self.read, self.write, self.reads, self.writes, self.faults = 0, 0, 0, 0, 0
        self.threads, self.handles = 2, 3
        self.running, self.denied = True, set()
        self.descendants, self.children_calls = [], 0

    def _check(self, name):
        if name in self.denied:
            raise psutil.AccessDenied(self.pid)

    def create_time(self):
        self._check('identity')
        return self.created

    def is_running(self): return self.running
    def ppid(self): return self.parent
    def name(self): return 'game.exe'

    def children(self, recursive=False):
        self.children_calls += 1
        self._check('children')
        return self.descendants

    def cpu_times(self):
        self._check('cpu')
        return SimpleNamespace(user=self.user, system=self.kernel)

    def memory_info(self):
        self._check('memory')
        return SimpleNamespace(rss=self.size*1048576, private=self.size*524288,
                               vms=self.size*2097152, num_page_faults=self.faults)

    def num_threads(self):
        self._check('threads')
        return self.threads

    def num_handles(self):
        self._check('handles')
        return self.handles

    def io_counters(self):
        self._check('io')
        return SimpleNamespace(read_bytes=self.read, write_bytes=self.write,
                               read_count=self.reads, write_count=self.writes)


def tree():
    root = Process()
    child = Process(5000, root.pid, 2., 20.)
    grandchild = Process(6000, child.pid, 3., 30.)
    root.descendants = [grandchild, child]  # Order is not proof of ancestry.
    return root, child, grandchild


def test_tree_snapshots_and_normalized_delta_metrics_share_the_population():
    root, child, grandchild = tree()
    sampler = ProcessTreeResources(4)
    first, pids = sampler.sample(root, 10., PROCESS_KEYS)
    assert pids == {4242, 5000, 6000}
    assert first == {'memory': 60., 'private_memory': 30., 'virtual_memory': 120.,
                     'thread_count': 6., 'handle_count': 9.}
    for process in (root, child, grandchild):
        process.user, process.kernel = .4, .2
        process.read, process.write = 2048, 4096
        process.reads, process.writes, process.faults = 4, 6, 8
    second, _ = sampler.sample(root, 12., PROCESS_KEYS)
    assert second['app_cpu'] == pytest.approx(22.5)
    assert second['app_user_cpu'] == pytest.approx(15.)
    assert second['app_kernel_cpu'] == pytest.approx(7.5)
    assert second['disk_read'] == 3. and second['disk_write'] == 6.
    assert second['disk_read_iops'] == 6. and second['disk_write_iops'] == 9.
    assert second['page_faults'] == 12.
    assert all(item['complete'] for item in sampler.last['resource_metric_coverage'].values())
    assert sampler.last['resource_completeness'] == 'not_proven'


def test_new_child_warms_delta_without_emitting_a_partial_sum_then_exit_is_clean():
    root, child = Process(), Process(5000, 4242, 2.)
    sampler, enabled = ProcessTreeResources(2), {'app_cpu', 'disk_read', 'memory'}
    sampler.sample(root, 1., enabled)
    root.user, root.read = 1., 1024
    root.descendants = [child]
    values, _ = sampler.sample(root, 2., enabled)
    assert values == {'memory': 20.}
    assert sampler.last['resource_metric_coverage']['app_cpu']['warming_pids'] == [5000]
    root.user, root.read, child.user, child.read = 2., 2048, .5, 2048
    values, _ = sampler.sample(root, 3., enabled)
    assert values['app_cpu'] == pytest.approx(75.)
    assert values['disk_read'] == 3.
    root.descendants, root.user, root.read = [], 3., 3072
    values, _ = sampler.sample(root, 4., enabled)
    assert values['app_cpu'] == 50. and values['disk_read'] == 1.
    assert sampler.last['resource_pids'] == [4242]


def test_reused_child_pid_has_new_baselines_and_does_not_inherit_old_counters():
    root, child, _ = tree()
    root.descendants = [child]
    sampler, enabled = ProcessTreeResources(4), {'app_cpu', 'disk_read'}
    sampler.sample(root, 1., enabled)
    root.user, child.user, child.read = 1., 100., 102400
    sampler.sample(root, 2., enabled)
    replacement = Process(child.pid, root.pid, created=5.)
    root.descendants, root.user = [replacement], 2.
    values, _ = sampler.sample(root, 3., enabled)
    assert not values
    assert sampler.last['resource_metric_coverage']['app_cpu']['warming_pids'] == [5000]
    replacement.user, root.user = .5, 3.
    assert sampler.sample(root, 4., enabled)[0]['app_cpu'] == 37.5


@pytest.mark.parametrize('kind', ['unrelated', 'reparented', 'older', 'denied', 'cycle'])
def test_invalid_descendants_never_contribute_to_a_complete_aggregate(kind):
    root, child, grandchild = tree()
    if kind == 'unrelated': child.parent = 7777
    elif kind == 'reparented': grandchild.parent = 0
    elif kind == 'older': child.created = 0.
    elif kind == 'denied': child.denied.add('identity')
    else: child.parent = grandchild.pid
    sampler = ProcessTreeResources(4)
    values, pids = sampler.sample(root, 1., {'memory'})
    assert values == {}
    assert sampler.last['resource_failed_pids']
    assert not sampler.last['resource_metric_coverage']['memory']['complete']
    assert 4242 in pids


def test_child_permission_failure_is_per_metric_and_never_fills_a_partial_sum():
    root, child, _ = tree()
    root.descendants = [child]
    child.denied.add('memory')
    sampler = ProcessTreeResources(4)
    values, _ = sampler.sample(root, 1., {'memory', 'thread_count'})
    assert values == {'thread_count': 4.}
    assert sampler.last['resource_failed_pids'] == [5000]
    assert sampler.last['resource_metric_coverage']['memory']['missing_pids'] == [5000]


def test_discovery_failure_does_not_turn_root_only_into_complete_tree():
    root = Process()
    root.denied.add('children')
    sampler = ProcessTreeResources(4)
    assert sampler.sample(root, 1., {'memory'})[0] == {}
    assert sampler.last['resource_discovery_failed']


def test_counter_reset_is_missing_then_recovers_from_a_fresh_baseline():
    root, sampler, enabled = Process(), ProcessTreeResources(2), {'app_cpu', 'disk_read', 'page_faults'}
    root.user, root.read, root.faults = 20., 10240, 100
    sampler.sample(root, 1., enabled)
    root.user, root.read, root.faults = 0., 0, 0
    assert sampler.sample(root, 2., enabled)[0] == {}
    root.user, root.read, root.faults = 1., 1024, 2
    assert sampler.sample(root, 3., enabled)[0] == {'app_cpu': 50., 'disk_read': 1., 'page_faults': 2.}


def test_disabled_interval_never_enters_the_new_delta():
    root, sampler, enabled = Process(), ProcessTreeResources(2), {'app_cpu', 'disk_read'}
    sampler.sample(root, 1., enabled)
    sampler.configure(set())
    root.user, root.read = 100., 102400
    sampler.configure(enabled)
    assert sampler.sample(root, 100., enabled)[0] == {}
    root.user, root.read = 101., 103424
    assert sampler.sample(root, 101., enabled)[0] == {'app_cpu': 50., 'disk_read': 1.}


@pytest.mark.parametrize('change', ['exited', 'reused', 'reparented'])
def test_change_during_a_read_discards_snapshot_and_descendants(change):
    root, child, grandchild = tree()
    original = child.memory_info
    def changed():
        value = original()
        if change == 'exited': child.running = False
        elif change == 'reused': child.created = 100.
        else: child.parent = 0
        return value
    child.memory_info = changed
    sampler = ProcessTreeResources(4)
    values, pids = sampler.sample(root, 1., {'memory'})
    assert not values and pids == {4242}
    assert sampler.last['resource_pids'] == [4242]
    assert sampler.last['resource_failed_pids'] == [5000, 6000]


def test_scope_is_explicit_and_frozen_while_running():
    collector = WindowsCollector()
    assert collector.diagnostics()['process_scope']['resource_scope'] == 'selected'
    with pytest.raises(ValueError): collector.set_resource_scope('all')
    collector.set_resource_scope('tree')
    collector._process = Process()
    with pytest.raises(RuntimeError, match='先停止'): collector.set_resource_scope('selected')


def test_frame_stream_stays_independent_from_resource_population_and_discovery_is_reused():
    root, child, grandchild = tree()
    unrelated = Process(7000, 8888, 4.)
    root.descendants.append(unrelated)
    collector = make_collector([frame_row(20, pid=child.pid)] * 3 +
                               [frame_row(100, pid=root.pid)], enabled={'fps','memory','app_gpu_usage'})
    collector._resource_scope = 'tree'
    collector._process = root
    collector._recorded_target_pid = root.pid
    collector._gpu_counters.sample = Mock(return_value={'app_gpu_usage': 12.})
    collector._gpu_counters.close = Mock()
    sample = collector.sample(1.)
    assert sample.values['fps'] == 50. and len(sample.frames) == 3
    assert 'memory' not in sample.values  # Known unverified candidate => no false full sum.
    assert root.children_calls == 1
    collector._gpu_counters.sample.assert_called_once_with({4242, 5000, 6000}, collector._enabled)
    assert collector.diagnostics()['process_scope']['resource_failed_pids'] == [7000]
    collector._terminate_presentmon = Mock()
    collector.stop()
    scope = collector.diagnostics()['process_scope']
    assert scope['resource_scope'] == 'tree' and scope['selected_pid'] == 4242
    assert scope['resource_pids'] == [4242, 5000, 6000]
    notes = collection_quality_notes(collector.diagnostics())
    assert any('聚合指标保持缺失' in note for note in notes)
    assert not any('不包含该子进程' in note for note in notes)


def test_selected_default_does_not_sum_child_resources():
    root, child, _ = tree()
    collector = make_collector(enabled={'memory'})
    collector._process = root
    assert collector.sample(1.).values == {'memory': 10.}
    assert root.children_calls == 0


def test_windows_provenance_uses_recorded_scope_and_other_platforms_do_not_change():
    keys = {'app_cpu','memory','disk_read','app_gpu_usage','network_recv','fps'}
    selected = metric_provenance('windows', keys)
    grouped = metric_provenance('windows', keys, {'resource_scope':'tree'})
    assert '不自动合计子进程' in selected['memory']['scope']
    assert '验证的子进程' in grouped['memory']['scope']
    assert '共享页' in grouped['memory']['note']
    assert '验证的子进程' in grouped['app_gpu_usage']['scope']
    assert '不保证' in grouped['app_gpu_usage']['note']
    assert selected['network_recv'] == grouped['network_recv']
    assert selected['fps'] == grouped['fps']
    for platform in ('android', 'ios'):
        assert metric_provenance(platform, keys) == metric_provenance(platform, keys, {'resource_scope':'tree'})
