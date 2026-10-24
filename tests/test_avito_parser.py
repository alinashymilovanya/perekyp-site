import unittest

from iphone_monitor.avito_parser import parse_card


class AvitoParserTests(unittest.TestCase):
    def test_parses_price_condition_battery_and_storage(self):
        card = {
            "id": "i123456789",
            "title": "iPhone 13 Pro Max, 256 ГБ, SIM + eSIM в Москве",
            "url": "https://www.avito.ru/moskva/telefony/item_123456789",
            "text": (
                "Ещё\n3 фото\n"
                "iPhone 13 Pro Max, 256 ГБ, SIM + eSIM\n"
                "25 000 ₽\nХорошее\nМосква\n"
                "Аккумулятор 84%. Менялась задняя крышка."
            ),
            "images": ["https://img.example/1.jpg"],
        }

        result = parse_card(card)

        self.assertEqual(result.item_id, "123456789")
        self.assertEqual(result.price_rub, 25_000)
        self.assertEqual(result.model, "iPhone 13 Pro Max")
        self.assertEqual(result.storage_gb, 256)
        self.assertEqual(result.condition, "Хорошее")
        self.assertEqual(result.battery_percent, 84)
        self.assertEqual(result.images, ("https://img.example/1.jpg",))

    def test_handles_missing_optional_data(self):
        result = parse_card(
            {
                "id": "i987",
                "title": "iPhone 11",
                "url": "https://www.avito.ru/item/987",
                "text": "iPhone 11\n15 000 ₽\n",
                "images": [],
            }
        )

        self.assertEqual(result.item_id, "987")
        self.assertEqual(result.price_rub, 15_000)
        self.assertIsNone(result.storage_gb)
        self.assertIsNone(result.battery_percent)
