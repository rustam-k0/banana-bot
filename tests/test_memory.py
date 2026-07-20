import unittest

from banana_bot.memory import ConversationMemory


class MemoryTests(unittest.TestCase):
    def test_keeps_exactly_last_eight_messages_and_summary(self):
        memory = ConversationMemory(message_limit=8)
        for index in range(10):
            memory.add(1, "user", f"message-{index}")
        context = memory.context(1)
        messages = [item for item in context if not item["content"].startswith("Conversation summary:")]
        self.assertEqual(len(messages), 8)
        self.assertEqual(messages[0]["content"], "message-2")
        self.assertIn("message-0", context[0]["content"])

    def test_captures_explicit_facts_and_clear(self):
        memory = ConversationMemory()
        memory.add(5, "user", "Запомни: мой часовой пояс UTC+2")
        self.assertIn("UTC+2", memory.context(5)[0]["content"])
        memory.clear(5)
        self.assertEqual(memory.context(5), [])


if __name__ == "__main__":
    unittest.main()
