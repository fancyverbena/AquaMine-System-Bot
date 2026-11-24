"""環境変数とパスの定義。

v2 では設定・XP・チケットをすべて単一の SQLite DB (data/bot.db) に保存する。
JSON の設定ファイルはルール本文 (config/rules.json) のみ。
"""
import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

_guild_raw = os.getenv("GUILD_ID")
GUILD_ID = None
if _guild_raw:
    try:
        GUILD_ID = int(_guild_raw)
    except ValueError:
        print("⚠️ GUILD_ID が不正な値です。グローバル同期にフォールバックします。")

DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "bot.db")
RULES_PATH = os.path.join("config", "rules.json")
TRANSCRIPT_DIR = os.path.join(DATA_DIR, "transcripts")
