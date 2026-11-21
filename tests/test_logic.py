"""ロジックのユニットテスト (標準ライブラリ unittest のみ)。

実行方法: リポジトリルートで `python -m unittest discover -s tests -v`
(discord.py がインストールされている仮想環境を想定)
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import jsonstore
from ticket_store import TicketStore


class LevelingCalcTest(unittest.TestCase):
    """レベリングのXP/レベル計算 (cogs.leveling のモジュール関数)。"""

    @classmethod
    def setUpClass(cls):
        from cogs import leveling

        cls.leveling = leveling

    def test_thresholds(self):
        calc = self.leveling.calc_xp_for_level

        # レベル到達に必要な累計XP
        self.assertEqual(calc(1), 155)
        self.assertEqual(calc(2), 220)
        self.assertEqual(calc(3), 295)

    def test_calc_level(self):
        calc_level = self.leveling.calc_level

        self.assertEqual(calc_level(0), 0)
        self.assertEqual(calc_level(154), 0)
        self.assertEqual(calc_level(155), 1)
        self.assertEqual(calc_level(219), 1)
        self.assertEqual(calc_level(220), 2)

    def test_level_zero_baseline_and_progress(self):
        leveling = self.leveling

        # レベル0の基準は 0 XP でなければならない (M4)
        self.assertEqual(leveling.current_level_baseline(0), 0)
        self.assertEqual(leveling.current_level_baseline(1), 155)
        self.assertEqual(leveling.current_level_baseline(2), 220)

        # レベル0・XP 0 のとき進捗は 0 (マイナスにならない)
        xp = 0
        level = leveling.calc_level(xp)
        baseline = leveling.current_level_baseline(level)
        next_xp = leveling.calc_xp_for_level(level + 1)

        progress = xp - baseline
        needed = next_xp - baseline

        self.assertEqual(progress, 0)
        self.assertEqual(needed, 155)
        self.assertGreaterEqual(progress, 0)

    def test_cog_methods_match_module_functions(self):
        leveling = self.leveling

        cog = object.__new__(leveling.LevelingCog)

        self.assertEqual(cog.calc_xp_for_level(1), 155)
        self.assertEqual(cog.calc_level(220), 2)
        self.assertEqual(cog.get_level_up_xp(0), 155)


class JsonStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "settings.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_round_trip(self):
        jsonstore.write_json(self.path, {"a": 1, "b": "あ"})

        self.assertEqual(
            jsonstore.read_json(self.path, {}),
            {"a": 1, "b": "あ"}
        )

        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1, "b": "あ"})

    def test_missing_file_returns_default_and_creates_file(self):
        result = jsonstore.read_json(self.path, {"x": []})

        self.assertEqual(result, {"x": []})
        self.assertTrue(os.path.exists(self.path))

    def test_update_json_mutator(self):
        jsonstore.write_json(self.path, {"guild": {"verified_role": 1}})

        def mutator(data):
            data["guild"]["level_roles"] = {"1": "42"}
            return data

        updated = jsonstore.update_json(self.path, {}, mutator)

        self.assertEqual(updated["guild"]["verified_role"], 1)
        self.assertEqual(updated["guild"]["level_roles"], {"1": "42"})
        self.assertEqual(
            jsonstore.read_json(self.path, {}),
            updated
        )

    def test_corrupt_file_is_backed_up_not_silently_wiped(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("<<<<<<< HEAD\n{}\n=======\n{}\n")

        result = jsonstore.read_json(self.path, {})

        # デフォルトにフォールバックしつつ、壊れた内容は退避される
        self.assertEqual(result, {})
        self.assertTrue(os.path.exists(self.path + ".corrupt"))

        with open(self.path + ".corrupt", encoding="utf-8") as f:
            self.assertIn("<<<<<<< HEAD", f.read())

        # 退避後は新しいファイルが正常なJSONとして読める
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {})


class TicketStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.tmpdir.name, "tickets.db")
        self.store = TicketStore(db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_insert_lookup_delete(self):
        created = self.store.create_ticket(
            channel_id=111,
            guild_id=222,
            owner_id=333,
            ticket_type="question",
            now=1000.0
        )

        self.assertEqual(created["channel_id"], 111)
        self.assertEqual(created["guild_id"], 222)
        self.assertEqual(created["owner_id"], 333)
        self.assertEqual(created["ticket_type"], "question")
        self.assertIsNone(created["assignee_id"])
        self.assertEqual(created["created_at"], 1000.0)
        self.assertEqual(created["last_activity_at"], 1000.0)

        fetched = self.store.get_ticket(111)
        self.assertEqual(fetched, created)

        self.assertIsNone(self.store.get_ticket(999))

        self.store.delete_ticket(111)
        self.assertIsNone(self.store.get_ticket(111))

    def test_lookup_by_owner_and_assignee_and_activity(self):
        self.store.create_ticket(
            channel_id=111,
            guild_id=222,
            owner_id=333,
            ticket_type="report",
            now=1000.0
        )

        row = self.store.get_open_ticket_by_owner(222, 333)
        self.assertIsNotNone(row)
        self.assertEqual(row["channel_id"], 111)

        self.assertIsNone(self.store.get_open_ticket_by_owner(222, 444))

        self.store.set_assignee(111, 555)
        self.assertEqual(self.store.get_ticket(111)["assignee_id"], 555)

        self.store.touch_activity(111, now=2000.0)
        self.assertEqual(
            self.store.get_ticket(111)["last_activity_at"],
            2000.0
        )

        self.assertEqual(len(self.store.list_tickets()), 1)


class TicketNamingTest(unittest.TestCase):
    """チケットのチャンネル名サニタイズ (cogs.tickets)。"""

    @classmethod
    def setUpClass(cls):
        from cogs import tickets

        cls.tickets = tickets

    def test_types(self):
        self.assertEqual(
            set(self.tickets.TICKET_TYPES),
            {"question", "report", "whitelist", "other"}
        )

    def test_sanitize(self):
        sanitize = self.tickets.sanitize_channel_name

        self.assertEqual(sanitize("Yuu Ma!"), "yuu-ma")
        self.assertEqual(sanitize("abc_def-123"), "abc_def-123")
        self.assertEqual(sanitize("!!!"), "")
        # 日本語のみの名前は空になる (呼び出し側で "user" にフォールバック)
        self.assertEqual(sanitize("ゆうま"), "")

    def test_channel_name(self):
        class FakeUser:
            def __init__(self, name):
                self.name = name

        name = self.tickets.ticket_channel_name(
            "whitelist",
            FakeUser("Yuu Ma!")
        )
        self.assertEqual(name, "ticket-whitelist-yuu-ma")

        fallback = self.tickets.ticket_channel_name(
            "question",
            FakeUser("ゆうま")
        )
        self.assertEqual(fallback, "ticket-question-user")

        long_name = self.tickets.ticket_channel_name(
            "other",
            FakeUser("a" * 200)
        )
        self.assertLessEqual(len(long_name), 100)


if __name__ == "__main__":
    unittest.main()
