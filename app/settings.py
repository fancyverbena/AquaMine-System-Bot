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
AUTOMOD_LINK_RESTRICT_DAYS = "automod_link_restrict_days"
AUTOMOD_EXEMPT_CHANNEL_IDS = "automod_exempt_channel_ids"
AUTOMOD_AD_CHANNEL_IDS = "automod_ad_channel_ids"
ROLE_PANEL_ROLE_IDS = "role_panel_role_ids"
ROLE_PANEL_EXCLUSIVE = "role_panel_exclusive"
SUGGESTION_CHANNEL_ID = "suggestion_channel_id"
BUG_CHANNEL_ID = "bug_channel_id"
BUMP_REMINDER_ENABLED = "bump_reminder_enabled"
BUMP_NOTIFY_ROLE_ID = "bump_notify_role_id"
BACKUP_CHANNEL_ID = "backup_channel_id"
BACKUP_INTERVAL_HOURS = "backup_interval_hours"
BACKUP_LAST_AT = "backup_last_at"
TICKET_RATING_ENABLED = "ticket_rating_enabled"
TICKET_NOTIFY_ROLES = "ticket_notify_roles"
WELCOME_CHANNEL_ID = "welcome_channel_id"
WELCOME_MESSAGE = "welcome_message"
WELCOME_DM_ENABLED = "welcome_dm_enabled"
AUDIT_LOG_CHANNEL_ID = "audit_log_channel_id"
AUDIT_MESSAGE_DELETE = "audit_message_delete"
AUDIT_MESSAGE_EDIT = "audit_message_edit"
AUDIT_VOICE = "audit_voice"
WEEKLY_RANKING_CHANNEL_ID = "weekly_ranking_channel_id"
WEEKLY_RANKING_LAST_WEEK = "weekly_ranking_last_week"
TEMP_VC_LOBBY_ID = "temp_vc_lobby_id"
STATS_CHANNEL_ID = "stats_channel_id"
STATS_LAST_WEEK = "stats_last_week"

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
DEFAULT_AUTOMOD_LINK_RESTRICT_DAYS = 7
LINK_RESTRICT_DAYS_HARD_MAX = 90

# ロールパネルの上限 (ボタンの並び順 = リストの順序)
MAX_ROLE_PANEL_ROLES = 20

# DB 自動バックアップのデフォルトと許容範囲 (間隔は時間単位、0 で自動投稿オフ)
DEFAULT_BACKUP_INTERVAL_HOURS = 24
BACKUP_INTERVAL_HARD_MAX = 168

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


def get_automod_link_restrict_days(guild_id: int) -> int:
    """新規メンバーのリンク制限の日数 (参加からこの日数はリンク投稿不可)。

    0 で制限無効、範囲外・壊れた値はデフォルト。
    """
    value = _get_int(
        guild_id,
        AUTOMOD_LINK_RESTRICT_DAYS,
        DEFAULT_AUTOMOD_LINK_RESTRICT_DAYS,
    )
    if not (0 <= value <= LINK_RESTRICT_DAYS_HARD_MAX):
        return DEFAULT_AUTOMOD_LINK_RESTRICT_DAYS
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


def get_suggestion_channel_id(guild_id: int) -> int:
    """提案箱の投稿先チャンネル ID (0 は未設定)。"""
    value = _get_int(guild_id, SUGGESTION_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_bug_channel_id(guild_id: int) -> int:
    """バグ報告の投稿先チャンネル ID (0 は未設定)。"""
    value = _get_int(guild_id, BUG_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_bump_reminder_enabled(guild_id: int) -> bool:
    """Bump リマインダーの有効/無効 (既定は有効、壊れた値は有効扱い)。

    検知は受動的で、bump の成功メッセージを見たときだけ予約する。
    """
    value = get(guild_id, BUMP_REMINDER_ENABLED, True)
    return value if isinstance(value, bool) else True


def get_bump_notify_role_id(guild_id: int) -> int:
    """Bump リマインダーでメンションするロール ID (0 は未設定)。"""
    value = _get_int(guild_id, BUMP_NOTIFY_ROLE_ID, 0)
    return value if value > 0 else 0


def get_backup_channel_id(guild_id: int) -> int:
    """DB バックアップの投稿先チャンネル ID (0 は未設定)。"""
    value = _get_int(guild_id, BACKUP_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_backup_interval_hours(guild_id: int) -> int:
    """自動バックアップの間隔 (時間)。0 で自動投稿オフ、範囲外はデフォルト。"""
    value = _get_int(
        guild_id, BACKUP_INTERVAL_HOURS, DEFAULT_BACKUP_INTERVAL_HOURS
    )
    if not (0 <= value <= BACKUP_INTERVAL_HARD_MAX):
        return DEFAULT_BACKUP_INTERVAL_HOURS
    return value


def get_backup_last_at(guild_id: int) -> float:
    """最後に自動/手動バックアップを投稿先へ送った時刻 (未実施は 0)。"""
    try:
        return float(get(guild_id, BACKUP_LAST_AT, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def get_ticket_rating_enabled(guild_id: int) -> bool:
    """チケット閉鎖後の満足度評価 (DM で ⭐1〜5 を聞く) の有効/無効。

    既定は有効、壊れた値は有効扱い。
    """
    value = get(guild_id, TICKET_RATING_ENABLED, True)
    return value if isinstance(value, bool) else True


def get_ticket_notify_roles(guild_id: int) -> dict:
    """チケット種類ごとの担当通知ロール {種類キー: ロールID (int)}。

    壊れた値や数値化できないロール ID は落とす。種類キーの妥当性は
    呼び出し側 (cogs.tickets の TICKET_TYPES) で検証する。
    """
    roles = get(guild_id, TICKET_NOTIFY_ROLES, {})
    if not isinstance(roles, dict):
        return {}
    result = {}
    for ticket_type, role_id in roles.items():
        try:
            role_id = int(role_id)
        except (TypeError, ValueError):
            continue
        if role_id > 0:
            result[str(ticket_type)] = role_id
    return result


def get_ticket_notify_role(guild_id: int, ticket_type: str) -> int:
    """その種類の担当通知ロール ID (0 は未設定)。"""
    return get_ticket_notify_roles(guild_id).get(ticket_type, 0)


def set_ticket_notify_role(guild_id: int, ticket_type: str, role_id):
    """種類ごとの担当通知ロールを設定する。role_id が 0/None なら解除。"""
    roles = get_ticket_notify_roles(guild_id)
    if role_id:
        roles[ticket_type] = int(role_id)
    else:
        roles.pop(ticket_type, None)
    set_value(guild_id, TICKET_NOTIFY_ROLES, roles)


def get_welcome_channel_id(guild_id: int) -> int:
    """ウェルカムメッセージの投稿先チャンネル ID (0 は未設定)。"""
    value = _get_int(guild_id, WELCOME_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_welcome_message(guild_id: int) -> str:
    """ウェルカムメッセージの文面テンプレート (未設定・壊れた値は空文字)。

    空文字は「デフォルト文面を使う」という意味で、実際の文面への
    解決は cogs.serverlog の effective_welcome_template が行う。
    使えるプレースホルダは {member} {name} {server} {count}。
    """
    value = get(guild_id, WELCOME_MESSAGE, "")
    return value if isinstance(value, str) else ""


def get_welcome_dm_enabled(guild_id: int) -> bool:
    """参加時にウェルカムメッセージを DM でも送るか (既定は無効)。"""
    value = get(guild_id, WELCOME_DM_ENABLED, False)
    return value if isinstance(value, bool) else False


def get_audit_log_channel_id(guild_id: int) -> int:
    """監査ログの投稿先チャンネル ID (0 は未設定)。"""
    value = _get_int(guild_id, AUDIT_LOG_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_audit_message_delete_enabled(guild_id: int) -> bool:
    """メッセージ削除を監査ログに記録するか (既定は有効、壊れた値は有効扱い)。"""
    value = get(guild_id, AUDIT_MESSAGE_DELETE, True)
    return value if isinstance(value, bool) else True


def get_audit_message_edit_enabled(guild_id: int) -> bool:
    """メッセージ編集を監査ログに記録するか (既定は有効、壊れた値は有効扱い)。"""
    value = get(guild_id, AUDIT_MESSAGE_EDIT, True)
    return value if isinstance(value, bool) else True


def get_audit_voice_enabled(guild_id: int) -> bool:
    """VC の出入りを監査ログに記録するか (既定は無効 — 件数が多いため)。"""
    value = get(guild_id, AUDIT_VOICE, False)
    return value if isinstance(value, bool) else False


def get_weekly_ranking_channel_id(guild_id: int) -> int:
    """週間 XP ランキングの自動投稿先チャンネル ID (0 は未設定 = 投稿オフ)。"""
    value = _get_int(guild_id, WEEKLY_RANKING_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_weekly_ranking_last_week(guild_id: int) -> str:
    """最後に週間ランキングを投稿した週のキー (週開始の月曜日の日付)。

    未投稿・壊れた値は空文字。二重投稿の防止に使う。
    """
    value = get(guild_id, WEEKLY_RANKING_LAST_WEEK, "")
    return value if isinstance(value, str) else ""


def get_temp_vc_lobby_id(guild_id: int) -> int:
    """一時 VC のロビーチャンネル ID (0 は未設定 = 機能オフ)。"""
    value = _get_int(guild_id, TEMP_VC_LOBBY_ID, 0)
    return value if value > 0 else 0


def get_stats_channel_id(guild_id: int) -> int:
    """サーバー統計レポートの自動投稿先チャンネル ID (0 は未設定 = 投稿オフ)。"""
    value = _get_int(guild_id, STATS_CHANNEL_ID, 0)
    return value if value > 0 else 0


def get_stats_last_week(guild_id: int) -> str:
    """最後に統計レポートを自動投稿した週のキー (週開始の月曜日の日付)。

    未投稿・壊れた値は空文字。二重投稿の防止に使う (週間ランキングの
    weekly_ranking_last_week とは独立に管理する)。
    """
    value = get(guild_id, STATS_LAST_WEEK, "")
    return value if isinstance(value, str) else ""


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
