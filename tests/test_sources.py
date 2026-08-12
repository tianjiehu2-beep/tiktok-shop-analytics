"""数据源抽象层单元测试：demo / api 适配器与管道集成。"""

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.config import Settings
from ttshop.db import Database
from ttshop.models import Product
from ttshop.pipeline import run_pipeline
from ttshop.sources import ApiSource, DemoSource, ScraperSource, get_source
from ttshop.sources.api import dig_path


class GetSourceTest(unittest.TestCase):
    def test_known_sources(self):
        self.assertIsInstance(get_source("demo", Settings()), DemoSource)
        self.assertIsInstance(get_source("scraper", Settings()), ScraperSource)
        self.assertIsInstance(get_source("api", Settings()), ApiSource)

    def test_default_source_is_demo(self):
        self.assertIsInstance(get_source(None, Settings()), DemoSource)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            get_source("nope", Settings())


class DemoSourceTest(unittest.TestCase):
    def test_fetch_returns_products_and_history(self):
        source = DemoSource(seed=1, product_count=30)
        result = source.fetch(category="Sports & Outdoors")
        self.assertEqual(len(result.products), 30)
        self.assertTrue(all(isinstance(p, Product) for p in result.products))
        self.assertTrue(result.history)
        self.assertTrue(all(len(row) == 4 for row in result.history))


class ApiSourceTest(unittest.TestCase):
    def test_missing_key_raises(self):
        source = ApiSource(settings=Settings())
        with self.assertRaises(RuntimeError):
            source.fetch(keyword="yoga mat")

    def test_normalize_item(self):
        source = ApiSource(settings=Settings(api_key="test"))
        item = {
            "product_id": "P1001",
            "title": "Non-Slip Yoga Mat",
            "price": "19.99",
            "sales": "2.3K",
            "rating": 4.6,
            "review_count": 120,
            "seller_name": "TikTop Store",
            "video_views": "1.2M",
        }
        product = source.normalize_item(item, keyword="yoga mat")
        self.assertIsNotNone(product)
        self.assertEqual(product.title, "Non-Slip Yoga Mat")
        self.assertEqual(product.price, 19.99)
        self.assertEqual(product.sold_count, 2300)
        self.assertEqual(product.video_views, 1_200_000)
        self.assertEqual(product.product_id, "P1001")

    def test_dig_path(self):
        payload = {"data": {"list": [{"id": 1}, {"id": 2}]}}
        self.assertEqual(dig_path(payload, "data.list"), [{"id": 1}, {"id": 2}])
        self.assertEqual(dig_path(payload, "data.missing"), [])

    def test_fetch_with_mock_http(self):
        source = ApiSource(settings=Settings(api_key="test"))
        source._request_json = lambda url, config, body=None: {
            "data": {"list": [
                {"product_id": "P1", "title": "Yoga Mat Pro", "price": 12.99, "sales": 100},
                {"product_id": "P2", "title": "Yoga Block", "price": 8.5, "sales": 50},
            ]}
        }
        result = source.fetch(keyword="yoga mat", limit=10)
        self.assertEqual(len(result.products), 2)
        self.assertEqual(result.products[0].product_id, "P1")

    def test_build_url_without_auth_header(self):
        from dataclasses import replace

        source = ApiSource(settings=Settings(api_key="secret"), provider="echotik")
        config = replace(source._provider_config(), auth_header="")
        url = source._build_url(config, "https://example.com", "yoga mat", 10, "Sports")
        self.assertIn("sk=yoga+mat", url)
        self.assertIn("api_key=secret", url)


    def test_fastmoss_request_body(self):
        source = ApiSource(settings=Settings(api_key="secret"), provider="fastmoss")
        url, body = source._build_request(
            source._provider_config(), "https://openapi.fastmoss.com", "yoga mat", 10, None)
        self.assertEqual(url, "https://openapi.fastmoss.com/product/v1/search")
        self.assertEqual(body["keywords"], "yoga mat")
        self.assertEqual(body["pagesize"], 10)
        self.assertEqual(body["filter"]["region"], "US")

    def test_normalize_item_fastmoss(self):
        source = ApiSource(settings=Settings(api_key="test"), provider="fastmoss")
        item = {
            "product_id": "1729514474840169156",
            "title": "Non-Slip Yoga Mat",
            "floor_price": 29.99,
            "total_units_sold": 4921,
            "product_rating": 4.9,
            "commission_rate": "12.5%",
            "ctime": "2025-06-03 02:19:00",
            "category": {"l1": {"name": "Sports & Outdoors"}, "l2": {"name": "Yoga"}},
            "shop": {"seller_id": 123, "name": "Test Store"},
        }
        product = source.normalize_item(item, keyword="yoga mat")
        self.assertIsNotNone(product)
        self.assertEqual(product.product_id, "1729514474840169156")
        self.assertEqual(product.price, 29.99)
        self.assertEqual(product.sold_count, 4921)
        self.assertEqual(product.rating, 4.9)
        self.assertEqual(product.seller_name, "Test Store")
        self.assertEqual(product.seller_id, "123")
        self.assertEqual(product.commission_rate, 12.5)
        self.assertIn("Sports & Outdoors", product.category)

    def test_echotik_query_params(self):
        source = ApiSource(settings=Settings(api_key="secret"), provider="echotik")
        url = source._build_url(source._provider_config(), "https://open.echotik.live", "yoga mat", 20, None)
        self.assertIn("/api/v3/echotik/search/items", url)
        self.assertIn("sk=yoga+mat", url)
        self.assertIn("type=2", url)
        self.assertIn("size=20", url)

    def test_normalize_item_echotik(self):
        source = ApiSource(settings=Settings(api_key="test"), provider="echotik")
        item = {
            "product_id": "1729425704051509331",
            "product_name": "Yoga Mat Non Slip",
            "min_price": 12.5,
            "total_sale_cnt": 8800,
            "product_rating": 4.8,
            "review_count": 320,
            "seller_id": "7494831765943781864",
            "product_commission_rate": 15,
        }
        product = source.normalize_item(item, keyword="yoga mat")
        self.assertIsNotNone(product)
        self.assertEqual(product.product_id, "1729425704051509331")
        self.assertEqual(product.title, "Yoga Mat Non Slip")
        self.assertEqual(product.price, 12.5)
        self.assertEqual(product.sold_count, 8800)
        self.assertEqual(product.rating, 4.8)
        self.assertEqual(product.review_count, 320)
        self.assertEqual(product.commission_rate, 15.0)

class PipelineSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / "tests" / "_tmp_sources"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.settings = Settings(db_path=str(self.tmp / "test.db"), report_dir=str(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pipeline_demo_source(self):
        db = Database(self.settings.db_path)
        db.init_schema()
        report = run_pipeline(db, self.settings, source="demo", product_count=20, seed=1)
        self.assertTrue(report.exists())
        stats = db.stats()
        self.assertEqual(stats["products"], 20)
        self.assertGreater(stats["analyses"], 0)

    def test_pipeline_demo_flag_maps_to_demo_source(self):
        db = Database(self.settings.db_path)
        db.init_schema()
        report = run_pipeline(db, self.settings, demo=True, product_count=10, seed=1)
        self.assertTrue(report.exists())
        self.assertEqual(db.stats()["products"], 10)


if __name__ == "__main__":
    unittest.main()
