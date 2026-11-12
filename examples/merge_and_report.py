"""仅处理本地记录：列出记录、按顺序合并、生成 HTML 报告。"""
import argparse
from pathlib import Path

from perfzy import PerfZY


def main():
    parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--root', type=Path, required=True)
            parser.add_argument('--sessions', nargs='+', help='按希望的合并顺序提供至少两个记录编号')
                parser.add_argument('--name', default='自动化合并记录')
                    args = parser.parse_args()
                        with PerfZY(args.root) as perf:
                                if not args.sessions:
                                            for record in perf.list_records():
                                                            print(record['id'], record.get('name', ''), record.get('duration', ''))
                                                                        return
                                                                                path = perf.merge_records(args.sessions, args.name)
                                                                                        print('合并记录：', path)
                                                                                                print('HTML：', perf.generate_html(path))


                                                                                                if __name__ == '__main__':
                                                                                                    main()
                                                                                                    