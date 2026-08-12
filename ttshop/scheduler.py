"""轻量级定时调度器（标准库实现，零第三方依赖）。

三种运行模式：
- 固定时刻：--time 08:30，每天本地时区该时刻执行一次
- 固定间隔：--interval-minutes 60，按间隔执行（便于测试）
- 一次性：--once，立即执行一次后退出（配合 Windows 任务计划程序使用）
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger("ttshop.scheduler")

WAKE_INTERVAL_SECONDS = 60  # 每 60 秒检查一次目标时刻，保证 Ctrl+C 及时响应


def next_run_time(run_at: str, now: datetime | None = None) -> datetime:
    """计算下一个执行时刻（本地时区）。run_at 形如 '08:30'。

    若今天该时刻已过（或正好等于当前时刻），顺延到明天。
    """
    now = now or datetime.now()
    try:
        hour, minute = (int(x) for x in run_at.split(":"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"执行时刻格式应为 HH:MM，收到: {run_at!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"执行时刻超出范围: {run_at!r}")
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _safe_run(job) -> None:
    """执行任务，异常只记录日志，不中断调度器。"""
    try:
        job()
    except Exception:
        logger.exception("任务执行失败，等待下次调度")


def run_loop(job, run_at: str = "08:30", interval_minutes: int | None = None, once: bool = False) -> None:
    """调度主循环。

    :param job: 无参可调用对象，执行一次完整数据管道
    :param run_at: 每日执行时刻（HH:MM，本地时区）
    :param interval_minutes: 若提供，按该间隔执行（覆盖 run_at）
    :param once: 立即执行一次后退出
    """
    if once:
        logger.info("一次性模式：立即执行一次任务")
        _safe_run(job)
        return

    if interval_minutes:
        next_run = datetime.now() + timedelta(minutes=interval_minutes)
        logger.info("调度器启动：每 %d 分钟执行一次，下次 %s",
                    interval_minutes, next_run.strftime("%Y-%m-%d %H:%M:%S"))
    else:
        next_run = next_run_time(run_at)
        logger.info("调度器启动：每日 %s 执行，下次 %s",
                    run_at, next_run.strftime("%Y-%m-%d %H:%M:%S"))

    try:
        while True:
            remaining = (next_run - datetime.now()).total_seconds()
            if remaining > 0:
                time.sleep(min(remaining, WAKE_INTERVAL_SECONDS))
                continue
            logger.info("开始执行任务")
            _safe_run(job)
            if interval_minutes:
                next_run = datetime.now() + timedelta(minutes=interval_minutes)
            else:
                next_run = next_run_time(run_at)
            logger.info("下次执行：%s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，调度器退出")
