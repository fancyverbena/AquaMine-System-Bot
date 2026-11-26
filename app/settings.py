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
VC_XP_PER_MINUTE = "vc_xp_per_minute"
AUTOMOD_ENABLED = "automod_enabled"
AUTOMOD_NGWORDS = "automod_ngwords"
AUTOMOD_LOG_CHANNEL_ID = "automod_log_channel_id"
AUTOMOD_MENTION_LIMIT = "automod_mention_limit"
AUTOMOD_INVITES_ENABLED = "automod_invites_enabled"
AUTOMOD_EXEMPT_CHANNEL_IDS = "automod_exempt_channel_ids"
AUTOMOD_AD_CHANNEL_IDS = "automod_ad_channel_ids"
ROLE_PANEL_ROLE_IDS = "role_panel_role_ids"
ROLE_PANEL_EXCLUSIVE = "role_panel_exclusive"

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
DEFAULT_VC_XP_PER_MINUTE = 2
VC_XP_HARD_MAX = 1000

# 自動モデレーション設定のデフォルトと許容範囲
DEFAULT_AUTOMOD_MENTION_LIMIT = 5
MENTION_LIMIT_HARD_MAX = 50

# ロールパネルの上限 (ボタンの並び順 = リストの順序)
MAX_ROLE_PANEL_ROLES = 20

# NG ワードのデフォルトセット。ギルドが独自のリストを保存するまでは
# これが使われる (追加・削除した時点で、その内容がギルドのリストとして
# 保存される)。判定は正規化 (空白畳み込み+小文字化) 後の部分一致。
DEFAULT_NGWORDS = [
    "死ね",
    "タヒ",
    "fuck",
    "bitch",
    "nigger",
    "nigga",
    "faggot",
    "free nitro",
    "無料nitro",
    "無料ニトロ",
    "nitro無料",
    "ニトロ無料",
]

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


def get_vc_xp_per_minute(guild_id: int) -> int:
    """VC 滞在で1分ごとに得る XP (0 で VC XP 無効、範囲外はデフォルト)。"""
    value = _get_int(guild_id, VC_XP_PER_MINUTE, DEFAULT_VC_XP_PER_MINUTE)
    if not (0 <= value <= VC_XP_HARD_MAX):
        return DEFAULT_VC_XP_PER_MINUTE
    return value


def get_automod_enabled(guild_id: int) -> bool:
    """自動モデレーションの有効/無効 (既定は無効、壊れた値は無効扱い)。"""
    value = get(guild_id, AUTOMOD_ENABLED, False)
    return value if isinstance(value, bool) else False


def get_automod_invites_enabled(guild_id: int) -> bool:
    """招待リンク検知の有効/無効 (既定は有効、壊れた値は有効扱い)。"""
    value = get(guild_id, AUTOMOD_INVITES_ENABLED, True)
    return value if isinstance(value, bool) else True


def get_automod_ngwords(guild_id: int) -> list:
    """NG ワードの有効なリスト。

    ギルドが独自リストを保存していればそれを (文字列でない要素は除外)、
    未保存ならデフォルトセットのコピーを返す。空リストを保存すれば
    NG ワードなしになる。
    """
    words = get(guild_id, AUTOMOD_NGWORDS, None)
    if not isinstance(words, list):
        return list(DEFAULT_NGWORDS)
    return [w for w in words if isinstance(w, str)]


def automod_ngwords_customized(guild_id: int) -> bool:
    """ギルドが独自の NG ワードリストを保存済みかどうか。"""
    return isinstance(get(guild_id, AUTOMOD_NGWORDS, None), list)


def get_automod_log_channel_id(guild_id: int) -> int:
    value = _get_int(guild_id, AUTOMOD_LOG_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_automod_mention_limit(guild_id: int) -> int:
    """メンション乱用のしきい値 (0 で検知無効、範囲外はデフォルト)。"""
    value = _get_int(
        guild_id, AUTOMOD_MENTION_LIMIT, DEFAULT_AUTOMOD_MENTION_LIMIT
    )
    if not (0 <= value <= MENTION_LIMIT_HARD_MAX):
        return DEFAULT_AUTOMOD_MENTION_LIMIT
    return value


def get_automod_exempt_channel_ids(guild_id: int) -> list:
    """自動モデレーション対象外チャンネルの ID リスト。"""
    ids = get(guild_id, AUTOMOD_EXEMPT_CHANNEL_IDS, [])
    if not isinstance(ids, list):
        return []
    result = []
    for item in ids:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def get_automod_ad_channel_ids(guild_id: int) -> list:
    """宣伝 (招待リンク) が許可されるチャンネルの ID リスト。

    このリストのチャンネルでは招待リンク検知だけが無効になる
    (NGワード・スパム・メンション検知は引き続き有効)。
    """
    ids = get(guild_id, AUTOMOD_AD_CHANNEL_IDS, [])
    if not isinstance(ids, list):
        return []
    result = []
    for item in ids:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def get_role_panel_role_ids(guild_id: int) -> list:
    """ロールパネルに並べるロール ID のリスト (順序 = ボタンの並び順)。"""
    ids = get(guild_id, ROLE_PANEL_ROLE_IDS, [])
    if not isinstance(ids, list):
        return []
    result = []
    for item in ids:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def get_role_panel_exclusive(guild_id: int) -> bool:
    """ロールパネルの排他モード (既定は無効、壊れた値は無効扱い)。

    有効時はパネルのロールを付けると、他のパネルロールが外れる。
    """
    value = get(guild_id, ROLE_PANEL_EXCLUSIVE, False)
    return value if isinstance(value, bool) else False


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
