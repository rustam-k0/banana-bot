import json
import unittest

from aiohttp.test_utils import make_mocked_request

from banana_bot.app import healthcheck


class HealthcheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthcheck_is_safe_and_ready(self):
        response = await healthcheck(make_mocked_request("GET", "/healthz"))
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "banana-bot")


if __name__ == "__main__":
    unittest.main()
