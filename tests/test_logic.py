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
from cogs.leveling import (  # noqa: E402
    RECENT_CONTENT_HASH_LIMIT,
    calc_level,
    content_hash,
    level_progress,
    next_recent_hashes,
    normalize_content,
    validate_xp_config,
    xp_block_reason,
    xp_threshold,
)


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


XP_CONFIG = {
    "xp_min": 10,
    "xp_max": 25,
    "xp_cooldown_seconds": 30,
    "xp_min_length": 3,
}


class TestXpAntiSpam(unittest.TestCase):
    def test_passing_message(self):
        self.assertIsNone(
            xp_block_reason("今日はいい天気ですね", [], 10000.0, 0.0, XP_CONFIG)
        )

    def test_cooldown(self):
        self.assertEqual(
            xp_block_reason(
                "今日はいい天気ですね", [], 100.0, 90.0, XP_CONFIG
            ),
            "cooldown",
        )
        # ちょうどクールダウン経過後は通る
        self.assertIsNone(
            xp_block_reason(
                "今日はいい天気ですね", [], 100.0, 70.0, XP_CONFIG
            )
        )

    def test_no_text(self):
        self.assertEqual(
            xp_block_reason("", [], 10000.0, 0.0, XP_CONFIG), "no_text"
        )
        self.assertEqual(
            xp_block_reason("  \n\t ", [], 10000.0, 0.0, XP_CONFIG), "no_text"
        )

    def test_too_short(self):
        self.assertEqual(
            xp_block_reason("ab", [], 10000.0, 0.0, XP_CONFIG), "too_short"
        )
        self.assertIsNone(
            xp_block_reason("abc", [], 10000.0, 0.0, XP_CONFIG)
        )

    def test_duplicate_direct(self):
        recent = [content_hash("hello world")]
        self.assertEqual(
            xp_block_reason("hello world", recent, 10000.0, 0.0, XP_CONFIG),
            "duplicate",
        )

    def test_duplicate_a_b_a(self):
        # A → B → A の3通目 (A の再送) は履歴に残っているので弾かれる
        recent = []
        recent = next_recent_hashes(recent, content_hash("メッセージAです"))
        recent = next_recent_hashes(recent, content_hash("別のメッセージB"))
        self.assertEqual(
            xp_block_reason("メッセージAです", recent, 10000.0, 0.0, XP_CONFIG),
            "duplicate",
        )
        # 履歴にない新しい内容は通る
        self.assertIsNone(
            xp_block_reason("新しいメッセージC", recent, 10000.0, 0.0, XP_CONFIG)
        )

    def test_history_eviction(self):
        # 直近5件から押し出された古い内容は再度カウントされる
        recent = next_recent_hashes([], content_hash("メッセージAです"))
        for i in range(RECENT_CONTENT_HASH_LIMIT):
            recent = next_recent_hashes(
                recent, content_hash(f"別の発言その{i}です")
            )
        self.assertEqual(len(recent), RECENT_CONTENT_HASH_LIMIT)
        self.assertNotIn(content_hash("メッセージAです"), recent)
        self.assertIsNone(
            xp_block_reason("メッセージAです", recent, 10000.0, 0.0, XP_CONFIG)
        )

    def test_normalization_and_hash_stability(self):
        self.assertEqual(normalize_content("  Foo\tBar\n Baz  "), "foo bar baz")
        self.assertEqual(
            content_hash("  Foo\tBar\nBaz "), content_hash("foo bar baz")
        )
        self.assertNotEqual(content_hash("foo bar"), content_hash("foo baz"))


class TestXpConfigValidation(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(validate_xp_config(10, 25, 30, 3))
        self.assertIsNone(validate_xp_config(0, 0, 0, 0))
        self.assertIsNone(validate_xp_config(0, 10000, 3600, 200))

    def test_invalid(self):
        self.assertIsNotNone(validate_xp_config(26, 25, 30, 3))  # min > max
        self.assertIsNotNone(validate_xp_config(-1, 25, 30, 3))
        self.assertIsNotNone(validate_xp_config(10, 10001, 30, 3))
        self.assertIsNotNone(validate_xp_config(10, 25, -1, 3))
        self.assertIsNotNone(validate_xp_config(10, 25, 3601, 3))
        self.assertIsNotNone(validate_xp_config(10, 25, 30, -1))
        self.assertIsNotNone(validate_xp_config(10, 25, 30, 201))


class TestDatabaseMigration(unittest.TestCase):
    def test_user_xp_recent_hashes_migration(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            with conn:
                # v2 初期の user_xp (recent_content_hashes 列なし)
                conn.execute(
                    """
                    CREATE TABLE user_xp (
                        user_id INTEGER NOT NULL,
                        guild_id INTEGER NOT NULL,
                        xp INTEGER NOT NULL DEFAULT 0,
                        level INTEGER NOT NULL DEFAULT 0,
                        last_message_time REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, guild_id)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO user_xp VALUES (7, 100, 500, 4, 1.5)"
                )
            conn.close()

            db = Database(path)

            conn = sqlite3.connect(path)
            columns = [
                row[1] for row in conn.execute("PRAGMA table_info(user_xp)")
            ]
            conn.close()
            self.assertIn("recent_content_hashes", columns)

            # 既存行と XP が保持されている
            data = db.get_xp(7, 100)
            self.assertEqual(data["xp"], 500)
            self.assertEqual(data["level"], 4)
            self.assertEqual(data["recent_content_hashes"], [])

            # ハッシュ履歴が往復できる
            db.upsert_xp(
                7, 100, 520, 4, 2.0, recent_content_hashes=["abc", "def"]
            )
            data = db.get_xp(7, 100)
            self.assertEqual(data["xp"], 520)
            self.assertEqual(data["recent_content_hashes"], ["abc", "def"])


class TestRulesText(unittest.TestCase):
    def test_build_rules_text_is_plain_markdown(self):
        from cogs.verify import build_rules_text

        text = build_rules_text()
        self.assertTrue(text.startswith("# "))
        self.assertIn("## ", text)
        self.assertNotIn("\x00", text)

    def test_split_message(self):
        from cogs.verify import split_message

        self.assertEqual(split_message("短い文"), ["短い文"])

        long_text = "\n".join(f"行{i} " + "あ" * 90 for i in range(60))
        chunks = split_message(long_text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)
        self.assertEqual("\n".join(chunks), long_text)


if __name__ == "__main__":
    unittest.main()
