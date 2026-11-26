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
XP_MIN = "xp_min"
XP_MAX = "xp_max"
XP_COOLDOWN_SECONDS = "xp_cooldown_seconds"
XP_MIN_LENGTH = "xp_min_length"

DEFAULT_AUTO_CLOSE_HOURS = 48
MAX_AUTO_CLOSE_HOURS = 720

# レベリング (XP) 設定のデフォルトと許容範囲
DEFAULT_XP_MIN = 10
DEFAULT_XP_MAX = 25
DEFAULT_XP_COOLDOWN_SECONDS = 30
DEFAULT_XP_MIN_LENGTH = 3
XP_HARD_MAX = 10000
COOLDOWN_HARD_MAX = 3600
MIN_LENGTH_HARD_MAX = 200

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


def _get_int(guild_id: int, key: str, default: int) -> int:
    try:
        return int(get(guild_id, key, default))
    except (TypeError, ValueError):
        return default


def get_xp_config(guild_id: int) -> dict:
    """ギルドの XP 設定を有効値として返す (壊れた値はデフォルトにフォールバック)。"""
    xp_min = _get_int(guild_id, XP_MIN, DEFAULT_XP_MIN)
    xp_max = _get_int(guild_id, XP_MAX, DEFAULT_XP_MAX)
    cooldown = _get_int(
        guild_id, XP_COOLDOWN_SECONDS, DEFAULT_XP_COOLDOWN_SECONDS
    )
    min_length = _get_int(guild_id, XP_MIN_LENGTH, DEFAULT_XP_MIN_LENGTH)
    if not (0 <= xp_min <= xp_max <= XP_HARD_MAX):
        xp_min, xp_max = DEFAULT_XP_MIN, DEFAULT_XP_MAX
    if not (0 <= cooldown <= COOLDOWN_HARD_MAX):
        cooldown = DEFAULT_XP_COOLDOWN_SECONDS
    if not (0 <= min_length <= MIN_LENGTH_HARD_MAX):
        min_length = DEFAULT_XP_MIN_LENGTH
    return {
        "xp_min": xp_min,
        "xp_max": xp_max,
        "xp_cooldown_seconds": cooldown,
        "xp_min_length": min_length,
    }


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
