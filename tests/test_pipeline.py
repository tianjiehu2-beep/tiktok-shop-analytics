"""端到端冒烟测试：seed -> analyze -> report。"""

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.scoring import run_analysis
from ttshop.config import Settings
from ttshop.db import Database
from ttshop.demo_data import generate_history, generate_products
from ttshop.report.html_report import build_report


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / "tests" / "_tmp"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.settings = Settings(db_path=str(self.tmp / "test.db"), report_dir=str(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_pipeline(self):
        db = Database(self.settings.db_path)
        db.init_schema()

        products = generate_products(count=50, seed=1)
        written = db.upsert_products(products)
        self.assertEqual(written, 50)

        for product_id, price, sold, ts in generate_history(products):
            db.add_history(product_id, price, sold, ts)

        analyzed = run_analysis(db, self.settings)
        self.assertEqual(analyzed, 50)

        report_path = Path(self.tmp) / "report.html"
        build_report(db, self.settings, report_path)
        self.assertTrue(report_path.exists())
        text = report_path.read_text(encoding="utf-8")
        self.assertIn("选品分析看板", text)
        self.assertIn("爆品榜", text)

        latest = db.latest_analysis()
        self.assertEqual(len(latest), 50)
        self.assertTrue(all(a["selection_score"] <= 100 for a in latest))


if __name__ == "__main__":
    unittest.main()
