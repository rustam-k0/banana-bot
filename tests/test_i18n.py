import unittest

from banana_bot.i18n import LEGACY_BUTTONS, TEXTS, button_values


class LocalizationTests(unittest.TestCase):
    def test_languages_have_the_same_keys(self):
        self.assertEqual(set(TEXTS["EN"]), set(TEXTS["RU"]))

    def test_old_telegram_keyboards_remain_compatible(self):
        for key, old_labels in LEGACY_BUTTONS.items():
            self.assertTrue(old_labels.issubset(button_values(key)))

    def test_main_buttons_are_short_enough_for_mobile(self):
        keys = {
            "BTN_CHAT", "BTN_COMPLEX", "BTN_GENERATE", "BTN_EDIT",
            "BTN_FILE", "BTN_TRANSLATE", "BTN_NEW", "BTN_SETTINGS",
        }
        for language in TEXTS.values():
            for key in keys:
                self.assertLessEqual(len(language[key]), 22)


if __name__ == "__main__":
    unittest.main()
