import unittest

from aiogram.fsm.storage.memory import MemoryStorage

from banana_bot.app import build_dispatcher
from banana_bot.config import load_config
from banana_bot.memory import ConversationMemory
from banana_bot.observability import Metrics


class TelegramWiringTests(unittest.TestCase):
    def test_all_router_groups_are_registered(self):
        config = load_config({"TELEGRAM_BOT_TOKEN": "t", "OPENAI_API_KEY": "o", "XAI_API_KEY": "x", "GOOGLE_API_KEY": "g"}, validate=True)
        dispatcher = build_dispatcher(config, object(), ConversationMemory(), Metrics(), MemoryStorage())
        names = {router.name for router in dispatcher.sub_routers}
        self.assertEqual(names, {"admin", "common", "text", "media"})


if __name__ == "__main__":
    unittest.main()
