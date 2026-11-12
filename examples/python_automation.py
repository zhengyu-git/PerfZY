"""Python SDK 示例；先连接设备并打开游戏。运行 --help 查看参数。"""
import argparse
from pathlib import Path
import time

from perfzy import PerfZY


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('android', 'ios', 'windows'), required=True)
    parser.add_argument('--app', required=True, help='包名、bundle id 或 exe 名称')
    parser.add_argument('--device', default=None, help='设备序列号或名称；多设备时建议明确指定')
    parser.add_argument('--root', type=Path, default=Path.cwd() / 'perfzy-runs')
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--fps-limit', type=float, default=None, help='仅 iOS，0 表示不设上限')
    parser.add_argument('--screenshots', action='store_true')
    args = parser.parse_args()
    if not 0 < args.seconds <= 86400:
        parser.error('--seconds 必须大于 0 且不超过 86400')
    memory = 'ios_physical_footprint' if args.platform == 'ios' else 'memory'
    with PerfZY(args.root) as perf:
        device = perf.connect_device(args.device, platform=args.platform)
        game = perf.select_game(args.app)
        print('设备：', device.name, '应用：', game.name)
        perf.start(metrics=['fps', 'frame_time', 'frame_jank', 'frame_big_jank', 'app_cpu', memory],
                   sample_interval=1, screenshots=args.screenshots,
                   ios_fps_limit=args.fps_limit)
        perf.start_mark('自动化测试')
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            perf.check_health()
            print(perf.latest_metrics())
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        perf.end_mark()
        result = perf.stop('自动化性能测试')
        print('数据：', result.session_path)
        print('报告：', perf.generate_html(result))


if __name__ == '__main__':
    main()
