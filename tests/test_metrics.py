import unittest

from backend.app.services.cost import summarize_cost, summarize_execution
from backend.app.services.metrics import validate_billing_record, validate_publication_observation


class CostSummaryTests(unittest.TestCase):
    def test_missing_cost_is_unknown_not_zero(self):
        summary = summarize_cost({"trace": {"events": []}})
        self.assertEqual(summary["amount_status"], "not_reported")
        self.assertIsNone(summary["total"])
        self.assertEqual(summary["reported_total"], 0)

    def test_explicit_reported_zero_is_distinct_from_missing_cost(self):
        summary = summarize_cost({"trace": {"events": [{"type": "asset", "cost": 0, "cost_reported": True}]}})
        self.assertEqual(summary["amount_status"], "reported")
        self.assertEqual(summary["total"], 0)
        self.assertIsNone(summary["currency"])

    def test_partial_cost_is_incomplete_and_keeps_reported_subtotal(self):
        summary = summarize_cost({"trace": {"events": [
            {"type": "asset", "cost": 0.25, "cost_reported": True}, {"type": "llm", "model": "chat"},
        ]}})
        self.assertEqual(summary["amount_status"], "incomplete")
        self.assertIsNone(summary["total"])
        self.assertEqual(summary["reported_total"], 0.25)
        self.assertEqual(summary["missing_event_count"], 1)

    def test_execution_runs_report_status_and_last_duration(self):
        summary = summarize_execution({"execution_runs": [
            {"run_id": "run_1", "duration_seconds": 12.5, "status": "completed"},
            {"run_id": "run_2", "duration_seconds": 3.25, "status": "failed"},
        ]})
        self.assertEqual(summary["completed_run_count"], 1)
        self.assertEqual(summary["failed_run_count"], 1)
        self.assertEqual(summary["total_seconds"], 15.75)
        self.assertEqual(summary["last_seconds"], 3.25)


class BillingValidationTests(unittest.TestCase):
    def test_billing_record_requires_source_currency_and_valid_date(self):
        record = validate_billing_record({
            "amount": "0", "currency": "CNY", "source": "Provider invoice 2026-11",
            "captured_at": "2026-11-21",
        })
        self.assertEqual(record["amount"], 0)
        self.assertEqual(record["currency"], "CNY")
        for patch in (
            {"amount": -1, "currency": "CNY", "source": "invoice", "captured_at": "2026-11-21"},
            {"amount": "NaN", "currency": "CNY", "source": "invoice", "captured_at": "2026-11-21"},
            {"amount": 1, "currency": "usd", "source": "invoice", "captured_at": "2026-11-21"},
            {"amount": 1, "currency": "CNY", "source": "", "captured_at": "2026-11-21"},
            {"amount": 1, "currency": "CNY", "source": "invoice", "captured_at": "not-a-date"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_billing_record(patch)


class PublicationObservationValidationTests(unittest.TestCase):
    def test_observation_preserves_only_reported_metrics_including_zero(self):
        record = validate_publication_observation({
            "platform": "douyin", "post_url": "https://www.douyin.com/video/123",
            "published_at": "2026-11-20", "observed_at": "2026-11-21", "window_days": 1,
            "metrics": {"views": 0, "likes": 3},
        })
        self.assertEqual(record["metrics"], {"views": 0, "likes": 3})

    def test_observation_rejects_invalid_metrics_but_allows_unreported_fields(self):
        base = {
            "platform": "youtube", "post_url": "https://youtube.com/watch?v=abc",
            "published_at": "2026-11-20", "observed_at": "2026-11-21", "window_days": 7,
            "metrics": {"views": 5},
        }
        self.assertEqual(validate_publication_observation(base)["metrics"], {"views": 5})
        for metrics in ({}, {"views": -1}, {"views": True}, {"virality_score": 99}):
            with self.subTest(metrics=metrics), self.assertRaises(ValueError):
                validate_publication_observation({**base, "metrics": metrics})

    def test_observation_requires_http_post_url_and_valid_reporting_window(self):
        base = {
            "platform": "xiaohongshu", "post_url": "https://www.xiaohongshu.com/explore/abc",
            "published_at": "2026-11-20", "observed_at": "2026-11-21", "window_days": 7,
            "metrics": {"reach": 4},
        }
        for changes in ({"post_url": "javascript:alert(1)"}, {"window_days": 0}, {"platform": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_publication_observation({**base, **changes})


if __name__ == "__main__":
    unittest.main()
