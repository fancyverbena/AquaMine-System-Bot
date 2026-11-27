"""単一 SQLite DB (data/bot.db) へのアクセス層。

テーブル:
  guild_settings(guild_id, key, value)  -- value は JSON 文字列
  user_xp(user_id, guild_id, xp, level, last_message_time, recent_content_hashes,
          vc_xp, vc_level)
          -- xp/level は発言トラック、vc_xp/vc_level は VC トラック。
             2つのトラックは独立に加算され、互いに干渉しない
  xp_weekly_baseline(guild_id, user_id, xp)
          -- 週間ランキング用。週の開始時点の累計 XP スナップショットで、
             現在の累計との差分が「その週に増えた XP」になる
  tickets(channel_id, guild_id, owner_id, assignee_id, ticket_type,
          subject, message_id, created_at, last_activity_at)
  ticket_ratings(ticket_id, guild_id, user_id, rating, created_at)
          -- ticket_id は tickets の channel_id。rating=0 は評価待ち
             (閉鎖時に DM を送ったが未回答)、1〜5 が実際の評価
  infractions(id, guild_id, user_id, reason, detail, punishment, created_at)
  giveaways(id, guild_id, channel_id, message_id, host_id, prize,
            winner_count, ends_at, status, winners, created_at)
  giveaway_entries(giveaway_id, user_id, entered_at)
  feedback(id, guild_id, kind, author_id, title, content,
           channel_id, message_id, status, created_at)
  feedback_votes(feedback_id, user_id, vote)
  bump_reminders(guild_id, channel_id, bumper_id, remind_at)
  temp_vc_channels(channel_id, guild_id, owner_id, created_at)
          -- 一時 VC (ロビー参加で自動作成される個人用 VC) の記録。
             再起動時に実チャンネルと照合して掃除するために使う
  stats_daily(guild_id, date, metric, channel_id, count)
          -- サーバー統計の日次カウンタ。date は JST の暦日 (YYYY-MM-DD)、
             metric は 'message' / 'join' / 'leave'。'message' のときだけ
             channel_id が意味を持ち (他は 0)

すべてのアクセスは context manager 経由で行い、接続を漏らさない。
"""
import json
import os
import sqlite3
from contextlib import closing, contextmanager

from . import config as app_config


class Database:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_schema(self):
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id INTEGER NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT,
                        PRIMARY KEY (guild_id, key)
                    );
                    CREATE TABLE IF NOT EXISTS user_xp (
                        user_id INTEGER NOT NULL,
                        guild_id INTEGER NOT NULL,
                        xp INTEGER NOT NULL DEFAULT 0,
                        level INTEGER NOT NULL DEFAULT 0,
                        last_message_time REAL NOT NULL DEFAULT 0,
                        recent_content_hashes TEXT,
                        vc_xp INTEGER NOT NULL DEFAULT 0,
                        vc_level INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, guild_id)
                    );
                    CREATE TABLE IF NOT EXISTS infractions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        detail TEXT,
                        punishment TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS giveaways (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        message_id INTEGER,
                        host_id INTEGER NOT NULL,
                        prize TEXT NOT NULL,
                        winner_count INTEGER NOT NULL,
                        ends_at REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        winners TEXT,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS giveaway_entries (
                        giveaway_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        entered_at REAL NOT NULL,
                        PRIMARY KEY (giveaway_id, user_id)
                    );
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        author_id INTEGER NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL,
                        channel_id INTEGER NOT NULL,
                        message_id INTEGER,
                        status TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS feedback_votes (
                        feedback_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        vote INTEGER NOT NULL,
                        PRIMARY KEY (feedback_id, user_id)
                    );
                    CREATE TABLE IF NOT EXISTS bump_reminders (
                        guild_id INTEGER PRIMARY KEY,
                        channel_id INTEGER NOT NULL,
                        bumper_id INTEGER,
                        remind_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS tickets (
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
                    CREATE TABLE IF NOT EXISTS ticket_ratings (
                        ticket_id INTEGER PRIMARY KEY,
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        rating INTEGER NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS xp_weekly_baseline (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        xp INTEGER NOT NULL,
                        PRIMARY KEY (guild_id, user_id)
                    );
                    CREATE TABLE IF NOT EXISTS temp_vc_channels (
                        channel_id INTEGER PRIMARY KEY,
                        guild_id INTEGER NOT NULL,
                        owner_id INTEGER NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS stats_daily (
                        guild_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        metric TEXT NOT NULL,
                        channel_id INTEGER NOT NULL DEFAULT 0,
                        count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (guild_id, date, metric, channel_id)
                    );
                    """
                )
                self._migrate(conn)

    @staticmethod
    def _migrate(conn):
        """既存 DB への列追加などのマイグレーション (既存行は保持する)。"""
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(user_xp)").fetchall()
        }
        if columns and "recent_content_hashes" not in columns:
            conn.execute(
                "ALTER TABLE user_xp ADD COLUMN recent_content_hashes TEXT"
            )
        if columns and "vc_xp" not in columns:
            conn.execute(
                "ALTER TABLE user_xp ADD COLUMN vc_xp INTEGER NOT NULL DEFAULT 0"
            )
        if columns and "vc_level" not in columns:
            conn.execute(
                "ALTER TABLE user_xp"
                " ADD COLUMN vc_level INTEGER NOT NULL DEFAULT 0"
            )

    # ---------- guild_settings ----------

    def get_setting(self, guild_id: int, key: str, default=None):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?",
                (guild_id, key),
            ).fetchone()
        if row is None or row["value"] is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    def set_setting(self, guild_id: int, key: str, value):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value
                """,
                (guild_id, key, json.dumps(value, ensure_ascii=False)),
            )

    # ---------- user_xp ----------

    @staticmethod
    def _parse_hashes(raw) -> list:
        """recent_content_hashes (JSON 配列文字列) をリストに戻す。"""
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(value, list):
            return []
        return [str(h) for h in value]

    def get_xp(self, user_id: int, guild_id: int, create: bool = False) -> dict:
        """ユーザーの XP 行を返す。create=False の読み取りでは行を作らない。

        戻り値には発言トラック (xp / level) と VC トラック (vc_xp /
        vc_level) の両方を含む。読み取り専用なので、どちらのトラックも
        変更しない。
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT xp, level, vc_xp, vc_level,
                       last_message_time, recent_content_hashes
                FROM user_xp
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            ).fetchone()
            if row is None and create:
                conn.execute(
                    """
                    INSERT INTO user_xp (user_id, guild_id, xp, level, last_message_time)
                    VALUES (?, ?, 0, 0, 0)
                    """,
                    (user_id, guild_id),
                )
                return {
                    "xp": 0,
                    "level": 0,
                    "vc_xp": 0,
                    "vc_level": 0,
                    "last_message_time": 0,
                    "recent_content_hashes": [],
                }
        if row is None:
            return {
                "xp": 0,
                "level": 0,
                "vc_xp": 0,
                "vc_level": 0,
                "last_message_time": 0,
                "recent_content_hashes": [],
            }
        return {
            "xp": row["xp"],
            "level": row["level"],
            "vc_xp": row["vc_xp"],
            "vc_level": row["vc_level"],
            "last_message_time": row["last_message_time"],
            "recent_content_hashes": self._parse_hashes(
                row["recent_content_hashes"]
            ),
        }

    def upsert_xp(
        self,
        user_id: int,
        guild_id: int,
        xp: int,
        level: int,
        last_message_time: float,
        recent_content_hashes=None,
    ):
        hashes_json = (
            json.dumps(list(recent_content_hashes))
            if recent_content_hashes is not None
            else None
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_xp (
                    user_id, guild_id, xp, level, last_message_time,
                    recent_content_hashes
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET
                    xp = excluded.xp,
                    level = excluded.level,
                    last_message_time = excluded.last_message_time,
                    recent_content_hashes = excluded.recent_content_hashes
                """,
                (
                    user_id,
                    guild_id,
                    xp,
                    level,
                    last_message_time,
                    hashes_json,
                ),
            )

    def upsert_vc_xp(self, user_id: int, guild_id: int, vc_xp: int, vc_level: int):
        """VC トラックの XP/レベルだけを書き込む。

        発言側の xp / level / last_message_time / recent_content_hashes
        には一切触れない (行が無ければ、発言側はすべて 0 の行を作る)。
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_xp (user_id, guild_id, vc_xp, vc_level)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET
                    vc_xp = excluded.vc_xp,
                    vc_level = excluded.vc_level
                """,
                (user_id, guild_id, vc_xp, vc_level),
            )

    def delete_xp(self, user_id: int, guild_id: int):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM user_xp WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )

    def get_rank(self, user_id: int, guild_id: int) -> int:
        """同点は同順位 (自分より XP が多い人数 + 1)。"""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) + 1 AS rank FROM user_xp
                WHERE guild_id = ? AND xp > (
                    SELECT COALESCE(
                        (SELECT xp FROM user_xp WHERE user_id = ? AND guild_id = ?), 0)
                )
                """,
                (guild_id, user_id, guild_id),
            ).fetchone()
        return row["rank"] if row else 1

    def get_top_users(self, guild_id: int, limit: int = 10, offset: int = 0):
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT user_id, xp, level FROM user_xp
                WHERE guild_id = ?
                ORDER BY xp DESC, user_id ASC
                LIMIT ? OFFSET ?
                """,
                (guild_id, limit, offset),
            ).fetchall()
        return [(r["user_id"], r["xp"], r["level"]) for r in rows]

    def count_users(self, guild_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM user_xp WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        return row["c"] if row else 0

    def get_all_user_xp(self, guild_id: int):
        """ギルド全員の (user_id, 累計 XP) を user_id 順に返す。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT user_id, xp FROM user_xp
                WHERE guild_id = ?
                ORDER BY user_id ASC
                """,
                (guild_id,),
            ).fetchall()
        return [(r["user_id"], r["xp"]) for r in rows]

    # ---------- xp_weekly_baseline (週間ランキングの基準スナップショット) ----------

    def get_xp_baselines(self, guild_id: int) -> dict:
        """週開始時点の累計 XP を {user_id: xp} で返す。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT user_id, xp FROM xp_weekly_baseline
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchall()
        return {r["user_id"]: r["xp"] for r in rows}

    def replace_xp_baselines(self, guild_id: int, rows):
        """ギルドの基準スナップショットを rows で丸ごと差し替える。

        rows は (user_id, xp) の列。削除と挿入は同一トランザクションで
        行うため、途中で失敗しても中途半端な状態にはならない。
        """
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM xp_weekly_baseline WHERE guild_id = ?",
                (guild_id,),
            )
            conn.executemany(
                """
                INSERT INTO xp_weekly_baseline (guild_id, user_id, xp)
                VALUES (?, ?, ?)
                """,
                [(guild_id, int(user_id), int(xp)) for user_id, xp in rows],
            )

    def count_xp_baselines(self, guild_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM xp_weekly_baseline
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        return row["c"] if row else 0

    # ---------- tickets ----------

    def create_ticket(
        self,
        channel_id: int,
        guild_id: int,
        owner_id: int,
        ticket_type: str,
        subject: str,
        now: float,
        message_id=None,
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO tickets (
                    channel_id, guild_id, owner_id, assignee_id,
                    ticket_type, subject, message_id, created_at, last_activity_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    guild_id,
                    owner_id,
                    ticket_type,
                    subject,
                    message_id,
                    now,
                    now,
                ),
            )

    def get_ticket(self, channel_id: int):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_open_ticket_by_owner(self, guild_id: int, owner_id: int):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM tickets WHERE guild_id = ? AND owner_id = ?
                ORDER BY created_at ASC LIMIT 1
                """,
                (guild_id, owner_id),
            ).fetchone()
        return dict(row) if row else None

    def list_tickets(self, guild_id: int = None):
        with self._conn() as conn:
            if guild_id is None:
                rows = conn.execute("SELECT * FROM tickets").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE guild_id = ?",
                    (guild_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def set_ticket_assignee(self, channel_id: int, assignee_id):
        with self._conn() as conn:
            conn.execute(
                "UPDATE tickets SET assignee_id = ? WHERE channel_id = ?",
                (assignee_id, channel_id),
            )

    def set_ticket_message_id(self, channel_id: int, message_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE tickets SET message_id = ? WHERE channel_id = ?",
                (message_id, channel_id),
            )

    def touch_ticket(self, channel_id: int, now: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE tickets SET last_activity_at = ? WHERE channel_id = ?",
                (now, channel_id),
            )

    def delete_ticket(self, channel_id: int):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM tickets WHERE channel_id = ?",
                (channel_id,),
            )

    # ---------- ticket_ratings (閉鎖後の満足度評価) ----------

    def create_ticket_rating_prompt(
        self, ticket_id: int, guild_id: int, user_id: int, now: float
    ) -> bool:
        """評価待ちの行 (rating=0) を作る。すでにあれば False。

        チケット閉鎖時に作成者へ DM で評価ボタンを送る際、誰が・どの
        ギルドのチケットを評価できるかを再起動後も検証できるよう、
        先にこの行を記録する (tickets 行自体は閉鎖時に消えるため)。
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO ticket_ratings (
                    ticket_id, guild_id, user_id, rating, created_at
                ) VALUES (?, ?, ?, 0, ?)
                """,
                (ticket_id, guild_id, user_id, now),
            )
            return cursor.rowcount > 0

    def complete_ticket_rating(
        self, ticket_id: int, user_id: int, rating: int, now: float
    ) -> bool:
        """評価待ちの行を実際の評価 (1〜5) で確定する。

        確定できたら True。評価待ちの行がない・別ユーザーの行・
        評価済みの場合は何もせず False。範囲外の rating は ValueError。
        """
        if not (1 <= rating <= 5):
            raise ValueError(f"rating は 1〜5 で指定してください: {rating}")
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE ticket_ratings SET rating = ?, created_at = ?
                WHERE ticket_id = ? AND user_id = ? AND rating = 0
                """,
                (rating, now, ticket_id, user_id),
            )
            return cursor.rowcount > 0

    def get_ticket_rating(self, ticket_id: int):
        """チケットの評価行 (評価待ち含む) を dict で返す。なければ None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ticket_ratings WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_ticket_rating(self, ticket_id: int):
        """評価行を消す (DM が送れなかった評価待ち行の掃除用)。"""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM ticket_ratings WHERE ticket_id = ?",
                (ticket_id,),
            )

    def ticket_rating_stats(self, guild_id: int) -> dict:
        """ギルドの評価集計。評価待ち (rating=0) は含まない。

        戻り値: {"count": 件数, "average": 平均 (0件なら 0.0),
                 "distribution": {1: 件数, ..., 5: 件数}}
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT rating, COUNT(*) AS c FROM ticket_ratings
                WHERE guild_id = ? AND rating BETWEEN 1 AND 5
                GROUP BY rating
                """,
                (guild_id,),
            ).fetchall()
        distribution = {stars: 0 for stars in range(1, 6)}
        count = 0
        total = 0
        for row in rows:
            distribution[row["rating"]] = row["c"]
            count += row["c"]
            total += row["rating"] * row["c"]
        return {
            "count": count,
            "average": (total / count) if count else 0.0,
            "distribution": distribution,
        }

    # ---------- infractions (自動モデレーションの違反履歴) ----------

    def add_infraction(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        detail,
        punishment: str,
        created_at: float,
    ) -> int:
        """違反を1件記録し、新しい行の id を返す。"""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO infractions (
                    guild_id, user_id, reason, detail, punishment, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, reason, detail, punishment, created_at),
            )
            return cursor.lastrowid

    def count_infractions(self, guild_id: int, user_id: int, since: float) -> int:
        """since (UNIX 時刻) 以降の違反件数。"""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM infractions
                WHERE guild_id = ? AND user_id = ? AND created_at >= ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
        return row["c"] if row else 0

    def recent_infractions(self, guild_id: int, user_id: int, limit: int = 5):
        """新しい順に最大 limit 件の違反履歴を dict のリストで返す。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, guild_id, user_id, reason, detail, punishment,
                       created_at
                FROM infractions
                WHERE guild_id = ? AND user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_infractions(self, guild_id: int, user_id: int) -> int:
        """そのメンバーの違反履歴をすべて消し、削除件数を返す。"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM infractions WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            return cursor.rowcount

    def count_infractions_in_range(
        self, guild_id: int, start_ts: float, end_ts: float
    ) -> int:
        """期間 [start_ts, end_ts) のギルド全体の違反件数 (統計レポート用)。"""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM infractions
                WHERE guild_id = ? AND created_at >= ? AND created_at < ?
                """,
                (guild_id, start_ts, end_ts),
            ).fetchone()
        return row["c"] if row else 0

    # ---------- giveaways (抽選) ----------

    @staticmethod
    def _parse_winners(raw) -> list:
        """winners (JSON 配列文字列) をユーザー ID のリストに戻す。"""
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result

    @classmethod
    def _giveaway_row(cls, row) -> dict:
        result = dict(row)
        result["winners"] = cls._parse_winners(result.get("winners"))
        return result

    def create_giveaway(
        self,
        guild_id: int,
        channel_id: int,
        host_id: int,
        prize: str,
        winner_count: int,
        ends_at: float,
        now: float,
    ) -> int:
        """抽選を作成し、新しい行の id を返す (message_id は投稿後に設定)。"""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO giveaways (
                    guild_id, channel_id, message_id, host_id, prize,
                    winner_count, ends_at, status, winners, created_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, 'active', NULL, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    host_id,
                    prize,
                    winner_count,
                    ends_at,
                    now,
                ),
            )
            return cursor.lastrowid

    def get_giveaway(self, giveaway_id: int):
        """抽選を1件返す (winners はリスト化済み)。なければ None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM giveaways WHERE id = ?",
                (giveaway_id,),
            ).fetchone()
        return self._giveaway_row(row) if row else None

    def set_giveaway_message(self, giveaway_id: int, message_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE giveaways SET message_id = ? WHERE id = ?",
                (message_id, giveaway_id),
            )

    def list_active_giveaways(self, guild_id: int):
        """そのギルドの開催中 (active) の抽選を終了時刻順に返す。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM giveaways
                WHERE guild_id = ? AND status = 'active'
                ORDER BY ends_at ASC, id ASC
                """,
                (guild_id,),
            ).fetchall()
        return [self._giveaway_row(r) for r in rows]

    def list_overdue_giveaways(self, now: float):
        """終了時刻を過ぎた active な抽選をすべて返す (再起動後の回収用)。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM giveaways
                WHERE status = 'active' AND ends_at <= ?
                ORDER BY ends_at ASC, id ASC
                """,
                (now,),
            ).fetchall()
        return [self._giveaway_row(r) for r in rows]

    def toggle_entry(self, giveaway_id: int, user_id: int, now: float) -> bool:
        """参加の付け外し。参加状態になったら True、取り消したら False。"""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM giveaway_entries
                WHERE giveaway_id = ? AND user_id = ?
                """,
                (giveaway_id, user_id),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """
                    DELETE FROM giveaway_entries
                    WHERE giveaway_id = ? AND user_id = ?
                    """,
                    (giveaway_id, user_id),
                )
                return False
            conn.execute(
                """
                INSERT INTO giveaway_entries (giveaway_id, user_id, entered_at)
                VALUES (?, ?, ?)
                """,
                (giveaway_id, user_id, now),
            )
            return True

    def count_entries(self, giveaway_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id = ?",
                (giveaway_id,),
            ).fetchone()
        return row["c"] if row else 0

    def list_entry_user_ids(self, giveaway_id: int):
        """参加者のユーザー ID を参加順に返す。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT user_id FROM giveaway_entries
                WHERE giveaway_id = ?
                ORDER BY entered_at ASC, user_id ASC
                """,
                (giveaway_id,),
            ).fetchall()
        return [r["user_id"] for r in rows]

    def finish_giveaway(self, giveaway_id: int, status: str, winners):
        """抽選を終了状態にし、当選者リスト (ユーザー ID) を保存する。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE giveaways SET status = ?, winners = ? WHERE id = ?",
                (status, json.dumps([int(w) for w in (winners or [])]), giveaway_id),
            )

    def count_giveaways_in_range(
        self, guild_id: int, start_ts: float, end_ts: float
    ) -> int:
        """期間 [start_ts, end_ts) に作成された抽選の件数 (統計レポート用)。"""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM giveaways
                WHERE guild_id = ? AND created_at >= ? AND created_at < ?
                """,
                (guild_id, start_ts, end_ts),
            ).fetchone()
        return row["c"] if row else 0

    # ---------- feedback (提案箱・バグ報告) ----------

    def create_feedback(
        self,
        guild_id: int,
        kind: str,
        author_id: int,
        title: str,
        content: str,
        channel_id: int,
        now: float,
    ) -> int:
        """フィードバックを1件作成し、新しい行の id を返す。

        初期ステータスは kind で決まる (提案='pending'、バグ='open')。
        message_id は投稿後に set_feedback_message で設定する。
        """
        status = "pending" if kind == "suggestion" else "open"
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feedback (
                    guild_id, kind, author_id, title, content,
                    channel_id, message_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    guild_id,
                    kind,
                    author_id,
                    title or "",
                    content,
                    channel_id,
                    status,
                    now,
                ),
            )
            return cursor.lastrowid

    def get_feedback(self, feedback_id: int):
        """フィードバックを1件 dict で返す。なければ None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM feedback WHERE id = ?",
                (feedback_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_feedback_message(self, feedback_id: int, message_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE feedback SET message_id = ? WHERE id = ?",
                (message_id, feedback_id),
            )

    def set_feedback_status(self, feedback_id: int, status: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE feedback SET status = ? WHERE id = ?",
                (status, feedback_id),
            )

    def list_feedback(self, guild_id: int, kind: str = None, limit: int = 10):
        """そのギルドのフィードバックを新しい順に返す (kind で絞り込み可)。"""
        with self._conn() as conn:
            if kind is None:
                rows = conn.execute(
                    """
                    SELECT * FROM feedback WHERE guild_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (guild_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM feedback
                    WHERE guild_id = ? AND kind = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (guild_id, kind, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def set_vote(self, feedback_id: int, user_id: int, vote: int):
        """投票を付け外しし、最新の (賛成数, 反対数) を返す。

        遷移は next_vote に従う: 未投票→その側に投票、同じ側→取り消し、
        反対側→切り替え。
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT vote FROM feedback_votes
                WHERE feedback_id = ? AND user_id = ?
                """,
                (feedback_id, user_id),
            ).fetchone()
            current = row["vote"] if row else None
            new_vote = next_vote(current, vote)
            if new_vote is None:
                conn.execute(
                    """
                    DELETE FROM feedback_votes
                    WHERE feedback_id = ? AND user_id = ?
                    """,
                    (feedback_id, user_id),
                )
            elif current is None:
                conn.execute(
                    """
                    INSERT INTO feedback_votes (feedback_id, user_id, vote)
                    VALUES (?, ?, ?)
                    """,
                    (feedback_id, user_id, new_vote),
                )
            else:
                conn.execute(
                    """
                    UPDATE feedback_votes SET vote = ?
                    WHERE feedback_id = ? AND user_id = ?
                    """,
                    (new_vote, feedback_id, user_id),
                )
            return self._count_votes(conn, feedback_id)

    @staticmethod
    def _count_votes(conn, feedback_id: int):
        rows = conn.execute(
            """
            SELECT vote, COUNT(*) AS c FROM feedback_votes
            WHERE feedback_id = ? GROUP BY vote
            """,
            (feedback_id,),
        ).fetchall()
        up = down = 0
        for row in rows:
            if row["vote"] > 0:
                up = row["c"]
            elif row["vote"] < 0:
                down = row["c"]
        return up, down

    def count_votes(self, feedback_id: int):
        """(賛成数, 反対数) を返す。"""
        with self._conn() as conn:
            return self._count_votes(conn, feedback_id)

    def count_feedback_in_range(
        self, guild_id: int, start_ts: float, end_ts: float
    ) -> int:
        """期間 [start_ts, end_ts) のフィードバック投稿数 (統計レポート用)。

        提案・バグ報告の合計。
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM feedback
                WHERE guild_id = ? AND created_at >= ? AND created_at < ?
                """,
                (guild_id, start_ts, end_ts),
            ).fetchone()
        return row["c"] if row else 0

    # ---------- bump_reminders (DISBOARD の Bump リマインダー) ----------

    def set_bump_reminder(
        self, guild_id: int, channel_id: int, bumper_id, remind_at: float
    ):
        """ギルドの Bump リマインダーを予約する (既存があれば上書き)。

        1ギルドにつき予約は1件だけ。新しく bump されたら予約し直す。
        bumper_id は bump した人の ID (不明なら None)。
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO bump_reminders (
                    guild_id, channel_id, bumper_id, remind_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    bumper_id = excluded.bumper_id,
                    remind_at = excluded.remind_at
                """,
                (guild_id, channel_id, bumper_id, remind_at),
            )

    def get_bump_reminder(self, guild_id: int):
        """ギルドの予約中リマインダーを dict で返す。なければ None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bump_reminders WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_due_bump_reminders(self, now: float):
        """通知時刻 (remind_at) を過ぎた予約をすべて返す (再起動後の回収用)。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM bump_reminders
                WHERE remind_at <= ?
                ORDER BY remind_at ASC, guild_id ASC
                """,
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_bump_reminder(self, guild_id: int):
        """ギルドの予約中リマインダーを消す。"""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM bump_reminders WHERE guild_id = ?",
                (guild_id,),
            )

    # ---------- temp_vc_channels (一時 VC) ----------

    def add_temp_vc_channel(
        self, channel_id: int, guild_id: int, owner_id: int, now: float
    ):
        """作成した一時 VC を記録する。"""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO temp_vc_channels (
                    channel_id, guild_id, owner_id, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (channel_id, guild_id, owner_id, now),
            )

    def get_temp_vc_channel(self, channel_id: int):
        """一時 VC を1件 dict で返す。なければ None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM temp_vc_channels WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_temp_vc_channel_by_owner(self, guild_id: int, owner_id: int):
        """そのギルドでそのメンバーが所有する一時 VC を返す。

        通常は1人1件だけだが、古い順に1件返すことで壊れた状態でも
        決定的に動く。なければ None。
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM temp_vc_channels
                WHERE guild_id = ? AND owner_id = ?
                ORDER BY created_at ASC LIMIT 1
                """,
                (guild_id, owner_id),
            ).fetchone()
        return dict(row) if row else None

    def remove_temp_vc_channel(self, channel_id: int):
        """一時 VC の記録を消す (チャンネル削除時・記録の掃除用)。"""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM temp_vc_channels WHERE channel_id = ?",
                (channel_id,),
            )

    def list_temp_vc_channels(self, guild_id: int = None):
        """一時 VC の記録を作成順に返す (guild_id 省略時は全ギルド)。"""
        with self._conn() as conn:
            if guild_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM temp_vc_channels
                    ORDER BY created_at ASC, channel_id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM temp_vc_channels
                    WHERE guild_id = ?
                    ORDER BY created_at ASC, channel_id ASC
                    """,
                    (guild_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ---------- stats_daily (サーバー統計の日次カウンタ) ----------

    def increment_stat(
        self,
        guild_id: int,
        date: str,
        metric: str,
        channel_id: int = 0,
        amount: int = 1,
    ):
        """日次カウンタを amount だけ増やす (行が無ければ作る)。

        date は JST の暦日 (YYYY-MM-DD)。metric は 'message' / 'join' /
        'leave' など。channel_id は 'message' のときだけ意味がある。
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO stats_daily (guild_id, date, metric, channel_id, count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, date, metric, channel_id)
                DO UPDATE SET count = count + excluded.count
                """,
                (guild_id, date, metric, int(channel_id), int(amount)),
            )

    def get_stats(self, guild_id: int, start_date: str, end_date: str):
        """[start_date, end_date] (両端含む) の日次カウンタを dict の列で返す。

        date は YYYY-MM-DD 文字列なので、そのまま文字列比較で範囲を絞れる。
        並びは日付・指標・チャンネルの昇順 (決定的にするため)。
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT date, metric, channel_id, count FROM stats_daily
                WHERE guild_id = ? AND date >= ? AND date <= ?
                ORDER BY date ASC, metric ASC, channel_id ASC
                """,
                (guild_id, start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- バックアップ ----------

    def backup_to(self, dest_path: str):
        """DB の一貫したコピーを dest_path に書き出す。

        sqlite3 の backup API を使うため、Bot が稼働中 (書き込みが
        あり得る状態) でも途中の状態を掴まないコピーが得られる。
        単なるファイルコピーは避けること。
        """
        directory = os.path.dirname(dest_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as source:
            with closing(sqlite3.connect(dest_path)) as dest:
                source.backup(dest)


def next_vote(current, clicked):
    """投票ボタンの遷移を決める純粋関数。

    current は現在の投票 (+1/-1、未投票は None)、clicked は押した側 (+1/-1)。
    戻り値は新しい投票 (+1/-1) か、取り消しなら None。
    - 未投票で押す → その側に投票
    - 同じ側をもう一度押す → 取り消し (None)
    - 反対側を押す → 切り替え
    """
    if current is None:
        return clicked
    if current == clicked:
        return None
    return clicked


# Cog から使うモジュールレベルのシングルトン
db = Database(app_config.DB_PATH)
