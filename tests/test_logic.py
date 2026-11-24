"""v2 のロジックテスト (stdlib unittest のみ)。

実行: python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import Database  # noqa: E402
from app.ui import progress_bar, sanitize_channel_part  # noqa: E402
from cogs.leveling import calc_level, level_progress, xp_threshold  # noqa: E402


class TestLevelMath(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(xp_threshold(0), 0)
        self.assertEqual(xp_threshold(1), 155)
        self.assertEqual(xp_threshold(2), 220)
        self.assertEqual(xp_threshold(3), 295)

    def test_calc_level(self):
        self.assertEqual(calc_level(0), 0)
        self.assertEqual(calc_level(154), 0)
        self.assertEqual(calc_level(155), 1)
        self.assertEqual(calc_level(219), 1)
        self.assertEqual(calc_level(220), 2)
        self.assertEqual(calc_level(1000), 9)

    def test_level_progress_level0(self):
        level, fraction, remaining = level_progress(0)
        self.assertEqual(level, 0)
        self.assertEqual(fraction, 0.0)
        self.assertEqual(remaining, 155)

    def test_level_progress_mid(self):
        level, fraction, remaining = level_progress(155)
        self.assertEqual(level, 1)
        self.assertEqual(fraction, 0.0)
        self.assertEqual(remaining, 65)


class TestUiHelpers(unittest.TestCase):
    def test_progress_bar(self):
        self.assertEqual(progress_bar(0), "░" * 20)
        self.assertEqual(progress_bar(1), "█" * 20)
        self.assertEqual(progress_bar(0.5), "█" * 10 + "░" * 10)
        self.assertEqual(len(progress_bar(0.3, width=10)), 10)
        # 範囲外はクランプされる
        self.assertEqual(progress_bar(-1), "░" * 20)
        self.assertEqual(progress_bar(2), "█" * 20)

    def test_sanitize_channel_part(self):
        self.assertEqual(sanitize_channel_part("Hello World!"), "hello-world")
        self.assertEqual(sanitize_channel_part("!!!"), "user")
        self.assertEqual(sanitize_channel_part(""), "user")
        self.assertEqual(sanitize_channel_part("たろう"), "たろう")
        long_name = sanitize_channel_part("a" * 100)
        self.assertLessEqual(len(long_name), 30)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_round_trip(self):
        self.assertIsNone(self.db.get_setting(1, "k"))
        self.assertEqual(self.db.get_setting(1, "k", 42), 42)
        self.db.set_setting(1, "k", 123)
        self.assertEqual(self.db.get_setting(1, "k"), 123)
        self.db.set_setting(1, "k", {"a": [1, 2]})
        self.assertEqual(self.db.get_setting(1, "k"), {"a": [1, 2]})
        # ギルドが違えば別
        self.assertIsNone(self.db.get_setting(2, "k"))

    def test_xp_read_does_not_create_row(self):
        data = self.db.get_xp(10, 20)  # create=False が既定
        self.assertEqual(data["xp"], 0)
        self.assertEqual(self.db.count_users(20), 0)

    def test_xp_upsert_rank_and_paging(self):
        self.db.upsert_xp(1, 100, 500, 4, 1.0)
        self.db.upsert_xp(2, 100, 900, 6, 1.0)
        self.db.upsert_xp(3, 100, 900, 6, 1.0)
        self.db.upsert_xp(4, 100, 100, 0, 1.0)

        self.assertEqual(self.db.get_xp(1, 100)["xp"], 500)
        # 順位: 900 が2人で同点1位、500 が3位
        self.assertEqual(self.db.get_rank(2, 100), 1)
        self.assertEqual(self.db.get_rank(1, 100), 3)
        self.assertEqual(self.db.get_rank(4, 100), 4)

        page1 = self.db.get_top_users(100, limit=2, offset=0)
        page2 = self.db.get_top_users(100, limit=2, offset=2)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertEqual({u for u, _, _ in page1}, {2, 3})
        self.assertEqual({u for u, _, _ in page2}, {1, 4})

        # upsert で更新できる
        self.db.upsert_xp(4, 100, 2000, 10, 2.0)
        self.assertEqual(self.db.get_rank(4, 100), 1)

        self.db.delete_xp(4, 100)
        self.assertEqual(self.db.count_users(100), 3)

    def test_ticket_crud(self):
        self.db.create_ticket(
            channel_id=555,
            guild_id=100,
            owner_id=7,
            ticket_type="question",
            subject="ログインできません",
            now=1000.0,
        )
        row = self.db.get_ticket(555)
        self.assertEqual(row["owner_id"], 7)
        self.assertEqual(row["ticket_type"], "question")
        self.assertIsNone(row["assignee_id"])
        self.assertEqual(row["last_activity_at"], 1000.0)

        by_owner = self.db.get_open_ticket_by_owner(100, 7)
        self.assertEqual(by_owner["channel_id"], 555)

        self.db.set_ticket_assignee(555, 42)
        self.assertEqual(self.db.get_ticket(555)["assignee_id"], 42)
        self.db.set_ticket_assignee(555, None)
        self.assertIsNone(self.db.get_ticket(555)["assignee_id"])

        self.db.set_ticket_message_id(555, 999)
        self.assertEqual(self.db.get_ticket(555)["message_id"], 999)

        self.db.touch_ticket(555, 2000.0)
        self.assertEqual(self.db.get_ticket(555)["last_activity_at"], 2000.0)

        self.assertEqual(len(self.db.list_tickets(100)), 1)
        self.db.delete_ticket(555)
        self.assertIsNone(self.db.get_ticket(555))
        self.assertIsNone(self.db.get_open_ticket_by_owner(100, 7))


if __name__ == "__main__":
    unittest.main()
