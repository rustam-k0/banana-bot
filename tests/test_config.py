import unittest

from banana_bot.config import ConfigError, load_config


BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "token",
    "OPENAI_API_KEY": "openai",
    "XAI_API_KEY": "xai",
    "GOOGLE_API_KEY": "google",
}


class ConfigTests(unittest.TestCase):
    def test_default_routing_contract(self):
        config = load_config(BASE_ENV, validate=True)
        self.assertEqual(config.chat_fast_chain[0].model, "gpt-5.6-luna")
        self.assertEqual(config.chat_balanced_chain[0].model, "gpt-5.6-terra")
        self.assertEqual(config.chat_complex_chain[0].model, "gpt-5.6-sol")
        self.assertEqual(config.image_pro_chain[0].model, "gpt-image-2")
        self.assertEqual(config.image_fast_chain[0].provider, "xai")
        self.assertEqual(config.speech_chain[0].model, "tts-1")

    def test_existing_environment_names_remain_supported(self):
        config = load_config({**BASE_ENV, "ALLOWED_USERS": "1, 2,invalid", "PORT": "9000"})
        self.assertEqual(config.allowed_users, frozenset({1, 2}))
        self.assertEqual(config.port, 9000)

    def test_chain_is_configurable(self):
        config = load_config({**BASE_ENV, "CHAT_FAST_CHAIN": "xai:custom,google:backup"})
        self.assertEqual([(item.provider, item.model) for item in config.chat_fast_chain], [("xai", "custom"), ("google", "backup")])

    def test_validation_rejects_unavailable_chain(self):
        with self.assertRaises(ConfigError):
            load_config({"TELEGRAM_BOT_TOKEN": "token", "OPENAI_API_KEY": "key", "IMAGE_FAST_CHAIN": "xai:model"}, validate=True)

    def test_validation_names_missing_telegram_token(self):
        with self.assertRaisesRegex(ConfigError, "TELEGRAM_BOT_TOKEN"):
            load_config({**BASE_ENV, "TELEGRAM_BOT_TOKEN": ""}, validate=True)


if __name__ == "__main__":
    unittest.main()
