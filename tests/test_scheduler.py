"""调度器单元测试：next_run_time 与一次性模式。"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.scheduler import next_run_time, run_loop


class SchedulerTest(unittest.TestCase):
    def test_future_time_today(self):
        now = datetime(2026, 8, 12, 8, 0, 0)
        self.assertEqual(next_run_time("08:30", now), datetime(2026, 8, 12, 8, 30, 0))

    def test_past_time_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 12, 9, 0, 0)
        self.assertEqual(next_run_time("08:30", now), datetime(2026, 8, 13, 8, 30, 0))

    def test_exact_now_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 12, 8, 30, 0)
        self.assertEqual(next_run_time("08:30", now), datetime(2026, 8, 13, 8, 30, 0))

    def test_invalid_format(self):
        with self.assertRaises(ValueError):
            next_run_time("8:30am")

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            next_run_time("24:00")

    def test_once_mode_runs_job_once(self):
        calls = []

        def job():
            calls.append(1)

        run_loop(job, once=True)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
