"""単一 SQLite DB (data/bot.db) へのアクセス層。

テーブル:
  guild_settings(guild_id, key, value)  -- value は JSON 文字列
  user_xp(user_id, guild_id, xp, level, last_message_time)
  tickets(channel_id, guild_id, owner_id, assignee_id, ticket_type,
          subject, message_id, created_at, last_activity_at)

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
                        PRIMARY KEY (user_id, guild_id)
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
                    """
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

    def get_xp(self, user_id: int, guild_id: int, create: bool = False) -> dict:
        """ユーザーの XP 行を返す。create=False の読み取りでは行を作らない。"""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT xp, level, last_message_time FROM user_xp
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
                return {"xp": 0, "level": 0, "last_message_time": 0}
        if row is None:
            return {"xp": 0, "level": 0, "last_message_time": 0}
        return {
            "xp": row["xp"],
            "level": row["level"],
            "last_message_time": row["last_message_time"],
        }

    def upsert_xp(
        self,
        user_id: int,
        guild_id: int,
        xp: int,
        level: int,
        last_message_time: float,
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_xp (user_id, guild_id, xp, level, last_message_time)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET
                    xp = excluded.xp,
                    level = excluded.level,
                    last_message_time = excluded.last_message_time
                """,
                (user_id, guild_id, xp, level, last_message_time),
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


# Cog から使うモジュールレベルのシングルトン
db = Database(app_config.DB_PATH)
