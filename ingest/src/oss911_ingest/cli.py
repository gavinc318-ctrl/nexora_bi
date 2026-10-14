"""命令行入口：oss911-ingest run --source <契约编号> --window <起>/<止>

退出码（LD-01 2.5 · DD-84），DAG 依赖这套约定，改动须同步：
    0  成功——含「批次被登记隔离」。隔离是设计好的结果，不是失败；
       否则 Airflow 会反复重试一个注定被隔离的批次
    1  运行失败（含：同一源已有实例在运行，咨询锁取不到——DD-85）
    2  用法或配置错误
程序不得以 99 退出：BashOperator 默认把 99 当作「跳过」，任务会被静默标为跳过而非失败。
DAG 里一律设 skip_on_exit_code=None，这里再从源头保证不出现 99。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import __version__

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

WINDOW_FORMAT = "%Y-%m-%dT%H:%M:%S"


class UsageError(Exception):
    """用法或配置错误，以退出码 2 结束。"""


def parse_window(text: str) -> tuple[datetime, datetime]:
    """解析 <起>/<止>，形如 2026-10-14T00:00:00/2026-10-14T00:05:00。

    只校验格式与先后；窗口长度的上下限属于各源契约（如 ICP 话单 ≤24h 且 ≥5s），
    在读到契约之后再校验。
    """
    parts = text.split("/")
    if len(parts) != 2:
        raise UsageError(f"--window 须为 <起>/<止>，实际：{text!r}")
    try:
        start, end = (datetime.strptime(p, WINDOW_FORMAT) for p in parts)
    except ValueError as exc:
        raise UsageError(f"--window 的时间格式须为 {WINDOW_FORMAT}：{exc}") from None
    if start >= end:
        raise UsageError(f"--window 的起点须早于终点：{text!r}")
    return start, end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oss911-ingest",
        description="按源契约取数、校验、写 raw、推进水位（LD-01）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="对一个源、一个窗口执行一次接入")
    run.add_argument("--source", required=True, help="契约中的 source_id，如 icp_cdr_voice")
    run.add_argument("--window", required=True, help="<起>/<止>，如 2026-10-14T00:00:00/2026-10-14T00:05:00")
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    start, end = parse_window(args.window)
    print(f"oss911-ingest {__version__}: source={args.source} window={start:%Y-%m-%d %H:%M:%S}/{end:%Y-%m-%d %H:%M:%S}")
    print("接入逻辑尚未实现（第 2 批进行中）", file=sys.stderr)
    return EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)   # 用法错误时 argparse 自己以 2 退出
        if args.command == "run":
            return cmd_run(args)
        raise UsageError(f"未知命令：{args.command}")
    except UsageError as exc:
        print(f"用法错误：{exc}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit:
        raise                            # argparse 的 0（--help / --version）与 2
    except BaseException as exc:         # noqa: BLE001 —— 任何意外一律以 1 结束，绝不落到 99
        print(f"运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
