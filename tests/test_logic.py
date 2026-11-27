"""v2 のロジックテスト (stdlib unittest のみ)。

実行: python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import settings  # noqa: E402
from app.db import Database, next_vote  # noqa: E402
from app.ui import progress_bar, sanitize_channel_part  # noqa: E402
from cogs.automod import (  # noqa: E402
    content_violation,
    find_invite,
    find_ngword,
    punishment_for_count,
    spam_reason,
)
from cogs.leveling import (  # noqa: E402
    RECENT_CONTENT_HASH_LIMIT,
    VC_TICK_SECONDS,
    advance_vc_sessions,
    calc_level,
    content_hash,
    level_progress,
    next_recent_hashes,
    normalize_content,
    validate_xp_config,
    vc_eligible,
    xp_block_reason,
    xp_threshold,
)
from cogs.bump import (  # noqa: E402
    DISBOARD_BOT_ID,
    extract_bumper_id,
    is_bump_success,
)
from cogs.feedback import (  # noqa: E402
    FeedbackStatusButton,
    FeedbackVoteButton,
    can_transition,
)
from cogs.giveaway import (  # noqa: E402
    GiveawayButton,
    draw_winners,
    parse_duration,
)
from cogs.roles import (  # noqa: E402
    RoleButton,
    panel_add_error,
    toggle_actions,
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


class TestAutomodPunishment(unittest.TestCase):
    def test_ladder(self):
        self.assertEqual(punishment_for_count(1), "warn")
        self.assertEqual(punishment_for_count(10), "warn")
        self.assertEqual(punishment_for_count(11), "timeout")
        self.assertEqual(punishment_for_count(12), "kick")
        self.assertEqual(punishment_for_count(13), "ban")
        self.assertEqual(punishment_for_count(30), "ban")


class TestAutomodSpam(unittest.TestCase):
    def test_flood(self):
        now = 1000.0
        # 10秒以内に5件 (内容はすべて別)
        events = [(now - i, f"hash{i}") for i in range(5)]
        self.assertEqual(spam_reason(events, now), "flood")

    def test_repeat(self):
        now = 1000.0
        # 60秒以内に同一内容が3件 (連投の件数には届かない)
        events = [(now - 50, "same"), (now - 30, "same"), (now, "same")]
        self.assertEqual(spam_reason(events, now), "repeat")

    def test_normal_traffic(self):
        now = 1000.0
        events = [(now - 100, "a"), (now - 50, "b"), (now, "c")]
        self.assertIsNone(spam_reason(events, now))
        self.assertIsNone(spam_reason([], now))

    def test_window_expiry(self):
        now = 1000.0
        # 5件あっても全て10秒より前なら連投にならない
        events = [(now - 20 - i, f"hash{i}") for i in range(5)]
        self.assertIsNone(spam_reason(events, now))
        # 同一内容3件でも全て60秒より前なら繰り返しにならない
        events = [(now - 120, "x"), (now - 90, "x"), (now - 61, "x")]
        self.assertIsNone(spam_reason(events, now))


class TestAutomodNgWord(unittest.TestCase):
    def test_match_with_normalization(self):
        words = ["bad word", "バカ"]
        # 大文字・余分な空白があっても正規化されて一致する
        self.assertEqual(
            find_ngword("this is a BAD   WORD here", words), "bad word"
        )
        self.assertEqual(find_ngword("おまえバカか", words), "バカ")

    def test_no_match(self):
        self.assertIsNone(find_ngword("普通のメッセージです", ["バカ"]))
        self.assertIsNone(find_ngword("", ["バカ"]))
        self.assertIsNone(find_ngword("何か言いたい", []))
        self.assertIsNone(find_ngword("何か言いたい", None))


class TestAutomodInvite(unittest.TestCase):
    def test_matches_three_forms(self):
        self.assertEqual(find_invite("join us discord.gg/abc123"), "discord.gg")
        self.assertEqual(
            find_invite("https://discord.com/invite/abc123"),
            "discord.com/invite",
        )
        self.assertEqual(
            find_invite("https://discordapp.com/invite/abc123"),
            "discordapp.com/invite",
        )
        # 大文字でも一致する
        self.assertEqual(find_invite("DISCORD.GG/XYZ"), "discord.gg")

    def test_no_match(self):
        self.assertIsNone(find_invite("https://example.com/invite/abc"))
        # スラッシュなしの言及だけでは対象にしない
        self.assertIsNone(find_invite("discord.gg というサイトがある"))
        self.assertIsNone(find_invite("普通の文章です"))
        self.assertIsNone(find_invite(""))


class TestAutomodContentViolation(unittest.TestCase):
    def test_mention_threshold(self):
        self.assertIsNone(content_violation("こんにちは", [], 4, 5, True))
        result = content_violation("こんにちは", [], 5, 5, True)
        self.assertEqual(result[0], "mention")
        # 上限 0 は検知無効
        self.assertIsNone(content_violation("こんにちは", [], 99, 0, True))

    def test_invite_toggle(self):
        result = content_violation("discord.gg/abc", [], 0, 5, True)
        self.assertEqual(result[0], "invite")
        self.assertEqual(result[1], "discord.gg")
        self.assertIsNone(content_violation("discord.gg/abc", [], 0, 5, False))

    def test_ngword_priority(self):
        result = content_violation("バカ discord.gg/abc", ["バカ"], 0, 5, True)
        self.assertEqual(result, ("ngword", "バカ"))


class TestAutomodAdChannel(unittest.TestCase):
    def test_invite_allowed_in_ad_channel(self):
        # 宣伝許可チャンネルでは招待リンクだけ素通りする
        self.assertIsNone(
            content_violation(
                "discord.gg/abc", [], 0, 5, True, allow_invites=True
            )
        )

    def test_ngword_still_flagged_in_ad_channel(self):
        result = content_violation(
            "死ね discord.gg/abc",
            settings.DEFAULT_NGWORDS,
            0,
            5,
            True,
            allow_invites=True,
        )
        self.assertEqual(result, ("ngword", "死ね"))

    def test_mention_still_flagged_in_ad_channel(self):
        result = content_violation(
            "こんにちは", [], 5, 5, True, allow_invites=True
        )
        self.assertEqual(result[0], "mention")


class TestAutomodNgwordDefaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_defaults_when_unset(self):
        self.assertEqual(
            settings.get_automod_ngwords(1), settings.DEFAULT_NGWORDS
        )
        self.assertFalse(settings.automod_ngwords_customized(1))

    def test_default_words_match(self):
        words = settings.get_automod_ngwords(1)
        self.assertEqual(find_ngword("お前死ね", words), "死ね")
        self.assertEqual(
            find_ngword("FREE NITRO for everyone", words), "free nitro"
        )
        self.assertIsNone(find_ngword("今日はいい天気", words))

    def test_defaults_returned_as_copy(self):
        words = settings.get_automod_ngwords(1)
        words.append("zzz")
        self.assertNotIn("zzz", settings.DEFAULT_NGWORDS)

    def test_custom_list_overrides_defaults(self):
        settings.set_value(1, settings.AUTOMOD_NGWORDS, ["abc"])
        self.assertEqual(settings.get_automod_ngwords(1), ["abc"])
        self.assertTrue(settings.automod_ngwords_customized(1))

    def test_empty_list_means_no_words(self):
        settings.set_value(1, settings.AUTOMOD_NGWORDS, [])
        self.assertEqual(settings.get_automod_ngwords(1), [])


class TestInfractions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_count_recent_clear(self):
        self.db.add_infraction(100, 7, "ngword", "バカ", "warn", 1000.0)
        self.db.add_infraction(100, 7, "flood", "スパム（連投）", "timeout", 2000.0)
        self.db.add_infraction(100, 8, "invite", "discord.gg", "warn", 2000.0)
        self.db.add_infraction(200, 7, "ngword", "バカ", "warn", 2000.0)

        # ギルド・ユーザー単位で数える
        self.assertEqual(self.db.count_infractions(100, 7, 0.0), 2)
        self.assertEqual(self.db.count_infractions(100, 8, 0.0), 1)
        self.assertEqual(self.db.count_infractions(200, 7, 0.0), 1)
        # since で期間を絞れる
        self.assertEqual(self.db.count_infractions(100, 7, 1500.0), 1)
        self.assertEqual(self.db.count_infractions(100, 7, 2000.0), 1)
        self.assertEqual(self.db.count_infractions(100, 7, 2000.1), 0)

        recent = self.db.recent_infractions(100, 7, 5)
        self.assertEqual(len(recent), 2)
        # 新しい順
        self.assertEqual(recent[0]["reason"], "flood")
        self.assertEqual(recent[0]["punishment"], "timeout")
        self.assertEqual(recent[1]["reason"], "ngword")
        self.assertEqual(recent[1]["detail"], "バカ")
        # limit が効く
        self.assertEqual(len(self.db.recent_infractions(100, 7, 1)), 1)

        removed = self.db.clear_infractions(100, 7)
        self.assertEqual(removed, 2)
        self.assertEqual(self.db.count_infractions(100, 7, 0.0), 0)
        # 他のユーザー・ギルドには影響しない
        self.assertEqual(self.db.count_infractions(100, 8, 0.0), 1)
        self.assertEqual(self.db.count_infractions(200, 7, 0.0), 1)

    def test_pre_automod_schema_db_opens(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            with conn:
                # automod 導入前のスキーマ (infractions テーブルなし)
                conn.executescript(
                    """
                    CREATE TABLE guild_settings (
                        guild_id INTEGER NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT,
                        PRIMARY KEY (guild_id, key)
                    );
                    CREATE TABLE user_xp (
                        user_id INTEGER NOT NULL,
                        guild_id INTEGER NOT NULL,
                        xp INTEGER NOT NULL DEFAULT 0,
                        level INTEGER NOT NULL DEFAULT 0,
                        last_message_time REAL NOT NULL DEFAULT 0,
                        recent_content_hashes TEXT,
                        PRIMARY KEY (user_id, guild_id)
                    );
                    CREATE TABLE tickets (
                        channel_id INTEGER PRIMARY KEY,
                        guild_id INTEGER NOT NULL,
                        owner_id INTEGER NOT NULL,
                        assignee_id INTEGER,
                        ticket_type TEXT NOT NULL,
                        subject TEXT,
                        message_id INTEGER,
                        created_at REAL NOT NULL,
                        last_activity_at REAL NOT NULL
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO guild_settings VALUES (100, 'k', '123')"
                )
            conn.close()

            db = Database(path)

            conn = sqlite3.connect(path)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            conn.close()
            self.assertIn("infractions", tables)

            # 既存データが保持され、新テーブルも使える
            self.assertEqual(db.get_setting(100, "k"), 123)
            self.assertEqual(db.count_infractions(100, 7, 0.0), 0)
            db.add_infraction(100, 7, "ngword", "バカ", "warn", 1000.0)
            self.assertEqual(db.count_infractions(100, 7, 0.0), 1)


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


class TestRolePanelToggleActions(unittest.TestCase):
    PANEL = [10, 20, 30]

    def test_add_when_missing(self):
        to_add, to_remove = toggle_actions([], self.PANEL, 10, False)
        self.assertEqual(to_add, [10])
        self.assertEqual(to_remove, [])

    def test_remove_when_present(self):
        to_add, to_remove = toggle_actions([10, 20], self.PANEL, 10, False)
        self.assertEqual(to_add, [])
        self.assertEqual(to_remove, [10])

    def test_non_exclusive_keeps_other_panel_roles(self):
        to_add, to_remove = toggle_actions([20], self.PANEL, 10, False)
        self.assertEqual(to_add, [10])
        self.assertEqual(to_remove, [])

    def test_exclusive_removes_other_panel_roles_on_add(self):
        to_add, to_remove = toggle_actions([20, 30], self.PANEL, 10, True)
        self.assertEqual(to_add, [10])
        self.assertEqual(to_remove, [20, 30])

    def test_exclusive_remove_does_not_touch_others(self):
        to_add, to_remove = toggle_actions([10, 20], self.PANEL, 10, True)
        self.assertEqual(to_add, [])
        self.assertEqual(to_remove, [10])

    def test_clicked_role_not_in_panel(self):
        self.assertEqual(toggle_actions([], self.PANEL, 99, False), ([], []))
        self.assertEqual(toggle_actions([99], self.PANEL, 99, True), ([], []))

    def test_non_panel_roles_never_touched(self):
        # 999 はパネル外のロール: 排他モードでも外さない
        to_add, to_remove = toggle_actions([999, 20], self.PANEL, 10, True)
        self.assertEqual(to_add, [10])
        self.assertEqual(to_remove, [20])


class TestRolePanelAddError(unittest.TestCase):
    def test_ok(self):
        self.assertIsNone(panel_add_error([10], 20))
        self.assertIsNone(panel_add_error([], 10))

    def test_duplicate(self):
        self.assertEqual(panel_add_error([10, 20], 20), "duplicate")

    def test_full(self):
        full = list(range(settings.MAX_ROLE_PANEL_ROLES))
        self.assertEqual(panel_add_error(full, 9999), "full")
        # 上限ちょうど - 1 件なら追加できる
        almost = list(range(settings.MAX_ROLE_PANEL_ROLES - 1))
        self.assertIsNone(panel_add_error(almost, 9999))


class TestRolePanelSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_role_ids_default_empty(self):
        self.assertEqual(settings.get_role_panel_role_ids(1), [])

    def test_role_ids_round_trip_preserves_order(self):
        settings.set_value(1, settings.ROLE_PANEL_ROLE_IDS, [30, 10, 20])
        self.assertEqual(settings.get_role_panel_role_ids(1), [30, 10, 20])
        # ギルドごとに独立
        self.assertEqual(settings.get_role_panel_role_ids(2), [])

    def test_role_ids_corrupt_fallback(self):
        settings.set_value(1, settings.ROLE_PANEL_ROLE_IDS, "not-a-list")
        self.assertEqual(settings.get_role_panel_role_ids(1), [])
        # 混在した要素は数値化できるものだけ残る
        settings.set_value(1, settings.ROLE_PANEL_ROLE_IDS, [10, "x", "20"])
        self.assertEqual(settings.get_role_panel_role_ids(1), [10, 20])

    def test_exclusive_default_and_round_trip(self):
        self.assertFalse(settings.get_role_panel_exclusive(1))
        settings.set_value(1, settings.ROLE_PANEL_EXCLUSIVE, True)
        self.assertTrue(settings.get_role_panel_exclusive(1))
        settings.set_value(1, settings.ROLE_PANEL_EXCLUSIVE, False)
        self.assertFalse(settings.get_role_panel_exclusive(1))

    def test_exclusive_corrupt_fallback(self):
        settings.set_value(1, settings.ROLE_PANEL_EXCLUSIVE, "yes")
        self.assertFalse(settings.get_role_panel_exclusive(1))


class TestRoleButtonDynamicItem(unittest.TestCase):
    def test_custom_id_and_template(self):
        button = RoleButton(123456, label="テスト")
        self.assertEqual(button.custom_id, "v2:role:123456")
        self.assertEqual(button.role_id, 123456)
        match = button.template.fullmatch("v2:role:789")
        self.assertIsNotNone(match)
        self.assertEqual(match["role_id"], "789")
        # 他のパネルの custom_id にはマッチしない
        self.assertIsNone(button.template.fullmatch("v2:agree"))
        self.assertIsNone(button.template.fullmatch("v2:role:abc"))


class TestGiveawayParseDuration(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_duration("30s"), 30)
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1d"), 86400)
        # 24h と 1d は等しい
        self.assertEqual(parse_duration("24h"), 86400)

    def test_case_and_whitespace(self):
        self.assertEqual(parse_duration(" 10M "), 600)
        self.assertEqual(parse_duration("2H"), 7200)
        self.assertEqual(parse_duration(" 1d"), 86400)

    def test_range_boundaries(self):
        self.assertEqual(parse_duration("10s"), 10)
        self.assertEqual(parse_duration("30d"), 30 * 24 * 3600)

    def test_out_of_range(self):
        self.assertIsNone(parse_duration("9s"))
        self.assertIsNone(parse_duration("0m"))
        self.assertIsNone(parse_duration("31d"))

    def test_invalid(self):
        self.assertIsNone(parse_duration(""))
        self.assertIsNone(parse_duration("abc"))
        self.assertIsNone(parse_duration("10"))
        self.assertIsNone(parse_duration("10x"))
        self.assertIsNone(parse_duration("1.5h"))
        self.assertIsNone(parse_duration("-5m"))
        self.assertIsNone(parse_duration("10mm"))
        self.assertIsNone(parse_duration("10分"))
        self.assertIsNone(parse_duration(None))


class TestGiveawayDrawWinners(unittest.TestCase):
    def test_seeded_draw(self):
        import random

        entries = [101, 102, 103, 104, 105, 106]
        winners = draw_winners(entries, 3, rng=random.Random(42))
        self.assertEqual(len(winners), 3)
        # 重複なし・全員が参加者の中から選ばれている
        self.assertEqual(len(set(winners)), 3)
        self.assertTrue(set(winners) <= set(entries))
        # 同じシードでは再現する
        again = draw_winners(entries, 3, rng=random.Random(42))
        self.assertEqual(winners, again)

    def test_more_winners_than_entries(self):
        self.assertEqual(sorted(draw_winners([7, 8], 5)), [7, 8])

    def test_all_entries_win_when_equal(self):
        self.assertEqual(sorted(draw_winners([1, 2, 3], 3)), [1, 2, 3])

    def test_empty_entries(self):
        self.assertEqual(draw_winners([], 3), [])
        self.assertEqual(draw_winners(None, 3), [])


class TestGiveawayDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _create(self, guild_id=100, ends_at=2000.0, now=1000.0):
        return self.db.create_giveaway(
            guild_id, 555, 999, "ダイヤ装備", 2, ends_at, now
        )

    def test_create_get_round_trip(self):
        gid = self._create()
        row = self.db.get_giveaway(gid)
        self.assertEqual(row["guild_id"], 100)
        self.assertEqual(row["channel_id"], 555)
        self.assertIsNone(row["message_id"])
        self.assertEqual(row["host_id"], 999)
        self.assertEqual(row["prize"], "ダイヤ装備")
        self.assertEqual(row["winner_count"], 2)
        self.assertEqual(row["ends_at"], 2000.0)
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["winners"], [])
        self.assertIsNone(self.db.get_giveaway(99999))

        self.db.set_giveaway_message(gid, 777)
        self.assertEqual(self.db.get_giveaway(gid)["message_id"], 777)

    def test_toggle_entry(self):
        gid = self._create()
        # 参加 → True、もう一度 → 取り消しで False
        self.assertTrue(self.db.toggle_entry(gid, 7, 1000.0))
        self.assertEqual(self.db.count_entries(gid), 1)
        self.assertFalse(self.db.toggle_entry(gid, 7, 1001.0))
        self.assertEqual(self.db.count_entries(gid), 0)
        # 別のユーザーは独立
        self.assertTrue(self.db.toggle_entry(gid, 7, 1002.0))
        self.assertTrue(self.db.toggle_entry(gid, 8, 1003.0))
        self.assertEqual(self.db.count_entries(gid), 2)
        self.assertEqual(self.db.list_entry_user_ids(gid), [7, 8])
        # 他の抽選には影響しない
        other = self._create()
        self.assertEqual(self.db.count_entries(other), 0)

    def test_list_active_and_overdue(self):
        past = self._create(ends_at=500.0)
        future = self._create(ends_at=5000.0)
        other_guild = self._create(guild_id=200, ends_at=500.0)

        active = self.db.list_active_giveaways(100)
        self.assertEqual([r["id"] for r in active], [past, future])

        overdue = self.db.list_overdue_giveaways(1000.0)
        self.assertEqual([r["id"] for r in overdue], [past, other_guild])

        # 終了したものはどちらにも出ない
        self.db.finish_giveaway(past, "ended", [7])
        self.assertEqual(
            [r["id"] for r in self.db.list_overdue_giveaways(1000.0)],
            [other_guild],
        )
        self.assertEqual(
            [r["id"] for r in self.db.list_active_giveaways(100)], [future]
        )

    def test_finish_stores_winners(self):
        gid = self._create()
        self.db.finish_giveaway(gid, "ended", [7, 8])
        row = self.db.get_giveaway(gid)
        self.assertEqual(row["status"], "ended")
        self.assertEqual(row["winners"], [7, 8])
        # キャンセル時は当選者なし
        gid2 = self._create()
        self.db.finish_giveaway(gid2, "cancelled", [])
        row2 = self.db.get_giveaway(gid2)
        self.assertEqual(row2["status"], "cancelled")
        self.assertEqual(row2["winners"], [])


class TestVcEligible(unittest.TestCase):
    def test_all_good(self):
        self.assertTrue(
            vc_eligible(
                is_bot=False,
                is_afk_channel=False,
                self_deaf=False,
                deaf=False,
                human_count=2,
            )
        )
        self.assertTrue(
            vc_eligible(
                is_bot=False,
                is_afk_channel=False,
                self_deaf=False,
                deaf=False,
                human_count=5,
            )
        )

    def test_bot_excluded(self):
        self.assertFalse(vc_eligible(True, False, False, False, 3))

    def test_afk_channel_excluded(self):
        self.assertFalse(vc_eligible(False, True, False, False, 3))

    def test_self_deaf_excluded(self):
        self.assertFalse(vc_eligible(False, False, True, False, 3))

    def test_server_deaf_excluded(self):
        self.assertFalse(vc_eligible(False, False, False, True, 3))

    def test_alone_excluded(self):
        self.assertFalse(vc_eligible(False, False, False, False, 1))
        self.assertFalse(vc_eligible(False, False, False, False, 0))


class TestVcSessions(unittest.TestCase):
    KEY = (100, 200)

    def test_young_session_gets_no_grant(self):
        sessions = {self.KEY: 1000.0}
        grants, updated = advance_vc_sessions(
            sessions, 1000.0 + VC_TICK_SECONDS - 1, {self.KEY}
        )
        self.assertEqual(grants, [])
        # 開始時刻はそのまま保持される
        self.assertEqual(updated, {self.KEY: 1000.0})

    def test_due_session_gets_one_grant_and_clock_resets(self):
        sessions = {self.KEY: 1000.0}
        now = 1000.0 + VC_TICK_SECONDS
        grants, updated = advance_vc_sessions(sessions, now, {self.KEY})
        self.assertEqual(grants, [self.KEY])
        self.assertEqual(updated, {self.KEY: now})

    def test_long_session_still_one_grant_per_tick(self):
        # 5分経っていても1ティックの付与は1分分だけ (端数は持ち越さない)
        sessions = {self.KEY: 1000.0}
        now = 1000.0 + 5 * VC_TICK_SECONDS
        grants, updated = advance_vc_sessions(sessions, now, {self.KEY})
        self.assertEqual(grants, [self.KEY])
        self.assertEqual(updated, {self.KEY: now})

    def test_ineligible_session_dropped_without_grant(self):
        # 60秒以上経過していても、対象でなくなっていれば破棄のみ
        sessions = {self.KEY: 1000.0}
        grants, updated = advance_vc_sessions(
            sessions, 1000.0 + 10 * VC_TICK_SECONDS, set()
        )
        self.assertEqual(grants, [])
        self.assertEqual(updated, {})

    def test_mixed_sessions(self):
        due = (100, 1)
        young = (100, 2)
        gone = (100, 3)
        sessions = {due: 0.0, young: 1000.0, gone: 0.0}
        grants, updated = advance_vc_sessions(
            sessions, 1000.0 + 30, {due, young}
        )
        self.assertEqual(grants, [due])
        self.assertEqual(updated[due], 1030.0)
        self.assertEqual(updated[young], 1000.0)
        self.assertNotIn(gone, updated)


class TestVcXpSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_default(self):
        self.assertEqual(settings.get_vc_xp_per_minute(1), 2)

    def test_round_trip(self):
        settings.set_value(1, settings.VC_XP_PER_MINUTE, 5)
        self.assertEqual(settings.get_vc_xp_per_minute(1), 5)
        # ギルドごとに独立
        self.assertEqual(settings.get_vc_xp_per_minute(2), 2)

    def test_zero_means_disabled(self):
        settings.set_value(1, settings.VC_XP_PER_MINUTE, 0)
        self.assertEqual(settings.get_vc_xp_per_minute(1), 0)

    def test_corrupt_fallback(self):
        settings.set_value(1, settings.VC_XP_PER_MINUTE, "abc")
        self.assertEqual(settings.get_vc_xp_per_minute(1), 2)

    def test_out_of_range_fallback(self):
        settings.set_value(1, settings.VC_XP_PER_MINUTE, 1001)
        self.assertEqual(settings.get_vc_xp_per_minute(1), 2)
        settings.set_value(1, settings.VC_XP_PER_MINUTE, -1)
        self.assertEqual(settings.get_vc_xp_per_minute(1), 2)
        # 境界値は有効
        settings.set_value(1, settings.VC_XP_PER_MINUTE, 1000)
        self.assertEqual(settings.get_vc_xp_per_minute(1), 1000)


class TestVcXpConfigValidation(unittest.TestCase):
    def test_vc_field_valid(self):
        self.assertIsNone(validate_xp_config(10, 25, 30, 3, 0))
        self.assertIsNone(validate_xp_config(10, 25, 30, 3, 2))
        self.assertIsNone(validate_xp_config(10, 25, 30, 3, 1000))
        # 省略時 (None) は VC 検証をスキップし、既存の検証はそのまま効く
        self.assertIsNone(validate_xp_config(10, 25, 30, 3))
        self.assertIsNone(validate_xp_config(10, 25, 30, 3, None))

    def test_vc_field_invalid(self):
        self.assertIsNotNone(validate_xp_config(10, 25, 30, 3, -1))
        self.assertIsNotNone(validate_xp_config(10, 25, 30, 3, 1001))


class TestGiveawayButtonDynamicItem(unittest.TestCase):
    def test_custom_id_and_template(self):
        button = GiveawayButton(42)
        self.assertEqual(button.custom_id, "v2:giveaway:42")
        self.assertEqual(button.giveaway_id, 42)
        self.assertTrue(button.is_persistent())
        match = button.template.fullmatch("v2:giveaway:123")
        self.assertIsNotNone(match)
        self.assertEqual(match["giveaway_id"], "123")
        # 他のコンポーネントの custom_id にはマッチしない
        self.assertIsNone(button.template.fullmatch("v2:role:123"))
        self.assertIsNone(button.template.fullmatch("v2:agree"))
        self.assertIsNone(button.template.fullmatch("v2:giveaway:abc"))


class TestNextVote(unittest.TestCase):
    def test_new_vote(self):
        self.assertEqual(next_vote(None, 1), 1)
        self.assertEqual(next_vote(None, -1), -1)

    def test_same_side_removes(self):
        self.assertIsNone(next_vote(1, 1))
        self.assertIsNone(next_vote(-1, -1))

    def test_opposite_side_switches(self):
        self.assertEqual(next_vote(1, -1), -1)
        self.assertEqual(next_vote(-1, 1), 1)


class TestFeedbackTransitions(unittest.TestCase):
    def test_suggestion_valid(self):
        self.assertTrue(can_transition("suggestion", "pending", "adopted"))
        self.assertTrue(can_transition("suggestion", "pending", "rejected"))

    def test_suggestion_invalid(self):
        # 提案に存在しないステータス
        self.assertFalse(can_transition("suggestion", "pending", "working"))
        self.assertFalse(can_transition("suggestion", "pending", "fixed"))
        self.assertFalse(can_transition("suggestion", "pending", "pending"))

    def test_bug_valid(self):
        self.assertTrue(can_transition("bug", "open", "working"))
        self.assertTrue(can_transition("bug", "open", "fixed"))
        self.assertTrue(can_transition("bug", "open", "rejected"))
        self.assertTrue(can_transition("bug", "working", "fixed"))
        self.assertTrue(can_transition("bug", "working", "rejected"))

    def test_bug_invalid(self):
        self.assertFalse(can_transition("bug", "open", "adopted"))
        self.assertFalse(can_transition("bug", "working", "open"))
        self.assertFalse(can_transition("bug", "open", "open"))

    def test_terminal_states_reject_everything(self):
        for kind, terminal in (
            ("suggestion", "adopted"),
            ("suggestion", "rejected"),
            ("bug", "fixed"),
            ("bug", "rejected"),
        ):
            for target in ("pending", "open", "working", "adopted", "fixed", "rejected"):
                self.assertFalse(
                    can_transition(kind, terminal, target),
                    f"{kind} {terminal} -> {target}",
                )

    def test_unknown_kind(self):
        self.assertFalse(can_transition("unknown", "pending", "adopted"))


class TestFeedbackDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _suggestion(self, guild_id=100, author_id=7, content="新しいイベントがほしい"):
        return self.db.create_feedback(
            guild_id, "suggestion", author_id, "", content, 555, 1000.0
        )

    def _bug(self, guild_id=100, author_id=8, title="ログインできない"):
        return self.db.create_feedback(
            guild_id, "bug", author_id, title, "手順の詳細", 666, 1001.0
        )

    def test_create_get_round_trip_suggestion(self):
        fid = self._suggestion()
        row = self.db.get_feedback(fid)
        self.assertEqual(row["guild_id"], 100)
        self.assertEqual(row["kind"], "suggestion")
        self.assertEqual(row["author_id"], 7)
        self.assertEqual(row["title"], "")
        self.assertEqual(row["content"], "新しいイベントがほしい")
        self.assertEqual(row["channel_id"], 555)
        self.assertIsNone(row["message_id"])
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["created_at"], 1000.0)
        self.assertIsNone(self.db.get_feedback(99999))

    def test_create_get_round_trip_bug(self):
        fid = self._bug()
        row = self.db.get_feedback(fid)
        self.assertEqual(row["kind"], "bug")
        self.assertEqual(row["title"], "ログインできない")
        self.assertEqual(row["status"], "open")

    def test_message_and_status_updates(self):
        fid = self._suggestion()
        self.db.set_feedback_message(fid, 777)
        self.assertEqual(self.db.get_feedback(fid)["message_id"], 777)
        self.db.set_feedback_status(fid, "adopted")
        self.assertEqual(self.db.get_feedback(fid)["status"], "adopted")

    def test_votes_toggle_switch_remove(self):
        fid = self._suggestion()
        self.assertEqual(self.db.count_votes(fid), (0, 0))
        # 投票 → 集計に反映
        self.assertEqual(self.db.set_vote(fid, 1, 1), (1, 0))
        self.assertEqual(self.db.set_vote(fid, 2, 1), (2, 0))
        self.assertEqual(self.db.set_vote(fid, 3, -1), (2, 1))
        # 反対側へ切り替え
        self.assertEqual(self.db.set_vote(fid, 1, -1), (1, 2))
        # 同じ側をもう一度 → 取り消し
        self.assertEqual(self.db.set_vote(fid, 1, -1), (1, 1))
        self.assertEqual(self.db.set_vote(fid, 2, 1), (0, 1))
        self.assertEqual(self.db.count_votes(fid), (0, 1))
        # 投稿者本人も投票できる (author_id=7)
        self.assertEqual(self.db.set_vote(fid, 7, 1), (1, 1))
        # 他の投稿には影響しない
        other = self._suggestion()
        self.assertEqual(self.db.count_votes(other), (0, 0))

    def test_list_filtering(self):
        s1 = self._suggestion()
        b1 = self._bug()
        s2 = self._suggestion(content="別の提案")
        other_guild = self._suggestion(guild_id=200)

        all_rows = self.db.list_feedback(100)
        self.assertEqual([r["id"] for r in all_rows], [s2, b1, s1])

        suggestions = self.db.list_feedback(100, kind="suggestion")
        self.assertEqual([r["id"] for r in suggestions], [s2, s1])

        bugs = self.db.list_feedback(100, kind="bug")
        self.assertEqual([r["id"] for r in bugs], [b1])

        limited = self.db.list_feedback(100, limit=2)
        self.assertEqual([r["id"] for r in limited], [s2, b1])

        self.assertEqual(
            [r["id"] for r in self.db.list_feedback(200)], [other_guild]
        )
        self.assertEqual(self.db.list_feedback(300), [])


class TestFeedbackSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_channel_ids_default_unset(self):
        self.assertEqual(settings.get_suggestion_channel_id(1), 0)
        self.assertEqual(settings.get_bug_channel_id(1), 0)

    def test_channel_ids_round_trip(self):
        settings.set_value(1, settings.SUGGESTION_CHANNEL_ID, 123)
        settings.set_value(1, settings.BUG_CHANNEL_ID, 456)
        self.assertEqual(settings.get_suggestion_channel_id(1), 123)
        self.assertEqual(settings.get_bug_channel_id(1), 456)
        # ギルドごとに独立
        self.assertEqual(settings.get_suggestion_channel_id(2), 0)

    def test_channel_ids_corrupt_fallback(self):
        settings.set_value(1, settings.SUGGESTION_CHANNEL_ID, "abc")
        self.assertEqual(settings.get_suggestion_channel_id(1), 0)
        settings.set_value(1, settings.BUG_CHANNEL_ID, -5)
        self.assertEqual(settings.get_bug_channel_id(1), 0)


class TestFeedbackButtonsDynamicItem(unittest.TestCase):
    def test_vote_button_custom_id_and_template(self):
        button = FeedbackVoteButton(42, "up", 3)
        self.assertEqual(button.custom_id, "v2:fbvote:42:up")
        self.assertEqual(button.feedback_id, 42)
        self.assertEqual(button.side, "up")
        self.assertTrue(button.is_persistent())
        match = button.template.fullmatch("v2:fbvote:123:down")
        self.assertIsNotNone(match)
        self.assertEqual(match["feedback_id"], "123")
        self.assertEqual(match["side"], "down")
        # 他のコンポーネントの custom_id にはマッチしない
        self.assertIsNone(button.template.fullmatch("v2:role:123"))
        self.assertIsNone(button.template.fullmatch("v2:giveaway:123"))
        self.assertIsNone(button.template.fullmatch("v2:fbstatus:42:adopted"))
        self.assertIsNone(button.template.fullmatch("v2:fbvote:42:left"))
        self.assertIsNone(button.template.fullmatch("v2:fbvote:abc:up"))

    def test_status_button_custom_id_and_template(self):
        button = FeedbackStatusButton(42, "adopted")
        self.assertEqual(button.custom_id, "v2:fbstatus:42:adopted")
        self.assertEqual(button.feedback_id, 42)
        self.assertEqual(button.status_key, "adopted")
        self.assertTrue(button.is_persistent())
        match = button.template.fullmatch("v2:fbstatus:7:working")
        self.assertIsNotNone(match)
        self.assertEqual(match["feedback_id"], "7")
        self.assertEqual(match["status"], "working")
        # 他のコンポーネントの custom_id にはマッチしない
        self.assertIsNone(button.template.fullmatch("v2:role:42"))
        self.assertIsNone(button.template.fullmatch("v2:giveaway:42"))
        self.assertIsNone(button.template.fullmatch("v2:fbvote:42:up"))
        self.assertIsNone(button.template.fullmatch("v2:fbstatus:abc:fixed"))


class TestBumpDetection(unittest.TestCase):
    def test_disboard_japanese_marker(self):
        self.assertTrue(
            is_bump_success(
                DISBOARD_BOT_ID, ["サーバーの表示順をアップしました！"]
            )
        )
        self.assertTrue(
            is_bump_success(DISBOARD_BOT_ID, ["表示順をアップしたよ"])
        )

    def test_disboard_english_marker(self):
        self.assertTrue(
            is_bump_success(DISBOARD_BOT_ID, ["Bump done! :thumbsup:"])
        )
        self.assertTrue(
            is_bump_success(DISBOARD_BOT_ID, ["<@7> bumped the server!"])
        )

    def test_disboard_unrelated_embed(self):
        self.assertFalse(
            is_bump_success(DISBOARD_BOT_ID, ["DISBOARD のお知らせです"])
        )

    def test_other_author_with_marker(self):
        self.assertFalse(
            is_bump_success(123456789, ["サーバーの表示順をアップしました"])
        )
        self.assertFalse(is_bump_success(123456789, ["Bump done!"]))

    def test_no_embeds(self):
        self.assertFalse(is_bump_success(DISBOARD_BOT_ID, []))
        self.assertFalse(is_bump_success(DISBOARD_BOT_ID, None))
        self.assertFalse(is_bump_success(DISBOARD_BOT_ID, [None, ""]))

    def test_marker_in_any_embed(self):
        descriptions = ["関係ない文", "サーバーの表示順をアップしました"]
        self.assertTrue(is_bump_success(DISBOARD_BOT_ID, descriptions))


class TestBumpExtractBumper(unittest.TestCase):
    def test_mention_present(self):
        self.assertEqual(
            extract_bumper_id("<@123456> が表示順をアップしました"), 123456
        )

    def test_nickname_mention_present(self):
        self.assertEqual(extract_bumper_id("<@!987654> bumped"), 987654)

    def test_mention_absent(self):
        self.assertIsNone(extract_bumper_id("表示順をアップしました"))
        self.assertIsNone(extract_bumper_id(""))
        self.assertIsNone(extract_bumper_id(None))


class TestBumpReminderDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_get_round_trip(self):
        self.db.set_bump_reminder(100, 555, 777, 2000.0)
        row = self.db.get_bump_reminder(100)
        self.assertEqual(row["guild_id"], 100)
        self.assertEqual(row["channel_id"], 555)
        self.assertEqual(row["bumper_id"], 777)
        self.assertEqual(row["remind_at"], 2000.0)
        self.assertIsNone(self.db.get_bump_reminder(999))

    def test_bumper_nullable(self):
        self.db.set_bump_reminder(100, 555, None, 2000.0)
        self.assertIsNone(self.db.get_bump_reminder(100)["bumper_id"])

    def test_overwrite_resets_reminder(self):
        self.db.set_bump_reminder(100, 555, 777, 2000.0)
        self.db.set_bump_reminder(100, 666, 888, 5000.0)
        row = self.db.get_bump_reminder(100)
        self.assertEqual(row["channel_id"], 666)
        self.assertEqual(row["bumper_id"], 888)
        self.assertEqual(row["remind_at"], 5000.0)

    def test_due_selection(self):
        self.db.set_bump_reminder(100, 555, 777, 1500.0)  # 期限切れ
        self.db.set_bump_reminder(200, 556, 778, 2500.0)  # まだ先
        due = self.db.get_due_bump_reminders(2000.0)
        self.assertEqual([r["guild_id"] for r in due], [100])
        # 時刻ちょうども期限として扱う
        due = self.db.get_due_bump_reminders(2500.0)
        self.assertEqual([r["guild_id"] for r in due], [100, 200])

    def test_clear(self):
        self.db.set_bump_reminder(100, 555, 777, 1500.0)
        self.db.clear_bump_reminder(100)
        self.assertIsNone(self.db.get_bump_reminder(100))
        self.assertEqual(self.db.get_due_bump_reminders(2000.0), [])

    def test_per_guild_isolation(self):
        self.db.set_bump_reminder(100, 555, 777, 1500.0)
        self.db.set_bump_reminder(200, 556, 778, 1600.0)
        self.db.clear_bump_reminder(100)
        self.assertIsNone(self.db.get_bump_reminder(100))
        self.assertEqual(self.db.get_bump_reminder(200)["channel_id"], 556)


class TestBumpSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_enabled_default_true(self):
        self.assertTrue(settings.get_bump_reminder_enabled(1))

    def test_enabled_round_trip(self):
        settings.set_value(1, settings.BUMP_REMINDER_ENABLED, False)
        self.assertFalse(settings.get_bump_reminder_enabled(1))
        settings.set_value(1, settings.BUMP_REMINDER_ENABLED, True)
        self.assertTrue(settings.get_bump_reminder_enabled(1))
        # ギルドごとに独立
        self.assertTrue(settings.get_bump_reminder_enabled(2))

    def test_enabled_corrupt_fallback_true(self):
        settings.set_value(1, settings.BUMP_REMINDER_ENABLED, "abc")
        self.assertTrue(settings.get_bump_reminder_enabled(1))

    def test_notify_role_default_unset(self):
        self.assertEqual(settings.get_bump_notify_role_id(1), 0)

    def test_notify_role_round_trip(self):
        settings.set_value(1, settings.BUMP_NOTIFY_ROLE_ID, 424242)
        self.assertEqual(settings.get_bump_notify_role_id(1), 424242)
        self.assertEqual(settings.get_bump_notify_role_id(2), 0)

    def test_notify_role_corrupt_fallback(self):
        settings.set_value(1, settings.BUMP_NOTIFY_ROLE_ID, "abc")
        self.assertEqual(settings.get_bump_notify_role_id(1), 0)
        settings.set_value(1, settings.BUMP_NOTIFY_ROLE_ID, -5)
        self.assertEqual(settings.get_bump_notify_role_id(1), 0)


if __name__ == "__main__":
    unittest.main()
