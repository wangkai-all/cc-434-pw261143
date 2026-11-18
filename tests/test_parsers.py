import unittest

from pricehunter.fixtures import catalog
from pricehunter.model import ErrorKind
from pricehunter.parsers import (
    AcmeParserV1, AcmeParserV2, BargainParserV1, CegParserV1, StructureChanged,
)
from pricehunter.transport import Transport


class ParserTests(unittest.TestCase):
    def test_acme_v1_parses_legacy_page_and_paginates(self):
        html = catalog.acme_html("2026-11-13", 1)
        result = AcmeParserV1().parse(html.encode())
        self.assertEqual(len(result.items), 3)
        self.assertEqual(result.items[0].sku, "SKU-1001")
        self.assertEqual(result.items[0].price_cents, 1299)
        self.assertEqual(result.next_url, "/acme/products?page=2")

    def test_acme_v1_legit_empty_page_is_not_structure_error(self):
        html = catalog.acme_html("2026-11-13", 99)
        result = AcmeParserV1().parse(html.encode())
        self.assertEqual(result.items, [])
        self.assertIsNone(result.next_url)

    def test_acme_v1_rejects_v2_markup_as_structure_changed(self):
        html = catalog.acme_html("2026-11-16", 1)
        with self.assertRaises(StructureChanged) as ctx:
            AcmeParserV1().parse(html.encode())
        self.assertEqual(ctx.exception.kind, ErrorKind.STRUCTURE_CHANGED)

    def test_acme_v2_parses_new_markup_with_dynamic_list_price(self):
        html = catalog.acme_html("2026-11-16", 1)
        result = AcmeParserV2().parse(html.encode())
        item = next(i for i in result.items if i.sku == "SKU-1001")
        self.assertEqual(item.price_cents, 1049)
        self.assertEqual(item.list_price_cents, 1299)

    def test_acme_v2_rejects_legacy_markup(self):
        html = catalog.acme_html("2026-11-13", 1)
        with self.assertRaises(StructureChanged):
            AcmeParserV2().parse(html.encode())

    def test_bargain_dynamic_fields_are_optional_and_tolerated(self):
        before = catalog.bargain_json("2026-11-13", None)
        after = catalog.bargain_json("2026-11-15", None)
        r1 = BargainParserV1().parse(__import__("json").dumps(before).encode())
        r2 = BargainParserV1().parse(__import__("json").dumps(after).encode())
        self.assertIsNone(next(i for i in r1.items if i.sku == "SKU-2001").list_price_cents)
        promoted = next(i for i in r2.items if i.sku == "SKU-2001")
        self.assertEqual(promoted.list_price_cents, 3199)
        self.assertEqual(promoted.promo_code, "SAVE15")
        # 游标分页
        self.assertEqual(r2.next_url, "/bargain/api/items?cursor=c2")

    def test_ceg_parser_and_maintenance_detection(self):
        html = catalog.ceg_html("2026-11-13", 1)
        result = CegParserV1().parse(html.encode())
        self.assertEqual(len(result.items), 2)
        maint = catalog.soft_maintenance_html("CegMart").encode()
        self.assertTrue(Transport.detect_soft_maintenance(maint))
        self.assertFalse(Transport.detect_soft_maintenance(html.encode()))
        # 正常空页绝不能被误判成维护
        empty = catalog.ceg_html("2026-11-13", 99).encode()
        self.assertFalse(Transport.detect_soft_maintenance(empty))
        self.assertEqual(CegParserV1().parse(empty).items, [])


if __name__ == "__main__":
    unittest.main()
