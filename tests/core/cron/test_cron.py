from datetime import datetime
import unittest

from juice_agents.core.cron.cron import compute_next_cron_run, parse_cron_expression


class CronParserTests(unittest.TestCase):
    def test_parse_valid_five_field_expression(self):
        fields = parse_cron_expression("*/5 9-17 * * 1-5")

        self.assertIsNotNone(fields)
        assert fields is not None
        self.assertEqual(fields.minute[:3], (0, 5, 10))
        self.assertIn(9, fields.hour)
        self.assertIn(17, fields.hour)
        self.assertEqual(fields.day_of_week, (1, 2, 3, 4, 5))

    def test_rejects_invalid_expression(self):
        self.assertIsNone(parse_cron_expression("*/0 * * * *"))
        self.assertIsNone(parse_cron_expression("61 * * * *"))
        self.assertIsNone(parse_cron_expression("* * *"))

    def test_next_run_is_strictly_after_anchor(self):
        fields = parse_cron_expression("*/5 * * * *")
        assert fields is not None

        next_run = compute_next_cron_run(fields, datetime(2026, 5, 15, 10, 5, 0))

        self.assertEqual(next_run, datetime(2026, 5, 15, 10, 10))

    def test_day_of_month_and_day_of_week_use_or_semantics(self):
        fields = parse_cron_expression("0 9 15 * 1")
        assert fields is not None

        next_run = compute_next_cron_run(fields, datetime(2026, 5, 14, 10, 0, 0))

        self.assertEqual(next_run, datetime(2026, 5, 15, 9, 0))


if __name__ == "__main__":
    unittest.main()
