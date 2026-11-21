"""チケットの永続ストア (SQLite)。

チャンネルの topic 文字列に頼らず、開いているチケットを
DBで管理する。discord には依存しない独立モジュール。
"""

import os
import sqlite3
import time
from contextlib import closing

TICKET_DB_PATH = "data/tickets.db"


class TicketStore:
    def __init__(self, db_path: str = TICKET_DB_PATH):
        self.db_path = db_path

        directory = os.path.dirname(db_path)

        if directory:
            os.makedirs(directory, exist_ok=True)

        self.init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tickets (
                        channel_id INTEGER PRIMARY KEY,
                        guild_id INTEGER NOT NULL,
                        owner_id INTEGER NOT NULL,
                        assignee_id INTEGER,
                        ticket_type TEXT DEFAULT 'other',
                        created_at REAL DEFAULT 0,
                        last_activity_at REAL DEFAULT 0
                    )
                """)

    def create_ticket(
        self,
        channel_id: int,
        guild_id: int,
        owner_id: int,
        ticket_type: str = "other",
        assignee_id=None,
        now: float = None
    ) -> dict:
        if now is None:
            now = time.time()

        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tickets
                    (
                        channel_id,
                        guild_id,
                        owner_id,
                        assignee_id,
                        ticket_type,
                        created_at,
                        last_activity_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        channel_id,
                        guild_id,
                        owner_id,
                        assignee_id,
                        ticket_type,
                        now,
                        now
                    )
                )

        return self.get_ticket(channel_id)

    def get_ticket(self, channel_id: int):
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row

            row = conn.execute(
                """
                SELECT channel_id, guild_id, owner_id, assignee_id,
                       ticket_type, created_at, last_activity_at
                FROM tickets
                WHERE channel_id = ?
                """,
                (channel_id,)
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def get_open_ticket_by_owner(
        self,
        guild_id: int,
        owner_id: int
    ):
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row

            row = conn.execute(
                """
                SELECT channel_id, guild_id, owner_id, assignee_id,
                       ticket_type, created_at, last_activity_at
                FROM tickets
                WHERE guild_id = ?
                AND owner_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (guild_id, owner_id)
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def set_assignee(
        self,
        channel_id: int,
        assignee_id
    ):
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tickets
                    SET assignee_id = ?
                    WHERE channel_id = ?
                    """,
                    (assignee_id, channel_id)
                )

    def touch_activity(
        self,
        channel_id: int,
        now: float = None
    ):
        if now is None:
            now = time.time()

        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tickets
                    SET last_activity_at = ?
                    WHERE channel_id = ?
                    """,
                    (now, channel_id)
                )

    def delete_ticket(self, channel_id: int):
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    DELETE FROM tickets
                    WHERE channel_id = ?
                    """,
                    (channel_id,)
                )

    def list_tickets(self):
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                """
                SELECT channel_id, guild_id, owner_id, assignee_id,
                       ticket_type, created_at, last_activity_at
                FROM tickets
                """
            ).fetchall()

        return [dict(row) for row in rows]
