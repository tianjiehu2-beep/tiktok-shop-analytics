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
        report, _ = run_pipeline(db, self.settings, source="demo", product_count=20, seed=1)
        self.assertTrue(report.exists())
        stats = db.stats()
        self.assertEqual(stats["products"], 20)
        self.assertGreater(stats["analyses"], 0)

    def test_pipeline_demo_flag_maps_to_demo_source(self):
        db = Database(self.settings.db_path)
        db.init_schema()
        report, _ = run_pipeline(db, self.settings, demo=True, product_count=10, seed=1)
        self.assertTrue(report.exists())
        self.assertEqual(db.stats()["products"], 10)


if __name__ == "__main__":
    unittest.main()


class ApiSourceEchoTikAdvancedTest(unittest.TestCase):
    def setUp(self):
        self.source = ApiSource(settings=Settings(api_key="secret"), provider="echotik")

    def test_normalize_echotik_new_fields(self):
        item = {
            "product_id": "P9001",
            "product_name": "Yoga Mat",
            "min_price": 19.99,
            "total_sale_cnt": 1000,
            "total_sale_7d_cnt": 120,
            "total_sale_30d_cnt": 300,
            "total_sale_gmv_amt": 50000.5,
            "total_ifl_cnt": 88,
            "total_video_cnt": 150,
            "product_rating": 4.5,
            "review_count": 40,
            "category_id": "600154",
        }
        p = self.source.normalize_item(item, keyword="yoga mat")
        self.assertIsNotNone(p)
        self.assertEqual(p.sale_7d_cnt, 120)
        self.assertEqual(p.sale_30d_cnt, 300)
        self.assertEqual(p.gmv_total, 50000.5)
        self.assertEqual(p.influencer_cnt, 88)
        self.assertEqual(p.video_cnt, 150)
        self.assertEqual(p.category_id, "600154")

    def test_multi_keyword_dedupe(self):
        seen = {"called": 0}
        def fake_request(url, config, body=None):
            seen["called"] += 1
            return {"data": {"list": [
                {"product_id": "P1", "title": "Yoga Mat", "price": 12.99, "sales": 100},
            ]}}
        self.source._request_json = fake_request
        result = self.source.fetch(keyword="yoga mat,resistance band", limit=10)
        self.assertEqual(seen["called"], 2)
        self.assertEqual(len(result.products), 1)  # dedup by product_id
        self.assertEqual(result.products[0].product_id, "P1")

    def test_category_crawl_url_and_items(self):
        self.source._tree = {
            "language": "en-US",
            "l1": [{"category_id": "600154", "category_name": "Textiles & Soft Furnishings", "parent_id": ""}],
            "l2": [],
            "l3": [],
        }
        captured = {}
        def fake_request(url, config, body=None):
            captured["url"] = url
            return {"data": [
                {"product_id": "C1", "product_name": "Cushion", "min_price": 9.9,
                 "total_sale_cnt": 500, "category_id": "600154"},
            ]}
        self.source._request_json = fake_request
        self.source.category_id = "600154"
        result = self.source.fetch(limit=10)
        url = captured["url"]
        self.assertIn("/api/v3/echotik/product/list", url)
        self.assertIn("category_id=600154", url)
        self.assertIn("page_num=1", url)
        self.assertIn("off_mark=0", url)
        self.assertIn("region=US", url)
        self.assertEqual(result.products[0].product_id, "C1")
        # category name resolved from tree
        self.assertIn("Textiles", result.products[0].category)

    def test_category_crawl_sort_and_filters(self):
        self.source._tree = {"l1": [], "l2": [], "l3": []}
        captured = {}
        def fake_request(url, config, body=None):
            captured["url"] = url
            return {"data": []}
        self.source._request_json = fake_request
        self.source.category_id = "999"
        self.source.sort_field = "sales7d"
        self.source.min_sales = 100
        self.source.max_price = 50.0
        self.source.min_commission = 0.1
        self.source.fetch(limit=10)
        url = captured["url"]
        self.assertIn("product_sort_field=4", url)
        self.assertIn("sort_type=1", url)
        self.assertIn("min_total_sale_cnt=100", url)
        self.assertIn("max_spu_avg_price=50.0", url)
        self.assertIn("min_product_commission_rate=0.1", url)

    def test_search_categories_by_name(self):
        self.source._tree = {
            "language": "en-US",
            "l1": [{"category_id": "600001", "category_name": "Home Supplies", "parent_id": ""}],
            "l2": [{"category_id": "600111", "category_name": "Yoga", "parent_id": "600001"}],
            "l3": [{"category_id": "600999", "category_name": "Mats", "parent_id": "600111"}],
        }
        matches = self.source.search_categories("yoga")
        self.assertTrue(matches)
        self.assertTrue(any("Home Supplies" in m["path"] and "Yoga" in m["path"] for m in matches))
        self.assertTrue(any("Mats" in m["path"] for m in matches))

    def test_ranklist_unsupported_provider(self):
        source = ApiSource(settings=Settings(api_key="secret"), provider="fastmoss")
        with self.assertRaises(RuntimeError):
            source.fetch_ranklist()

    def test_enrich_merges_detail(self):
        base = self.source
        base._request_json = lambda url, config, body=None: {
            "data": [
                {"product_id": "P1", "title": "Yoga Mat Pro", "price": 12.99, "sales": 100,
                 "total_sale_cnt": 100, "product_rating": 4.9, "review_count": 300,
                 "total_sale_gmv_amt": 5000.0, "total_ifl_cnt": 12, "total_video_cnt": 30},
            ]
        }
        products = [
            Product(product_id="P1", title="Yoga Mat Pro", category="Unknown", price=12.99,
                    original_price=12.99, sold_count=100, rating=0.0, review_count=0,
                    seller_name="", seller_id="", commission_rate=0.0, video_views=0,
                    video_likes=0, listed_at=""),
        ]
        enriched = base._enrich_details(base._provider_config(), "https://open.echotik.live", products)
        self.assertEqual(enriched[0].rating, 4.9)
        self.assertEqual(enriched[0].review_count, 300)
        self.assertEqual(enriched[0].gmv_total, 5000.0)
        self.assertEqual(enriched[0].influencer_cnt, 12)


class InfluencerKeywordTest(unittest.TestCase):
    def setUp(self):
        self.source = ApiSource(settings=Settings(api_key="secret"), provider="echotik")

    def test_normalize_influencer(self):
        item = {
            "user_id": "7202723462921962539",
            "nick_name": "Iselyta",
            "total_followers_cnt": 2706157,
            "total_post_video_cnt": 3670,
            "total_sale_cnt": 18999,
            "total_sale_gmv_amt": 683392.7,
            "ec_score": 8.15,
            "interaction_rate": 0.03,
            "per_video_product_views_avg_7d_cnt": 2693.86,
            "region": "US",
        }
        inf = self.source.normalize_influencer(item)
        self.assertIsNotNone(inf)
        self.assertEqual(inf.user_id, "7202723462921962539")
        self.assertEqual(inf.followers_cnt, 2706157)
        self.assertEqual(inf.sale_cnt, 18999)
        self.assertEqual(inf.sale_gmv_amt, 683392.7)
        self.assertEqual(inf.ec_score, 8.15)
        self.assertEqual(inf.per_video_views_avg_7d, 2693.86)

    def test_normalize_influencer_missing_id(self):
        self.assertIsNone(self.source.normalize_influencer({"nick_name": "x"}))

    def test_fetch_keyword_trends(self):
        captured = {}
        def fake_request(url, config, body=None):
            captured["url"] = url
            return {"data": {"inspiration_list": [
                {"query_text": "ford pinto car", "video_num": 0,
                 "popularity_v2": 60665062, "trending_seq_v2": [0, 7400, 83108]},
                {"query_text": "sydney towel", "video_num": 1182,
                 "popularity_v2": 53974598, "trending_seq_v2": [100, 200, 300]},
            ]}}
        self.source._request_json = fake_request
        trends = self.source.fetch_keyword_trends(tab="all", count=10)
        self.assertIn("/realtime/trending/keyword/ranking", captured["url"])
        self.assertIn("tab=all", captured["url"])
        self.assertEqual(len(trends), 2)
        self.assertEqual(trends[0].keyword, "ford pinto car")
        self.assertEqual(trends[0].video_num, 0)
        self.assertEqual(trends[0].popularity, 60665062)
        self.assertEqual(trends[0].trend, [0, 7400, 83108])
        self.assertEqual(trends[0].source, "ranking")

    def test_fetch_influencers_url(self):
        captured = {}
        def fake_request(url, config, body=None):
            captured["url"] = url
            return {"data": [
                {"user_id": "U1", "nick_name": "A", "total_followers_cnt": 1000,
                 "total_sale_cnt": 10, "total_sale_gmv_amt": 100.0},
            ]}
        self.source._request_json = fake_request
        result = self.source.fetch_influencers(pages=1, limit=5)
        url = captured["url"]
        self.assertIn("/api/v3/echotik/influencer/list", url)
        self.assertIn("influencer_sort_field_v2=1", url)
        self.assertIn("sales_flag=3", url)
        self.assertEqual(result[0].user_id, "U1")

    def test_fetch_product_influencers(self):
        def fake_request(url, config, body=None):
            return {"data": [
                {"product_id": "P1", "user_id": "U1", "nick_name": "Haley",
                 "total_followers_cnt": 74592, "per_product_ifl_sale_cnt": 174907,
                 "per_product_ifl_gmv_amt": 1922213},
            ]}
        self.source._request_json = fake_request
        rows = self.source.fetch_product_influencers("P1", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["per_sale_cnt"], 174907)
        self.assertEqual(rows[0]["per_gmv_amt"], 1922213)


class DbNewTablesTest(unittest.TestCase):
    def test_influencer_keyword_roundtrip(self):
        tmp = Path("data/test_extra.db")
        tmp.parent.mkdir(exist_ok=True)
        if tmp.exists():
            tmp.unlink()
        db = Database(tmp)
        db.init_schema()
        from ttshop.models import Influencer, KeywordTrend
        inf = Influencer(user_id="U1", nick_name="Tester", followers_cnt=100, sale_gmv_amt=99.9)
        self.assertEqual(db.upsert_influencers([inf]), 1)
        rows = db.top_influencers(limit=5)
        self.assertEqual(rows[0]["user_id"], "U1")
        self.assertEqual(rows[0]["followers_cnt"], 100)

        kw = KeywordTrend(keyword="yoga mat", video_num=10, popularity=99, trend=[1, 2, 3])
        self.assertEqual(db.upsert_keyword_trends([kw]), 1)
        kws = db.latest_keyword_trends(source="ranking", limit=5)
        self.assertEqual(kws[0]["keyword"], "yoga mat")
        self.assertIn("2", kws[0]["trend_json"])

        rows = [{"product_id": "P1", "user_id": "U1", "nick_name": "T", "per_sale_cnt": 5}]
        self.assertEqual(db.save_product_influencers(rows), 1)
        links = db.product_influencers("P1", limit=5)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["per_sale_cnt"], 5)
        tmp.unlink()
