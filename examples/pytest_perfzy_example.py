"""pytest 接入模板：PERFZY_TEST_APP 未设置时跳过，不自动操作设备。"""
import os
import time

import pytest
from perfzy import PerfZY


@pytest.fixture
def perfzy_case(tmp_path, request):
    app = os.environ.get('PERFZY_TEST_APP')
    if not app:
        pytest.skip('先设置 PERFZY_TEST_APP 和 PERFZY_PLATFORM，并打开目标游戏')
    platform = os.environ.get('PERFZY_PLATFORM', 'ios')
    with PerfZY(tmp_path / 'perfzy') as perf:
        perf.connect_device(os.environ.get('PERFZY_DEVICE_QUERY'), platform=platform)
        perf.select_game(app)
        perf.start(metrics=['fps', 'frame_time', 'frame_jank', 'frame_big_jank', 'app_cpu'], screenshots=False)
        perf.start_mark(request.node.name)
        try:
            yield perf
        finally:
            # stop() 会结束未闭合标记；用例失败也会保留已有采样。
            result = perf.stop(request.node.name)
            print('PerfZY 数据：', result.session_path)
            print('PerfZY 报告：', perf.generate_html(result))


def test_game_flow(perfzy_case):
    # 将本段替换为已有的点击、移动、战斗等测试步骤。
    for _ in range(10):
        perfzy_case.check_health()
        time.sleep(1)
    # 示例仅验证收到 FPS，不设未经校准的性能通过线。
    assert 'fps' in perfzy_case.metric_status()['received']
