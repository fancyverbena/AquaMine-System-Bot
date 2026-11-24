"""ギルド設定のキーとデフォルト値。

設定はすべて DB (guild_settings) に JSON で保存される。
"""
from .db import db

VERIFIED_ROLE_ID = "verified_role_id"
SUPPORT_ROLE_ID = "support_role_id"
TICKET_CATEGORY_ID = "ticket_category_id"
LOG_CHANNEL_ID = "log_channel_id"
LEVELUP_CHANNEL_ID = "levelup_channel_id"
AUTO_CLOSE_HOURS = "auto_close_hours"
LEVEL_ROLES = "level_roles"

DEFAULT_AUTO_CLOSE_HOURS = 48
MAX_AUTO_CLOSE_HOURS = 720

# 表示名 (セットアップパネルのチェックリスト用)
LABELS = {
    VERIFIED_ROLE_ID: "認証ロール",
    SUPPORT_ROLE_ID: "サポートロール",
    TICKET_CATEGORY_ID: "チケットカテゴリ",
    LOG_CHANNEL_ID: "ログチャンネル",
    LEVELUP_CHANNEL_ID: "レベル通知チャンネル",
    AUTO_CLOSE_HOURS: "自動クローズ",
}


def get(guild_id: int, key: str, default=None):
    return db.get_setting(guild_id, key, default)


def set_value(guild_id: int, key: str, value):
    db.set_setting(guild_id, key, value)


def get_auto_close_hours(guild_id: int) -> int:
    try:
        return int(get(guild_id, AUTO_CLOSE_HOURS, DEFAULT_AUTO_CLOSE_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_AUTO_CLOSE_HOURS


def get_level_roles(guild_id: int) -> dict:
    roles = get(guild_id, LEVEL_ROLES, {})
    return roles if isinstance(roles, dict) else {}


def set_level_role(guild_id: int, level: int, role_id: int):
    roles = get_level_roles(guild_id)
    roles[str(level)] = str(role_id)
    set_value(guild_id, LEVEL_ROLES, roles)


def remove_level_role(guild_id: int, level: int) -> bool:
    roles = get_level_roles(guild_id)
    if str(level) not in roles:
        return False
    del roles[str(level)]
    set_value(guild_id, LEVEL_ROLES, roles)
    return True
