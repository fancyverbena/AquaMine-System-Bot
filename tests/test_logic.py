"""v2 のロジックテスト (stdlib unittest のみ)。

実行: python -m unittest discover -s tests
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import settings  # noqa: E402
from app.db import Database, next_vote  # noqa: E402
from app.ui import progress_bar, sanitize_channel_part  # noqa: E402
from cogs.automod import (  # noqa: E402
    contains_link,
    content_violation,
    find_invite,
    find_ngword,
    is_link_restricted,
    link_restriction_violation,
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
    roles_for_level,
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
from cogs.backup import is_due  # noqa: E402
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
from cogs.tickets import (  # noqa: E402
    TicketRatingButton,
    rating_block_reason,
    valid_ticket_type,
)
from cogs.serverlog import (  # noqa: E402
    DEFAULT_WELCOME_MESSAGE,
    UNKNOWN_CONTENT_TEXT,
    content_or_unknown,
    effective_welcome_template,
    render_welcome,
    should_log_edit,
    truncate_content,
)
from cogs.weekly import (  # noqa: E402
    JST,
    compute_gains,
    previous_week_key,
    should_post,
    week_key,
    week_range_label,
)
from cogs.tempvc import (  # noqa: E402
    MAX_CHANNEL_NAME_LENGTH,
    join_action,
    public_overwrites,
    should_delete,
    temp_channel_name,
)
from cogs.stats import (  # noqa: E402
    jst_date,
    period_range,
    should_post_stats,
    summarize,
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


class TestVcLevelRolesSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_round_trip(self):
        self.assertEqual(settings.get_vc_level_roles(1), {})
        settings.set_vc_level_role(1, 10, 555)
        self.assertEqual(settings.get_vc_level_roles(1), {"10": "555"})
        settings.set_vc_level_role(1, 20, 777)
        self.assertEqual(
            settings.get_vc_level_roles(1), {"10": "555", "20": "777"}
        )
        # 同じレベルは上書きできる
        settings.set_vc_level_role(1, 10, 999)
        self.assertEqual(settings.get_vc_level_roles(1)["10"], "999")
        # 解除 (無いレベルは False)
        self.assertTrue(settings.remove_vc_level_role(1, 10))
        self.assertFalse(settings.remove_vc_level_role(1, 10))
        self.assertEqual(settings.get_vc_level_roles(1), {"20": "777"})
        # ギルドごとに独立
        self.assertEqual(settings.get_vc_level_roles(2), {})

    def test_independent_from_text_level_roles(self):
        settings.set_level_role(1, 10, 111)
        settings.set_vc_level_role(1, 10, 222)
        self.assertEqual(settings.get_level_roles(1), {"10": "111"})
        self.assertEqual(settings.get_vc_level_roles(1), {"10": "222"})
        # VC 側を解除しても発言側は残る
        self.assertTrue(settings.remove_vc_level_role(1, 10))
        self.assertEqual(settings.get_level_roles(1), {"10": "111"})


class TestVcTrackSeparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_vc_defaults_are_zero(self):
        data = self.db.get_xp(1, 100)
        self.assertEqual(data["vc_xp"], 0)
        self.assertEqual(data["vc_level"], 0)

    def test_text_write_does_not_touch_vc(self):
        self.db.upsert_vc_xp(1, 100, 300, 2)
        self.db.upsert_xp(1, 100, 500, 4, 1.0, recent_content_hashes=["a"])
        data = self.db.get_xp(1, 100)
        self.assertEqual(data["xp"], 500)
        self.assertEqual(data["level"], 4)
        self.assertEqual(data["vc_xp"], 300)
        self.assertEqual(data["vc_level"], 2)

    def test_vc_write_does_not_touch_text(self):
        self.db.upsert_xp(1, 100, 500, 4, 1.5, recent_content_hashes=["x"])
        self.db.upsert_vc_xp(1, 100, 800, 5)
        data = self.db.get_xp(1, 100)
        self.assertEqual(data["xp"], 500)
        self.assertEqual(data["level"], 4)
        self.assertEqual(data["last_message_time"], 1.5)
        self.assertEqual(data["recent_content_hashes"], ["x"])
        self.assertEqual(data["vc_xp"], 800)
        self.assertEqual(data["vc_level"], 5)

    def test_vc_write_creates_row_with_text_zero(self):
        self.db.upsert_vc_xp(9, 100, 120, 1)
        data = self.db.get_xp(9, 100)
        self.assertEqual(data["xp"], 0)
        self.assertEqual(data["level"], 0)
        self.assertEqual(data["vc_xp"], 120)
        self.assertEqual(data["vc_level"], 1)

    def test_vc_columns_migration(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            with conn:
                # vc 列のない user_xp (発言側の値だけ入っている)
                conn.execute(
                    """
                    CREATE TABLE user_xp (
                        user_id INTEGER NOT NULL,
                        guild_id INTEGER NOT NULL,
                        xp INTEGER NOT NULL DEFAULT 0,
                        level INTEGER NOT NULL DEFAULT 0,
                        last_message_time REAL NOT NULL DEFAULT 0,
                        recent_content_hashes TEXT,
                        PRIMARY KEY (user_id, guild_id)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO user_xp VALUES (7, 100, 500, 4, 1.5, NULL)"
                )
            conn.close()

            db = Database(path)

            conn = sqlite3.connect(path)
            columns = [
                row[1] for row in conn.execute("PRAGMA table_info(user_xp)")
            ]
            conn.close()
            self.assertIn("vc_xp", columns)
            self.assertIn("vc_level", columns)

            # 既存の発言側は保持され、VC 側は 0 から始まる
            data = db.get_xp(7, 100)
            self.assertEqual(data["xp"], 500)
            self.assertEqual(data["level"], 4)
            self.assertEqual(data["vc_xp"], 0)
            self.assertEqual(data["vc_level"], 0)

            # VC 側を書いても発言側は変わらない
            db.upsert_vc_xp(7, 100, 120, 1)
            data = db.get_xp(7, 100)
            self.assertEqual(data["xp"], 500)
            self.assertEqual(data["vc_xp"], 120)


class TestRolesForLevel(unittest.TestCase):
    def test_selects_roles_at_or_below_level(self):
        roles = {"5": "111", "10": "222", "20": "333"}
        self.assertEqual(roles_for_level(roles, 4), [])
        self.assertEqual(roles_for_level(roles, 5), [(5, 111)])
        self.assertEqual(roles_for_level(roles, 10), [(5, 111), (10, 222)])
        self.assertEqual(
            roles_for_level(roles, 99), [(5, 111), (10, 222), (20, 333)]
        )

    def test_result_sorted_by_level(self):
        roles = {"20": "333", "5": "111"}
        self.assertEqual(roles_for_level(roles, 20), [(5, 111), (20, 333)])

    def test_skips_invalid_entries(self):
        roles = {"x": "111", "5": "abc", "7": "222"}
        self.assertEqual(roles_for_level(roles, 10), [(7, 222)])

    def test_empty(self):
        self.assertEqual(roles_for_level({}, 10), [])
        self.assertEqual(roles_for_level(None, 10), [])


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


class TestDatabaseBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_to_copies_rows_and_tables(self):
        import sqlite3

        self.db.set_setting(123, "some_key", "some_value")
        self.db.upsert_xp(42, 123, 500, 3, 0.0)
        dest = os.path.join(self.tmp.name, "backup.db")
        self.db.backup_to(dest)

        self.assertTrue(os.path.exists(dest))
        with sqlite3.connect(dest) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value FROM guild_settings"
                " WHERE guild_id = 123 AND key = 'some_key'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(json.loads(row["value"]), "some_value")
            xp_row = conn.execute(
                "SELECT xp, level FROM user_xp"
                " WHERE user_id = 42 AND guild_id = 123"
            ).fetchone()
            self.assertIsNotNone(xp_row)
            self.assertEqual(xp_row["xp"], 500)
            self.assertEqual(xp_row["level"], 3)
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        for expected in (
            "guild_settings",
            "user_xp",
            "tickets",
            "infractions",
            "giveaways",
            "feedback",
            "bump_reminders",
        ):
            self.assertIn(expected, tables)

    def test_backup_to_creates_parent_directory(self):
        dest = os.path.join(self.tmp.name, "nested", "dir", "backup.db")
        self.db.backup_to(dest)
        self.assertTrue(os.path.exists(dest))


class TestBackupIsDue(unittest.TestCase):
    def test_never_run_is_due(self):
        self.assertTrue(is_due(None, 1_000_000.0, 24))
        self.assertTrue(is_due(0, 1_000_000.0, 24))
        self.assertTrue(is_due(0.0, 1_000_000.0, 1))

    def test_just_ran_is_not_due(self):
        now = 1_000_000.0
        self.assertFalse(is_due(now, now, 24))
        self.assertFalse(is_due(now - 3600, now, 24))

    def test_interval_elapsed_is_due(self):
        now = 1_000_000.0
        self.assertTrue(is_due(now - 24 * 3600, now, 24))
        self.assertTrue(is_due(now - 25 * 3600, now, 24))
        # 境界の手前はまだ
        self.assertFalse(is_due(now - 24 * 3600 + 1, now, 24))

    def test_zero_or_negative_interval_never_due(self):
        now = 1_000_000.0
        self.assertFalse(is_due(None, now, 0))
        self.assertFalse(is_due(0, now, 0))
        self.assertFalse(is_due(now - 10 * 24 * 3600, now, 0))
        self.assertFalse(is_due(None, now, -1))


class TestBackupSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_channel_default_unset(self):
        self.assertEqual(settings.get_backup_channel_id(1), 0)

    def test_channel_round_trip(self):
        settings.set_value(1, settings.BACKUP_CHANNEL_ID, 987654)
        self.assertEqual(settings.get_backup_channel_id(1), 987654)
        self.assertEqual(settings.get_backup_channel_id(2), 0)

    def test_channel_corrupt_fallback(self):
        settings.set_value(1, settings.BACKUP_CHANNEL_ID, "abc")
        self.assertEqual(settings.get_backup_channel_id(1), 0)
        settings.set_value(1, settings.BACKUP_CHANNEL_ID, -5)
        self.assertEqual(settings.get_backup_channel_id(1), 0)

    def test_interval_default_24(self):
        self.assertEqual(settings.get_backup_interval_hours(1), 24)

    def test_interval_round_trip(self):
        settings.set_value(1, settings.BACKUP_INTERVAL_HOURS, 6)
        self.assertEqual(settings.get_backup_interval_hours(1), 6)
        settings.set_value(1, settings.BACKUP_INTERVAL_HOURS, 0)
        self.assertEqual(settings.get_backup_interval_hours(1), 0)
        settings.set_value(1, settings.BACKUP_INTERVAL_HOURS, 168)
        self.assertEqual(settings.get_backup_interval_hours(1), 168)
        self.assertEqual(settings.get_backup_interval_hours(2), 24)

    def test_interval_out_of_range_fallback(self):
        settings.set_value(1, settings.BACKUP_INTERVAL_HOURS, 169)
        self.assertEqual(settings.get_backup_interval_hours(1), 24)
        settings.set_value(1, settings.BACKUP_INTERVAL_HOURS, -1)
        self.assertEqual(settings.get_backup_interval_hours(1), 24)
        settings.set_value(1, settings.BACKUP_INTERVAL_HOURS, "abc")
        self.assertEqual(settings.get_backup_interval_hours(1), 24)

    def test_last_at_default_and_round_trip(self):
        self.assertEqual(settings.get_backup_last_at(1), 0.0)
        settings.set_value(1, settings.BACKUP_LAST_AT, 1700000000.5)
        self.assertEqual(settings.get_backup_last_at(1), 1700000000.5)
        self.assertEqual(settings.get_backup_last_at(2), 0.0)


try:
    import io as _io

    from PIL import Image as _PILImage

    from app.rankcard import (
        CARD_HEIGHT as _CARD_HEIGHT,
        CARD_WIDTH as _CARD_WIDTH,
        generate_rank_card as _generate_rank_card,
        load_font as _load_font,
    )

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


@unittest.skipUnless(_HAS_PIL, "Pillow がインストールされていない")
class TestRankCard(unittest.TestCase):
    def _dummy_avatar(self):
        buf = _io.BytesIO()
        _PILImage.new("RGB", (128, 128), (200, 60, 60)).save(buf, format="PNG")
        return buf.getvalue()

    def _generate(self, avatar_bytes):
        return _generate_rank_card(
            display_name="テストユーザー",
            level=5,
            rank_position=2,
            total_xp=777,
            fraction=0.5,
            remaining_xp=68,
            next_level=6,
            next_threshold=845,
            avatar_bytes=avatar_bytes,
        )

    def test_card_with_avatar(self):
        png = self._generate(self._dummy_avatar())
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 5000)
        img = _PILImage.open(_io.BytesIO(png))
        self.assertEqual(img.size, (_CARD_WIDTH, _CARD_HEIGHT))

    def test_card_without_avatar_uses_fallback(self):
        png = self._generate(None)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 5000)
        img = _PILImage.open(_io.BytesIO(png))
        self.assertEqual(img.size, (_CARD_WIDTH, _CARD_HEIGHT))

    def test_card_with_broken_avatar_bytes_uses_fallback(self):
        png = self._generate(b"not-a-png")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_load_font_never_raises(self):
        self.assertIsNotNone(_load_font(24))
        self.assertIsNotNone(_load_font(24, bold=True))


class TestTicketRatingDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _prompt(self, ticket_id=555, guild_id=100, user_id=7):
        return self.db.create_ticket_rating_prompt(
            ticket_id, guild_id, user_id, 1000.0
        )

    def test_prompt_and_complete_round_trip(self):
        self.assertTrue(self._prompt())
        row = self.db.get_ticket_rating(555)
        self.assertEqual(row["guild_id"], 100)
        self.assertEqual(row["user_id"], 7)
        self.assertEqual(row["rating"], 0)  # 評価待ち
        self.assertTrue(self.db.complete_ticket_rating(555, 7, 4, 2000.0))
        row = self.db.get_ticket_rating(555)
        self.assertEqual(row["rating"], 4)
        self.assertEqual(row["created_at"], 2000.0)
        self.assertIsNone(self.db.get_ticket_rating(99999))

    def test_duplicate_prompt_ignored(self):
        self.assertTrue(self._prompt())
        self.assertFalse(self._prompt())
        # 評価済みの行にもプロンプトは作れない
        self.assertTrue(self.db.complete_ticket_rating(555, 7, 5, 2000.0))
        self.assertFalse(self._prompt())

    def test_complete_without_prompt_fails(self):
        self.assertFalse(self.db.complete_ticket_rating(555, 7, 3, 2000.0))
        self.assertIsNone(self.db.get_ticket_rating(555))

    def test_double_rating_rejected(self):
        self._prompt()
        self.assertTrue(self.db.complete_ticket_rating(555, 7, 2, 2000.0))
        # 2回目は確定できず、最初の評価が残る
        self.assertFalse(self.db.complete_ticket_rating(555, 7, 5, 3000.0))
        self.assertEqual(self.db.get_ticket_rating(555)["rating"], 2)

    def test_other_user_cannot_complete(self):
        self._prompt(user_id=7)
        self.assertFalse(self.db.complete_ticket_rating(555, 8, 5, 2000.0))
        self.assertEqual(self.db.get_ticket_rating(555)["rating"], 0)

    def test_stars_out_of_range_raise(self):
        self._prompt()
        for bad in (0, 6, -1):
            with self.assertRaises(ValueError):
                self.db.complete_ticket_rating(555, 7, bad, 2000.0)
        self.assertEqual(self.db.get_ticket_rating(555)["rating"], 0)

    def test_delete_prompt(self):
        self._prompt()
        self.db.delete_ticket_rating(555)
        self.assertIsNone(self.db.get_ticket_rating(555))

    def test_stats_math(self):
        # ギルド100: ⭐5, ⭐4, ⭐4 と評価待ち1件 (集計対象外)
        for ticket_id, user_id, stars in ((1, 7, 5), (2, 8, 4), (3, 9, 4)):
            self._prompt(ticket_id=ticket_id, user_id=user_id)
            self.assertTrue(
                self.db.complete_ticket_rating(ticket_id, user_id, stars, 2000.0)
            )
        self._prompt(ticket_id=4, user_id=10)
        # ギルド200: ⭐1 が1件 (ギルド100の集計に混ざらない)
        self._prompt(ticket_id=5, guild_id=200, user_id=11)
        self.assertTrue(self.db.complete_ticket_rating(5, 11, 1, 2000.0))

        stats = self.db.ticket_rating_stats(100)
        self.assertEqual(stats["count"], 3)
        self.assertAlmostEqual(stats["average"], 13 / 3)
        self.assertEqual(
            stats["distribution"], {1: 0, 2: 0, 3: 0, 4: 2, 5: 1}
        )

        stats200 = self.db.ticket_rating_stats(200)
        self.assertEqual(stats200["count"], 1)
        self.assertEqual(stats200["average"], 1.0)

    def test_stats_empty_guild(self):
        stats = self.db.ticket_rating_stats(100)
        self.assertEqual(stats["count"], 0)
        self.assertEqual(stats["average"], 0.0)
        self.assertEqual(
            stats["distribution"], {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        )


class TestTicketRatingDecision(unittest.TestCase):
    def _row(self, user_id=7, rating=0):
        return {
            "ticket_id": 555,
            "guild_id": 100,
            "user_id": user_id,
            "rating": rating,
            "created_at": 1000.0,
        }

    def test_ok(self):
        self.assertIsNone(rating_block_reason(self._row(), 7, 1))
        self.assertIsNone(rating_block_reason(self._row(), 7, 5))

    def test_missing_row(self):
        self.assertIsNotNone(rating_block_reason(None, 7, 3))

    def test_non_creator_rejected(self):
        self.assertIsNotNone(rating_block_reason(self._row(), 8, 3))

    def test_already_rated_rejected(self):
        self.assertIsNotNone(rating_block_reason(self._row(rating=4), 7, 3))

    def test_stars_out_of_range_rejected(self):
        self.assertIsNotNone(rating_block_reason(self._row(), 7, 0))
        self.assertIsNotNone(rating_block_reason(self._row(), 7, 6))


class TestTicketNotifySettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_valid_ticket_type(self):
        for key in ("question", "report", "whitelist", "other"):
            self.assertTrue(valid_ticket_type(key))
        self.assertFalse(valid_ticket_type("unknown"))
        self.assertFalse(valid_ticket_type(""))

    def test_rating_enabled_default_on(self):
        self.assertTrue(settings.get_ticket_rating_enabled(1))

    def test_rating_enabled_round_trip_and_corrupt(self):
        settings.set_value(1, settings.TICKET_RATING_ENABLED, False)
        self.assertFalse(settings.get_ticket_rating_enabled(1))
        # 壊れた値は既定 (有効) にフォールバック
        settings.set_value(1, settings.TICKET_RATING_ENABLED, "off")
        self.assertTrue(settings.get_ticket_rating_enabled(1))
        # ギルドごとに独立
        self.assertTrue(settings.get_ticket_rating_enabled(2))

    def test_notify_roles_default_unset(self):
        self.assertEqual(settings.get_ticket_notify_roles(1), {})
        self.assertEqual(settings.get_ticket_notify_role(1, "question"), 0)

    def test_notify_roles_round_trip_per_type(self):
        settings.set_ticket_notify_role(1, "question", 111)
        settings.set_ticket_notify_role(1, "whitelist", 222)
        self.assertEqual(settings.get_ticket_notify_role(1, "question"), 111)
        self.assertEqual(settings.get_ticket_notify_role(1, "whitelist"), 222)
        self.assertEqual(settings.get_ticket_notify_role(1, "report"), 0)
        self.assertEqual(
            settings.get_ticket_notify_roles(1),
            {"question": 111, "whitelist": 222},
        )
        # ギルドごとに独立
        self.assertEqual(settings.get_ticket_notify_roles(2), {})

    def test_notify_role_clear(self):
        settings.set_ticket_notify_role(1, "question", 111)
        settings.set_ticket_notify_role(1, "question", None)
        self.assertEqual(settings.get_ticket_notify_role(1, "question"), 0)
        settings.set_ticket_notify_role(1, "report", 333)
        settings.set_ticket_notify_role(1, "report", 0)
        self.assertEqual(settings.get_ticket_notify_roles(1), {})

    def test_notify_roles_corrupt_fallback(self):
        settings.set_value(1, settings.TICKET_NOTIFY_ROLES, "garbage")
        self.assertEqual(settings.get_ticket_notify_roles(1), {})
        # 数値化できないロール ID は落とされる
        settings.set_value(
            1, settings.TICKET_NOTIFY_ROLES, {"question": "abc", "report": "5"}
        )
        self.assertEqual(settings.get_ticket_notify_roles(1), {"report": 5})


class TestTicketRatingButtonDynamicItem(unittest.TestCase):
    def test_custom_id_and_template(self):
        button = TicketRatingButton(42, 3)
        self.assertEqual(button.custom_id, "v2:ticketrate:42:3")
        self.assertEqual(button.ticket_id, 42)
        self.assertEqual(button.stars, 3)
        self.assertTrue(button.is_persistent())
        match = button.template.fullmatch("v2:ticketrate:123:5")
        self.assertIsNotNone(match)
        self.assertEqual(match["ticket_id"], "123")
        self.assertEqual(match["stars"], "5")
        # 他のコンポーネントの custom_id にはマッチしない
        self.assertIsNone(button.template.fullmatch("v2:role:42"))
        self.assertIsNone(button.template.fullmatch("v2:giveaway:42"))
        self.assertIsNone(button.template.fullmatch("v2:fbvote:42:up"))
        self.assertIsNone(button.template.fullmatch("v2:ticketrate:abc:3"))
        self.assertIsNone(button.template.fullmatch("v2:ticketrate:42"))


class TestRenderWelcome(unittest.TestCase):
    def test_all_placeholders_replaced(self):
        result = render_welcome(
            "{member} さん、{server} へようこそ！{name} さんは {count} 人目です",
            member_mention="<@123>",
            name="テスト太郎",
            server="AquaMine",
            count=42,
        )
        self.assertEqual(
            result,
            "<@123> さん、AquaMine へようこそ！"
            "テスト太郎 さんは 42 人目です",
        )

    def test_unknown_placeholder_preserved(self):
        result = render_welcome(
            "{member} {unknown} {count}",
            member_mention="<@123>",
            name="n",
            server="s",
            count=7,
        )
        self.assertEqual(result, "<@123> {unknown} 7")

    def test_repeated_placeholder(self):
        result = render_welcome(
            "{name} {name}",
            member_mention="m",
            name="繰り返し",
            server="s",
            count=1,
        )
        self.assertEqual(result, "繰り返し 繰り返し")

    def test_empty_template_renders_empty(self):
        self.assertEqual(
            render_welcome(
                "",
                member_mention="m",
                name="n",
                server="s",
                count=1,
            ),
            "",
        )


class TestEffectiveWelcomeTemplate(unittest.TestCase):
    def test_empty_uses_default(self):
        self.assertEqual(effective_welcome_template(""), DEFAULT_WELCOME_MESSAGE)

    def test_whitespace_only_uses_default(self):
        self.assertEqual(
            effective_welcome_template("  \n "), DEFAULT_WELCOME_MESSAGE
        )

    def test_non_string_uses_default(self):
        self.assertEqual(effective_welcome_template(None), DEFAULT_WELCOME_MESSAGE)
        self.assertEqual(effective_welcome_template(123), DEFAULT_WELCOME_MESSAGE)

    def test_custom_template_kept(self):
        self.assertEqual(
            effective_welcome_template("{member} ようこそ！"),
            "{member} ようこそ！",
        )

    def test_default_template_renders(self):
        result = render_welcome(
            effective_welcome_template(""),
            member_mention="<@9>",
            name="新規さん",
            server="AquaMine",
            count=100,
        )
        self.assertIn("<@9>", result)
        self.assertIn("AquaMine", result)
        self.assertIn("100", result)


class TestServerLogSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_welcome_channel_default_and_round_trip(self):
        self.assertEqual(settings.get_welcome_channel_id(1), 0)
        settings.set_value(1, settings.WELCOME_CHANNEL_ID, 555)
        self.assertEqual(settings.get_welcome_channel_id(1), 555)
        self.assertEqual(settings.get_welcome_channel_id(2), 0)

    def test_welcome_channel_corrupt_fallback(self):
        settings.set_value(1, settings.WELCOME_CHANNEL_ID, "abc")
        self.assertEqual(settings.get_welcome_channel_id(1), 0)
        settings.set_value(1, settings.WELCOME_CHANNEL_ID, -3)
        self.assertEqual(settings.get_welcome_channel_id(1), 0)

    def test_welcome_message_default_and_round_trip(self):
        self.assertEqual(settings.get_welcome_message(1), "")
        settings.set_value(1, settings.WELCOME_MESSAGE, "{member} hi")
        self.assertEqual(settings.get_welcome_message(1), "{member} hi")
        self.assertEqual(settings.get_welcome_message(2), "")

    def test_welcome_message_corrupt_fallback(self):
        settings.set_value(1, settings.WELCOME_MESSAGE, 123)
        self.assertEqual(settings.get_welcome_message(1), "")

    def test_welcome_dm_default_false(self):
        self.assertFalse(settings.get_welcome_dm_enabled(1))

    def test_welcome_dm_round_trip(self):
        settings.set_value(1, settings.WELCOME_DM_ENABLED, True)
        self.assertTrue(settings.get_welcome_dm_enabled(1))
        settings.set_value(1, settings.WELCOME_DM_ENABLED, False)
        self.assertFalse(settings.get_welcome_dm_enabled(1))
        self.assertFalse(settings.get_welcome_dm_enabled(2))

    def test_welcome_dm_corrupt_fallback_false(self):
        settings.set_value(1, settings.WELCOME_DM_ENABLED, "yes")
        self.assertFalse(settings.get_welcome_dm_enabled(1))

    def test_audit_channel_default_and_round_trip(self):
        self.assertEqual(settings.get_audit_log_channel_id(1), 0)
        settings.set_value(1, settings.AUDIT_LOG_CHANNEL_ID, 777)
        self.assertEqual(settings.get_audit_log_channel_id(1), 777)
        self.assertEqual(settings.get_audit_log_channel_id(2), 0)

    def test_audit_channel_corrupt_fallback(self):
        settings.set_value(1, settings.AUDIT_LOG_CHANNEL_ID, "abc")
        self.assertEqual(settings.get_audit_log_channel_id(1), 0)
        settings.set_value(1, settings.AUDIT_LOG_CHANNEL_ID, -1)
        self.assertEqual(settings.get_audit_log_channel_id(1), 0)

    def test_audit_delete_default_true(self):
        self.assertTrue(settings.get_audit_message_delete_enabled(1))

    def test_audit_delete_round_trip_and_corrupt(self):
        settings.set_value(1, settings.AUDIT_MESSAGE_DELETE, False)
        self.assertFalse(settings.get_audit_message_delete_enabled(1))
        settings.set_value(1, settings.AUDIT_MESSAGE_DELETE, "abc")
        self.assertTrue(settings.get_audit_message_delete_enabled(1))

    def test_audit_edit_default_true(self):
        self.assertTrue(settings.get_audit_message_edit_enabled(1))

    def test_audit_edit_round_trip_and_corrupt(self):
        settings.set_value(1, settings.AUDIT_MESSAGE_EDIT, False)
        self.assertFalse(settings.get_audit_message_edit_enabled(1))
        settings.set_value(1, settings.AUDIT_MESSAGE_EDIT, 0)
        self.assertTrue(settings.get_audit_message_edit_enabled(1))

    def test_audit_voice_default_false(self):
        self.assertFalse(settings.get_audit_voice_enabled(1))

    def test_audit_voice_round_trip_and_corrupt(self):
        settings.set_value(1, settings.AUDIT_VOICE, True)
        self.assertTrue(settings.get_audit_voice_enabled(1))
        settings.set_value(1, settings.AUDIT_VOICE, "yes")
        self.assertFalse(settings.get_audit_voice_enabled(1))


class TestAuditLogHelpers(unittest.TestCase):
    def test_should_log_edit_unchanged(self):
        self.assertFalse(should_log_edit("同じ文", "同じ文"))
        self.assertFalse(should_log_edit("", ""))
        self.assertFalse(should_log_edit(None, ""))
        self.assertFalse(should_log_edit(None, None))

    def test_should_log_edit_changed(self):
        self.assertTrue(should_log_edit("編集前", "編集後"))
        self.assertTrue(should_log_edit("", "追加された"))
        self.assertTrue(should_log_edit("消された", ""))

    def test_content_or_unknown(self):
        self.assertEqual(content_or_unknown("本文"), "本文")
        self.assertEqual(content_or_unknown(""), UNKNOWN_CONTENT_TEXT)
        self.assertEqual(content_or_unknown(None), UNKNOWN_CONTENT_TEXT)
        self.assertEqual(content_or_unknown("   "), UNKNOWN_CONTENT_TEXT)

    def test_truncate_content(self):
        self.assertEqual(truncate_content("短い"), "短い")
        long_text = "あ" * 1500
        truncated = truncate_content(long_text)
        self.assertEqual(len(truncated), 1000)
        self.assertTrue(truncated.endswith("…"))


class TestAutomodLinkRestrict(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(
            2026, 1, 8, 12, 0, tzinfo=datetime.timezone.utc
        )

    def test_contains_link_schemes(self):
        self.assertTrue(contains_link("https://example.com/page"))
        self.assertTrue(contains_link("http://example.com"))

    def test_contains_link_uppercase_scheme(self):
        self.assertTrue(contains_link("HTTPS://EXAMPLE.COM"))
        self.assertTrue(contains_link("Http://example.com"))

    def test_contains_link_mid_sentence(self):
        self.assertTrue(contains_link("これ見て https://example.com すごいよ"))

    def test_contains_link_negative(self):
        self.assertFalse(contains_link("普通の文章です"))
        # スキームなしのベアドメインはリンクとみなさない
        self.assertFalse(contains_link("example.com にアクセスして"))
        self.assertFalse(contains_link("www.example.com"))
        self.assertFalse(contains_link(""))
        self.assertFalse(contains_link(None))

    def test_is_link_restricted_within_window(self):
        joined = self.now - datetime.timedelta(days=3)
        self.assertTrue(is_link_restricted(joined, self.now, 7))
        joined = self.now - datetime.timedelta(hours=1)
        self.assertTrue(is_link_restricted(joined, self.now, 7))

    def test_is_link_restricted_boundary_is_free(self):
        # ちょうど N 日経過したら制限は解ける
        joined = self.now - datetime.timedelta(days=7)
        self.assertFalse(is_link_restricted(joined, self.now, 7))

    def test_is_link_restricted_past_window(self):
        joined = self.now - datetime.timedelta(days=30)
        self.assertFalse(is_link_restricted(joined, self.now, 7))

    def test_is_link_restricted_disabled(self):
        # 日数 0 以下は制限自体が無効 (参加直後でも対象外)
        self.assertFalse(is_link_restricted(self.now, self.now, 0))
        self.assertFalse(is_link_restricted(self.now, self.now, -3))

    def test_is_link_restricted_unknown_join_time(self):
        # 参加日時不明なら処罰しない (フェイルオープン)
        self.assertFalse(is_link_restricted(None, self.now, 7))

    def test_link_restriction_violation(self):
        joined = self.now - datetime.timedelta(days=1)
        result = link_restriction_violation(
            "https://example.com", joined, self.now, 7
        )
        self.assertEqual(result[0], "new_member_link")
        self.assertIn("7日間", result[1])
        # リンクがなければ違反でない
        self.assertIsNone(
            link_restriction_violation("こんにちは", joined, self.now, 7)
        )
        # 制限期間を過ぎていれば違反でない
        old_member = self.now - datetime.timedelta(days=10)
        self.assertIsNone(
            link_restriction_violation(
                "https://example.com", old_member, self.now, 7
            )
        )
        # 宣伝許可チャンネルでは検知しない
        self.assertIsNone(
            link_restriction_violation(
                "https://example.com", joined, self.now, 7, allow_links=True
            )
        )


class TestAutomodLinkRestrictSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_default_is_7(self):
        self.assertEqual(settings.get_automod_link_restrict_days(1), 7)

    def test_round_trip(self):
        settings.set_value(1, settings.AUTOMOD_LINK_RESTRICT_DAYS, 30)
        self.assertEqual(settings.get_automod_link_restrict_days(1), 30)
        # 0 (無効) と上限 90 もそのまま保持される
        settings.set_value(1, settings.AUTOMOD_LINK_RESTRICT_DAYS, 0)
        self.assertEqual(settings.get_automod_link_restrict_days(1), 0)
        settings.set_value(1, settings.AUTOMOD_LINK_RESTRICT_DAYS, 90)
        self.assertEqual(settings.get_automod_link_restrict_days(1), 90)
        # 他ギルドには影響しない
        self.assertEqual(settings.get_automod_link_restrict_days(2), 7)

    def test_out_of_range_fallback(self):
        settings.set_value(1, settings.AUTOMOD_LINK_RESTRICT_DAYS, 91)
        self.assertEqual(settings.get_automod_link_restrict_days(1), 7)
        settings.set_value(1, settings.AUTOMOD_LINK_RESTRICT_DAYS, -1)
        self.assertEqual(settings.get_automod_link_restrict_days(1), 7)

    def test_corrupt_fallback(self):
        settings.set_value(1, settings.AUTOMOD_LINK_RESTRICT_DAYS, "abc")
        self.assertEqual(settings.get_automod_link_restrict_days(1), 7)


def _jst(year, month, day, hour=0, minute=0):
    return datetime.datetime(year, month, day, hour, minute, tzinfo=JST)


def _utc(year, month, day, hour=0, minute=0):
    return datetime.datetime(
        year, month, day, hour, minute, tzinfo=datetime.timezone.utc
    )


class TestWeekKey(unittest.TestCase):
    # 2026-11-23 は月曜日、11-25 は水曜日、11-29 は日曜日
    def test_wednesday_maps_to_monday(self):
        self.assertEqual(week_key(_jst(2026, 11, 25, 12, 0)), "2026-11-23")
        self.assertEqual(week_key(_utc(2026, 11, 25, 3, 0)), "2026-11-23")

    def test_sunday_maps_to_monday_six_days_earlier(self):
        self.assertEqual(week_key(_jst(2026, 11, 29, 10, 0)), "2026-11-23")

    def test_monday_early_morning_maps_to_itself(self):
        self.assertEqual(week_key(_jst(2026, 11, 23, 0, 30)), "2026-11-23")

    def test_utc_saturday_evening_already_sunday_in_jst(self):
        # 2026-11-28 16:00 UTC = 11-29 (日) 01:00 JST → その週の月曜日
        self.assertEqual(week_key(_utc(2026, 11, 28, 16, 0)), "2026-11-23")

    def test_utc_sunday_evening_already_monday_in_jst(self):
        # 2026-11-22 16:00 UTC = 11-23 (月) 01:00 JST
        # UTC 基準の週 (11-16 開始) ではなく JST 基準の週になる
        self.assertEqual(week_key(_utc(2026, 11, 22, 16, 0)), "2026-11-23")

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime.datetime(2026, 11, 25, 3, 0)
        self.assertEqual(week_key(naive), "2026-11-23")


class TestPreviousWeekKey(unittest.TestCase):
    def test_previous_week(self):
        self.assertEqual(previous_week_key(_jst(2026, 11, 25)), "2026-11-16")
        self.assertEqual(previous_week_key(_jst(2026, 11, 23)), "2026-11-16")
        self.assertEqual(previous_week_key(_jst(2026, 11, 29)), "2026-11-16")

    def test_consistent_with_week_key_minus_seven_days(self):
        for dt in (
            _jst(2026, 11, 23, 0, 30),
            _jst(2026, 11, 27, 23, 59),
            _utc(2026, 11, 28, 16, 0),
            _utc(2026, 12, 1, 0, 0),
        ):
            self.assertEqual(
                previous_week_key(dt),
                week_key(dt - datetime.timedelta(days=7)),
            )


class TestShouldPost(unittest.TestCase):
    # 2026-11-30 は月曜日。その前週のキーは "2026-11-23"
    def test_monday_before_9am_is_false(self):
        self.assertFalse(should_post(_jst(2026, 11, 30, 8, 59), ""))

    def test_monday_9am_is_true_when_not_posted(self):
        self.assertTrue(should_post(_jst(2026, 11, 30, 9, 0), ""))
        self.assertTrue(should_post(_jst(2026, 11, 30, 9, 0), "2026-11-16"))
        self.assertTrue(should_post(_jst(2026, 11, 30, 23, 59), ""))

    def test_monday_9am_in_utc(self):
        # 月曜日 09:00 JST = 月曜日 00:00 UTC
        self.assertTrue(should_post(_utc(2026, 11, 30, 0, 0), ""))

    def test_already_posted_is_false(self):
        self.assertFalse(should_post(_jst(2026, 11, 30, 9, 0), "2026-11-23"))
        self.assertFalse(should_post(_jst(2026, 11, 30, 18, 0), "2026-11-23"))

    def test_tuesday_is_false(self):
        self.assertFalse(should_post(_jst(2026, 12, 1, 10, 0), ""))
        self.assertFalse(should_post(_jst(2026, 11, 29, 12, 0), ""))


class TestComputeGains(unittest.TestCase):
    def test_basic_gains_and_missing_baseline(self):
        rows = [(1, 100), (2, 50), (3, 30), (4, 10)]
        baselines = {1: 40, 2: 50, 3: 10}
        entries = compute_gains(rows, baselines)
        # 2 は増加 0 なので除外、4 は基準なし = 0 から増えた扱い
        self.assertEqual(
            entries,
            [
                {"user_id": 1, "gain": 60, "total_xp": 100},
                {"user_id": 3, "gain": 20, "total_xp": 30},
                {"user_id": 4, "gain": 10, "total_xp": 10},
            ],
        )

    def test_negative_gain_excluded(self):
        # 累計が基準を下回る (XP リセット等) 場合は含めない
        self.assertEqual(compute_gains([(8, 10)], {8: 50}), [])

    def test_tie_breaks_by_total_then_user_id(self):
        rows = [(7, 150), (6, 200), (5, 200)]
        baselines = {5: 100, 6: 100, 7: 50}
        entries = compute_gains(rows, baselines)
        self.assertEqual([e["user_id"] for e in entries], [5, 6, 7])
        self.assertTrue(all(e["gain"] == 100 for e in entries))

    def test_empty_inputs(self):
        self.assertEqual(compute_gains([], {}), [])
        self.assertEqual(compute_gains([(1, 0)], {}), [])
        self.assertEqual(compute_gains([(1, 5)], None), [
            {"user_id": 1, "gain": 5, "total_xp": 5}
        ])


class TestWeekRangeLabel(unittest.TestCase):
    def test_label(self):
        self.assertEqual(week_range_label("2026-11-23"), "11/23〜11/29")
        # 月をまたぐ週
        self.assertEqual(week_range_label("2026-11-30"), "11/30〜12/6")


class TestWeeklyBaselineDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_replace_and_get_round_trip(self):
        self.assertEqual(self.db.get_xp_baselines(1), {})
        self.assertEqual(self.db.count_xp_baselines(1), 0)
        self.db.replace_xp_baselines(1, [(10, 100), (20, 250)])
        self.assertEqual(self.db.get_xp_baselines(1), {10: 100, 20: 250})
        self.assertEqual(self.db.count_xp_baselines(1), 2)

    def test_replace_overwrites_previous(self):
        self.db.replace_xp_baselines(1, [(10, 100), (20, 250)])
        self.db.replace_xp_baselines(1, [(30, 5)])
        self.assertEqual(self.db.get_xp_baselines(1), {30: 5})
        self.assertEqual(self.db.count_xp_baselines(1), 1)

    def test_guild_isolation(self):
        self.db.replace_xp_baselines(1, [(10, 100)])
        self.db.replace_xp_baselines(2, [(10, 999), (11, 1)])
        self.assertEqual(self.db.get_xp_baselines(1), {10: 100})
        self.assertEqual(self.db.get_xp_baselines(2), {10: 999, 11: 1})
        self.assertEqual(self.db.count_xp_baselines(1), 1)
        self.assertEqual(self.db.count_xp_baselines(2), 2)

    def test_get_all_user_xp(self):
        self.db.upsert_xp(20, 1, 250, 2, 0.0)
        self.db.upsert_xp(10, 1, 100, 1, 0.0)
        self.db.upsert_xp(10, 2, 777, 5, 0.0)
        self.assertEqual(self.db.get_all_user_xp(1), [(10, 100), (20, 250)])
        self.assertEqual(self.db.get_all_user_xp(2), [(10, 777)])
        self.assertEqual(self.db.get_all_user_xp(3), [])


class TestWeeklySettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_channel_default_unset(self):
        self.assertEqual(settings.get_weekly_ranking_channel_id(1), 0)

    def test_channel_round_trip(self):
        settings.set_value(1, settings.WEEKLY_RANKING_CHANNEL_ID, 555)
        self.assertEqual(settings.get_weekly_ranking_channel_id(1), 555)
        self.assertEqual(settings.get_weekly_ranking_channel_id(2), 0)

    def test_channel_corrupt_fallback(self):
        settings.set_value(1, settings.WEEKLY_RANKING_CHANNEL_ID, "abc")
        self.assertEqual(settings.get_weekly_ranking_channel_id(1), 0)
        settings.set_value(1, settings.WEEKLY_RANKING_CHANNEL_ID, -3)
        self.assertEqual(settings.get_weekly_ranking_channel_id(1), 0)

    def test_last_week_default_and_round_trip(self):
        self.assertEqual(settings.get_weekly_ranking_last_week(1), "")
        settings.set_value(1, settings.WEEKLY_RANKING_LAST_WEEK, "2026-11-23")
        self.assertEqual(settings.get_weekly_ranking_last_week(1), "2026-11-23")
        self.assertEqual(settings.get_weekly_ranking_last_week(2), "")

    def test_last_week_corrupt_fallback(self):
        settings.set_value(1, settings.WEEKLY_RANKING_LAST_WEEK, 20261123)
        self.assertEqual(settings.get_weekly_ranking_last_week(1), "")


class TestTempVcChannelName(unittest.TestCase):
    def test_normal_name(self):
        self.assertEqual(temp_channel_name("たろう"), "たろう の VC")
        # 表示名は sanitize_channel_part を通る (小文字化される)
        self.assertEqual(temp_channel_name("Taro"), "taro の VC")

    def test_long_name_is_capped(self):
        name = temp_channel_name("あ" * 200)
        self.assertLessEqual(len(name), MAX_CHANNEL_NAME_LENGTH)
        self.assertTrue(name.endswith(" の VC"))

    def test_empty_name_falls_back(self):
        self.assertEqual(temp_channel_name(""), "member の VC")
        self.assertEqual(temp_channel_name("   "), "member の VC")
        self.assertEqual(temp_channel_name(None), "member の VC")


class TestTempVcShouldDelete(unittest.TestCase):
    def test_empty_is_deleted(self):
        # Bot を除くメンバーが0人なら削除 (音楽Botだけの状態もここに来る)
        self.assertTrue(should_delete(0))

    def test_occupied_is_kept(self):
        self.assertFalse(should_delete(1))
        self.assertFalse(should_delete(5))


class TestTempVcJoinAction(unittest.TestCase):
    def test_create_when_no_existing(self):
        self.assertEqual(join_action(None), "create")

    def test_reuse_when_existing(self):
        row = {"channel_id": 123, "guild_id": 1, "owner_id": 42}
        self.assertEqual(join_action(row), "reuse")


class TestTempVcPublicOverwrites(unittest.TestCase):
    def test_everyone_can_view_and_connect(self):
        # @everyone (default_role) には「見る」「接続」だけを許可する
        overwrites = public_overwrites(999)
        self.assertEqual(list(overwrites.keys()), [999])
        overwrite = overwrites[999]
        self.assertIs(overwrite.view_channel, True)
        self.assertIs(overwrite.connect, True)
        # それ以外の権限には触れない (未設定 = None)
        self.assertIsNone(overwrite.speak)
        self.assertIsNone(overwrite.send_messages)
        self.assertIsNone(overwrite.mention_everyone)


class TestTempVcDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_get_round_trip(self):
        self.assertIsNone(self.db.get_temp_vc_channel(100))
        self.db.add_temp_vc_channel(100, 1, 42, 1000.0)
        row = self.db.get_temp_vc_channel(100)
        self.assertEqual(row["guild_id"], 1)
        self.assertEqual(row["owner_id"], 42)
        self.assertEqual(row["created_at"], 1000.0)

    def test_get_by_owner(self):
        self.db.add_temp_vc_channel(100, 1, 42, 1000.0)
        self.db.add_temp_vc_channel(200, 1, 43, 1001.0)
        row = self.db.get_temp_vc_channel_by_owner(1, 43)
        self.assertEqual(row["channel_id"], 200)
        self.assertIsNone(self.db.get_temp_vc_channel_by_owner(1, 999))
        # ギルドが違えば見つからない
        self.assertIsNone(self.db.get_temp_vc_channel_by_owner(2, 42))

    def test_list_and_guild_isolation(self):
        self.db.add_temp_vc_channel(100, 1, 42, 1000.0)
        self.db.add_temp_vc_channel(200, 2, 43, 1001.0)
        self.db.add_temp_vc_channel(300, 1, 44, 1002.0)
        self.assertEqual(
            [r["channel_id"] for r in self.db.list_temp_vc_channels()],
            [100, 200, 300],
        )
        self.assertEqual(
            [r["channel_id"] for r in self.db.list_temp_vc_channels(1)],
            [100, 300],
        )
        self.assertEqual(
            [r["channel_id"] for r in self.db.list_temp_vc_channels(2)],
            [200],
        )

    def test_remove(self):
        self.db.add_temp_vc_channel(100, 1, 42, 1000.0)
        self.db.remove_temp_vc_channel(100)
        self.assertIsNone(self.db.get_temp_vc_channel(100))
        self.assertEqual(self.db.list_temp_vc_channels(), [])


class TestTempVcSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_lobby_default_unset(self):
        self.assertEqual(settings.get_temp_vc_lobby_id(1), 0)

    def test_lobby_round_trip(self):
        settings.set_value(1, settings.TEMP_VC_LOBBY_ID, 555)
        self.assertEqual(settings.get_temp_vc_lobby_id(1), 555)
        # ギルドごとに独立
        self.assertEqual(settings.get_temp_vc_lobby_id(2), 0)
        settings.set_value(1, settings.TEMP_VC_LOBBY_ID, 0)
        self.assertEqual(settings.get_temp_vc_lobby_id(1), 0)

    def test_lobby_corrupt_fallback(self):
        settings.set_value(1, settings.TEMP_VC_LOBBY_ID, "abc")
        self.assertEqual(settings.get_temp_vc_lobby_id(1), 0)
        settings.set_value(1, settings.TEMP_VC_LOBBY_ID, -3)
        self.assertEqual(settings.get_temp_vc_lobby_id(1), 0)


class TestStatsJstDate(unittest.TestCase):
    def test_utc_afternoon_is_same_day_in_jst(self):
        dt = datetime.datetime(2026, 11, 26, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(jst_date(dt), "2026-11-26")

    def test_utc_1530_is_next_day_in_jst(self):
        # 15:30 UTC = 翌日 00:30 JST
        dt = datetime.datetime(2026, 11, 26, 15, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(jst_date(dt), "2026-11-27")

    def test_just_before_jst_midnight(self):
        # 14:59 UTC = 23:59 JST (当日)
        dt = datetime.datetime(2026, 11, 26, 14, 59, tzinfo=datetime.timezone.utc)
        self.assertEqual(jst_date(dt), "2026-11-26")

    def test_naive_datetime_treated_as_utc(self):
        self.assertEqual(jst_date(datetime.datetime(2026, 11, 26, 15, 30)), "2026-11-27")


class TestStatsDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_increment_accumulates(self):
        self.db.increment_stat(1, "2026-11-26", "message", 10)
        self.db.increment_stat(1, "2026-11-26", "message", 10)
        self.db.increment_stat(1, "2026-11-26", "message", 10)
        rows = self.db.get_stats(1, "2026-11-26", "2026-11-26")
        self.assertEqual(
            rows,
            [{"date": "2026-11-26", "metric": "message", "channel_id": 10, "count": 3}],
        )

    def test_increment_amount(self):
        self.db.increment_stat(1, "2026-11-26", "message", 10, amount=5)
        self.db.increment_stat(1, "2026-11-26", "message", 10, amount=2)
        rows = self.db.get_stats(1, "2026-11-26", "2026-11-26")
        self.assertEqual(rows[0]["count"], 7)

    def test_channel_and_metric_separation(self):
        self.db.increment_stat(1, "2026-11-26", "message", 10)
        self.db.increment_stat(1, "2026-11-26", "message", 20)
        self.db.increment_stat(1, "2026-11-26", "join")
        rows = self.db.get_stats(1, "2026-11-26", "2026-11-26")
        by_key = {(r["metric"], r["channel_id"]): r["count"] for r in rows}
        self.assertEqual(
            by_key,
            {("message", 10): 1, ("message", 20): 1, ("join", 0): 1},
        )

    def test_guild_isolation(self):
        self.db.increment_stat(1, "2026-11-26", "message", 10)
        self.db.increment_stat(2, "2026-11-26", "message", 10, amount=4)
        self.assertEqual(
            self.db.get_stats(1, "2026-11-26", "2026-11-26")[0]["count"], 1
        )
        self.assertEqual(
            self.db.get_stats(2, "2026-11-26", "2026-11-26")[0]["count"], 4
        )
        self.assertEqual(self.db.get_stats(3, "2026-11-26", "2026-11-26"), [])

    def test_range_filtering_is_inclusive(self):
        self.db.increment_stat(1, "2026-11-19", "message", 10)
        self.db.increment_stat(1, "2026-11-20", "message", 10)
        self.db.increment_stat(1, "2026-11-26", "message", 10)
        self.db.increment_stat(1, "2026-11-27", "message", 10)
        rows = self.db.get_stats(1, "2026-11-20", "2026-11-26")
        self.assertEqual([r["date"] for r in rows], ["2026-11-20", "2026-11-26"])


class TestStatsPeriodCounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_infractions_in_range(self):
        self.db.add_infraction(1, 10, "spam", None, "warn", 1000.0)
        self.db.add_infraction(1, 11, "spam", None, "warn", 2000.0)
        self.db.add_infraction(1, 12, "spam", None, "warn", 3000.0)
        self.db.add_infraction(2, 10, "spam", None, "warn", 2000.0)
        # [start, end) の半開区間
        self.assertEqual(self.db.count_infractions_in_range(1, 1500.0, 3000.0), 1)
        self.assertEqual(self.db.count_infractions_in_range(1, 0.0, 4000.0), 3)
        self.assertEqual(self.db.count_infractions_in_range(2, 0.0, 4000.0), 1)

    def test_giveaways_in_range(self):
        self.db.create_giveaway(1, 10, 99, "賞品", 1, 5000.0, 1000.0)
        self.db.create_giveaway(1, 10, 99, "賞品", 1, 5000.0, 2500.0)
        self.assertEqual(self.db.count_giveaways_in_range(1, 0.0, 2000.0), 1)
        self.assertEqual(self.db.count_giveaways_in_range(1, 0.0, 9000.0), 2)
        self.assertEqual(self.db.count_giveaways_in_range(2, 0.0, 9000.0), 0)

    def test_feedback_in_range(self):
        self.db.create_feedback(1, "suggestion", 10, "", "提案", 20, 1000.0)
        self.db.create_feedback(1, "bug", 11, "題", "内容", 21, 2500.0)
        self.db.create_feedback(2, "suggestion", 10, "", "提案", 20, 2500.0)
        self.assertEqual(self.db.count_feedback_in_range(1, 0.0, 2000.0), 1)
        self.assertEqual(self.db.count_feedback_in_range(1, 0.0, 9000.0), 2)
        self.assertEqual(self.db.count_feedback_in_range(2, 0.0, 9000.0), 1)


class TestStatsSummarize(unittest.TestCase):
    def test_totals_average_and_net(self):
        rows = [
            {"date": "2026-11-24", "metric": "message", "channel_id": 10, "count": 30},
            {"date": "2026-11-25", "metric": "message", "channel_id": 10, "count": 10},
            {"date": "2026-11-25", "metric": "message", "channel_id": 20, "count": 30},
            {"date": "2026-11-25", "metric": "join", "channel_id": 0, "count": 5},
            {"date": "2026-11-26", "metric": "leave", "channel_id": 0, "count": 2},
        ]
        result = summarize(rows, days=7)
        self.assertEqual(result["total_messages"], 70)
        # データのない日も含めて期間日数 (7) で割る
        self.assertAlmostEqual(result["messages_per_day"], 10.0)
        self.assertEqual(result["joins"], 5)
        self.assertEqual(result["leaves"], 2)
        self.assertEqual(result["net"], 3)
        self.assertEqual(result["top_channels"], [(10, 40), (20, 30)])

    def test_top_channels_limit_and_tie_break(self):
        rows = [
            {"date": "2026-11-25", "metric": "message", "channel_id": cid, "count": count}
            for cid, count in [
                (60, 5), (10, 50), (30, 20), (20, 20), (50, 1), (40, 10),
            ]
        ]
        result = summarize(rows, days=7)
        # 上位5件まで、同点は channel_id の昇順
        self.assertEqual(
            result["top_channels"],
            [(10, 50), (20, 20), (30, 20), (40, 10), (60, 5)],
        )

    def test_channel_zero_excluded_from_top_but_counted_in_total(self):
        rows = [
            {"date": "2026-11-25", "metric": "message", "channel_id": 0, "count": 99},
            {"date": "2026-11-25", "metric": "message", "channel_id": 10, "count": 3},
        ]
        result = summarize(rows, days=7)
        self.assertEqual(result["total_messages"], 102)
        self.assertEqual(result["top_channels"], [(10, 3)])

    def test_empty_rows(self):
        result = summarize([], days=7)
        self.assertEqual(result["total_messages"], 0)
        self.assertEqual(result["messages_per_day"], 0.0)
        self.assertEqual(result["joins"], 0)
        self.assertEqual(result["leaves"], 0)
        self.assertEqual(result["net"], 0)
        self.assertEqual(result["top_channels"], [])


class TestStatsPeriodRange(unittest.TestCase):
    def test_seven_days_includes_today(self):
        now = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=JST)
        start_date, end_date, start_ts, end_ts = period_range(now, 7)
        self.assertEqual(start_date, "2026-11-19")
        self.assertEqual(end_date, "2026-11-25")
        self.assertAlmostEqual(end_ts - start_ts, 7 * 86400)
        # 開始は開始日 00:00 JST
        start_dt = datetime.datetime.fromtimestamp(start_ts, JST)
        self.assertEqual(
            (start_dt.hour, start_dt.minute, start_dt.second), (0, 0, 0)
        )

    def test_thirty_days(self):
        now = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=JST)
        start_date, end_date, start_ts, end_ts = period_range(now, 30)
        self.assertEqual(start_date, "2026-10-27")
        self.assertEqual(end_date, "2026-11-25")
        self.assertAlmostEqual(end_ts - start_ts, 30 * 86400)


class TestStatsShouldPost(unittest.TestCase):
    def test_monday_before_0905_is_false(self):
        now = datetime.datetime(2026, 11, 30, 9, 4, tzinfo=JST)
        self.assertFalse(should_post_stats(now, ""))

    def test_monday_0905_is_true_when_not_posted(self):
        now = datetime.datetime(2026, 11, 30, 9, 5, tzinfo=JST)
        self.assertTrue(should_post_stats(now, ""))

    def test_monday_0905_in_utc(self):
        # 月曜日 00:05 UTC = 09:05 JST
        now = datetime.datetime(2026, 11, 30, 0, 5, tzinfo=datetime.timezone.utc)
        self.assertTrue(should_post_stats(now, ""))

    def test_already_posted_is_false(self):
        now = datetime.datetime(2026, 11, 30, 10, 0, tzinfo=JST)
        self.assertFalse(should_post_stats(now, "2026-11-23"))

    def test_tuesday_is_false(self):
        now = datetime.datetime(2026, 12, 1, 10, 0, tzinfo=JST)
        self.assertFalse(should_post_stats(now, ""))


class TestStatsSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = settings.db
        settings.db = Database(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        settings.db = self._orig_db
        self.tmp.cleanup()

    def test_channel_default_unset(self):
        self.assertEqual(settings.get_stats_channel_id(1), 0)

    def test_channel_round_trip(self):
        settings.set_value(1, settings.STATS_CHANNEL_ID, 555)
        self.assertEqual(settings.get_stats_channel_id(1), 555)
        self.assertEqual(settings.get_stats_channel_id(2), 0)

    def test_channel_corrupt_fallback(self):
        settings.set_value(1, settings.STATS_CHANNEL_ID, "abc")
        self.assertEqual(settings.get_stats_channel_id(1), 0)
        settings.set_value(1, settings.STATS_CHANNEL_ID, -3)
        self.assertEqual(settings.get_stats_channel_id(1), 0)

    def test_last_week_default_and_round_trip(self):
        self.assertEqual(settings.get_stats_last_week(1), "")
        settings.set_value(1, settings.STATS_LAST_WEEK, "2026-11-23")
        self.assertEqual(settings.get_stats_last_week(1), "2026-11-23")
        self.assertEqual(settings.get_stats_last_week(2), "")

    def test_last_week_corrupt_fallback(self):
        settings.set_value(1, settings.STATS_LAST_WEEK, 20261123)
        self.assertEqual(settings.get_stats_last_week(1), "")


if __name__ == "__main__":
    unittest.main()
