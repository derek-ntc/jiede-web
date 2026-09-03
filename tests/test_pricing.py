import unittest

from pricing import format_money_minor, line_total_minor, normalize_currency, parse_money_minor


class PricingTests(unittest.TestCase):
    def test_cny_round_trip_and_zero(self):
        self.assertEqual(parse_money_minor("12.34", "CNY"), 1234)
        self.assertEqual(parse_money_minor("0", "CNY"), 0)
        self.assertEqual(format_money_minor(1234, "CNY"), "12.34")

    def test_blank_is_missing_and_negative_is_rejected(self):
        self.assertIsNone(parse_money_minor("", "CNY"))
        with self.assertRaisesRegex(ValueError, "产品价格不能为负数"):
            parse_money_minor("-0.01", "CNY")

    def test_precision_and_currency_are_validated(self):
        with self.assertRaisesRegex(ValueError, "价格最多保留 2 位小数"):
            parse_money_minor("1.001", "CNY")
        self.assertEqual(parse_money_minor("120", "JPY"), 120)
        with self.assertRaisesRegex(ValueError, "价格最多保留 0 位小数"):
            parse_money_minor("120.5", "JPY")
        with self.assertRaisesRegex(ValueError, "请选择有效币种"):
            normalize_currency("ABC")

    def test_line_total_keeps_missing_distinct_from_zero(self):
        self.assertIsNone(line_total_minor(None, 3))
        self.assertEqual(line_total_minor(0, 3), 0)
        self.assertEqual(line_total_minor(250, 4), 1000)

    def test_price_and_line_total_reject_database_integer_overflow(self):
        try:
            parse_money_minor("42949672.99", "CNY")
        except Exception as error:
            self.assertIsInstance(error, ValueError)
            self.assertIn("价格过大", str(error))
        else:
            self.fail("超出安全范围的产品价格不应被接受")

        try:
            parse_money_minor("1e1000", "CNY")
        except Exception as error:
            self.assertIsInstance(error, ValueError)
            self.assertIn("价格过大", str(error))
        else:
            self.fail("极端指数价格不应被接受")

        self.assertEqual(
            line_total_minor(4_294_967_298, 2_147_483_647),
            9_223_372_036_854_775_806,
        )
        with self.assertRaisesRegex(ValueError, "金额过大"):
            line_total_minor(4_294_967_299, 2_147_483_647)
