import json
import unittest

from banana_bot.observability import Metrics, log_event


class ObservabilityTests(unittest.TestCase):
    def test_structured_log_drops_sensitive_content_fields(self):
        with self.assertLogs("banana_bot", level="INFO") as captured:
            log_event("request", provider="openai", prompt="private", api_key="secret")
        payload = json.loads(captured.records[0].getMessage())
        self.assertEqual(payload["provider"], "openai")
        self.assertNotIn("prompt", payload)
        self.assertNotIn("api_key", payload)

    def test_stats_include_latency_tokens_errors_and_cost(self):
        metrics = Metrics()
        metrics.user_activity(10)
        metrics.record("openai", "model", 120, 1000, 500, error=True)
        report = metrics.render()
        self.assertIn("Active users: 1", report)
        self.assertIn("1 errors", report)
        self.assertIn("1000/500 tokens", report)
        self.assertIn("≈$", report)


if __name__ == "__main__":
    unittest.main()
