import json
import os
import random
import sqlite3
import time
import discord
from discord.ext import commands


DB_PATH = "data/leveling.db"
GUILD_SETTINGS_PATH = "config/guild_settings.json"


class LevelingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = DB_PATH
        self.guild_settings_path = GUILD_SETTINGS_PATH

        os.makedirs("data", exist_ok=True)
        os.makedirs("config", exist_ok=True)

        self.init_db()

        self.cooldown_cache = {}
        self.cooldown_seconds = 30

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_xp (
                user_id INTEGER,
                guild_id INTEGER,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                last_message_time REAL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        """)

        conn.commit()
        conn.close()

        print("✅ レベリングDBを初期化しました。")

    def load_guild_settings(self):
        if not os.path.exists(self.guild_settings_path):
            self.save_json(self.guild_settings_path, {})
            return {}

        try:
            with open(
                self.guild_settings_path,
                "r",
                encoding="utf-8"
            ) as f:
                content = f.read().strip()

            if not content:
                self.save_json(self.guild_settings_path, {})
                return {}

            data = json.loads(content)

            if not isinstance(data, dict):
                self.save_json(self.guild_settings_path, {})
                return {}

            return data

        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError
        ):
            self.save_json(self.guild_settings_path, {})
            return {}

    def save_json(self, path, data):
        os.makedirs(
            os.path.dirname(path),
            exist_ok=True
        )

        temp_path = f"{path}.tmp"

        with open(
            temp_path,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            temp_path,
            path
        )

    def get_user_data(
        self,
        user_id: int,
        guild_id: int
    ):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            SELECT xp, level, last_message_time
            FROM user_xp
            WHERE user_id = ? AND guild_id = ?
            """,
            (user_id, guild_id)
        )

        result = c.fetchone()

        if result is None:
            c.execute(
                """
                INSERT INTO user_xp
                (user_id, guild_id, xp, level, last_message_time)
                VALUES (?, ?, 0, 0, 0)
                """,
                (user_id, guild_id)
            )

            conn.commit()
            conn.close()

            return {
                "xp": 0,
                "level": 0,
                "last_message_time": 0
            }

        conn.close()

        return {
            "xp": result[0],
            "level": result[1],
            "last_message_time": result[2]
        }

    def update_user_data(
        self,
        user_id: int,
        guild_id: int,
        xp: int,
        level: int,
        last_time: float
    ):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            UPDATE user_xp
            SET xp = ?, level = ?, last_message_time = ?
            WHERE user_id = ? AND guild_id = ?
            """,
            (
                xp,
                level,
                last_time,
                user_id,
                guild_id
            )
        )

        conn.commit()
        conn.close()

    def get_rank(
        self,
        user_id: int,
        guild_id: int
    ) -> int:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            SELECT COUNT(*) + 1
            FROM user_xp
            WHERE guild_id = ?
            AND xp > (
                SELECT xp
                FROM user_xp
                WHERE user_id = ?
                AND guild_id = ?
            )
            """,
            (
                guild_id,
                user_id,
                guild_id
            )
        )

        result = c.fetchone()

        conn.close()

        return result[0] if result else 1

    def get_top_users(
        self,
        guild_id: int,
        limit: int = 10
    ):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            SELECT user_id, xp, level
            FROM user_xp
            WHERE guild_id = ?
            ORDER BY xp DESC
            LIMIT ?
            """,
            (
                guild_id,
                limit
            )
        )

        result = c.fetchall()

        conn.close()

        return result

    def calc_xp_for_level(
        self,
        level: int
    ) -> int:
        return (
            5 * (level ** 2)
            + 50 * level
            + 100
        )

    def calc_level(
        self,
        total_xp: int
    ) -> int:
        level = 0

        while True:
            needed = self.calc_xp_for_level(
                level + 1
            )

            if total_xp < needed:
                break

            level += 1

        return level

    def get_level_up_xp(
        self,
        level: int
    ) -> int:
        return self.calc_xp_for_level(
            level + 1
        )

    def is_spam(
        self,
        user_id: int,
        guild_id: int
    ) -> bool:
        now = time.time()
        key = f"{user_id}_{guild_id}"

        if key in self.cooldown_cache:
            last_time = self.cooldown_cache[key]

            if now - last_time < self.cooldown_seconds:
                return True

        self.cooldown_cache[key] = now

        return False

    def get_level_roles(
        self,
        guild_id: int
    ) -> dict:
        data = self.load_guild_settings()

        guild_data = data.get(
            str(guild_id),
            {}
        )

        return guild_data.get(
            "level_roles",
            {}
        )

    def save_level_role(
        self,
        guild_id: int,
        level: int,
        role_id: int
    ):
        data = self.load_guild_settings()

        guild_key = str(guild_id)

        if guild_key not in data:
            data[guild_key] = {}

        if "level_roles" not in data[guild_key]:
            data[guild_key]["level_roles"] = {}

        data[guild_key]["level_roles"][
            str(level)
        ] = str(role_id)

        self.save_json(
            self.guild_settings_path,
            data
        )

    async def check_and_assign_roles(
        self,
        member: discord.Member,
        new_level: int
    ):
        guild = member.guild

        level_roles = self.get_level_roles(
            guild.id
        )

        for level_str, role_id_str in level_roles.items():
            try:
                level = int(level_str)
                role_id = int(role_id_str)
            except (ValueError, TypeError):
                continue

            if new_level >= level:
                role = guild.get_role(role_id)

                if role and role not in member.roles:
                    try:
                        await member.add_roles(
                            role,
                            reason=f"レベル {level} 達成による自動付与"
                        )

                        print(
                            f"✅ {member} に"
                            f"ロール {role.name} を付与しました"
                            f"（レベル {level}）"
                        )

                    except discord.Forbidden:
                        print(
                            f"⚠️ ロール {role.name} を"
                            f"付与する権限がありません。"
                        )

                    except discord.HTTPException as e:
                        print(
                            f"⚠️ ロール付与エラー: {e}"
                        )

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):
        if message.author.bot:
            return

        if not message.guild:
            return

        guild = message.guild
        user = message.author

        if self.is_spam(
            user.id,
            guild.id
        ):
            return

        data = self.get_user_data(
            user.id,
            guild.id
        )

        current_xp = data["xp"]
        current_level = data["level"]

        gain_xp = random.randint(
            10,
            25
        )

        new_xp = current_xp + gain_xp

        new_level = self.calc_level(
            new_xp
        )

        level_up = (
            new_level > current_level
        )

        now = time.time()

        self.update_user_data(
            user.id,
            guild.id,
            new_xp,
            new_level,
            now
        )

        if level_up:
            try:
                await message.channel.send(
                    f"🎉 {user.mention} が "
                    f"**レベル {new_level}** に上がりました！"
                )
            except discord.HTTPException:
                pass

            await self.check_and_assign_roles(
                user,
                new_level
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        LevelingCog(bot)
    )