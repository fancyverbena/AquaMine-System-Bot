"""自動モデレーション (自動処罰) 機能。

検知対象:
  - NG ワード (ギルドごとのリスト、正規化して部分一致)
  - スパム (短時間の連投 / 同一内容の繰り返し、判定はメモリ上のみ)
  - メンション乱用 (ユーザー+ロールのメンション数がしきい値以上)
  - Discord 招待リンク (設定で無効化可能)
  - 新規メンバーのリンク投稿 (参加から設定日数の間は http(s) リンク不可)

違反すると発言を削除して違反履歴 (DB) に記録し、直近30日の違反回数に
応じて段階的に処罰する: 10回目まで=警告のみ、11回目=タイムアウト10分、
12回目=キック、13回目以降=BAN。管理者・manage_messages 権限持ち・サーバーオーナーと
対象外チャンネルは処罰しない。

既定では無効。設定を済ませてから /automod toggle で有効化する。
"""
import datetime
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from app import settings
from app.db import db
from app.ui import COLOR_DANGER, COLOR_WARN, reply_error
from cogs.leveling import content_hash, normalize_content

# スパム判定のパラメータ (バッファはメモリ上のみ、再起動で消える)
FLOOD_COUNT = 5
FLOOD_WINDOW_SECONDS = 10.0
REPEAT_COUNT = 3
REPEAT_WINDOW_SECONDS = 60.0
BUFFER_WINDOW_SECONDS = REPEAT_WINDOW_SECONDS

# エスカレーションのパラメータ
INFRACTION_WINDOW_SECONDS = 30 * 24 * 3600  # 直近30日の違反で数える
BURST_SUPPRESS_SECONDS = 60.0  # 記録後この間は追加で記録しない
TIMEOUT_MINUTES = 10

NGWORD_MAX_LENGTH = 50
MENTION_LIMIT_MAX = settings.MENTION_LIMIT_HARD_MAX
LINK_RESTRICT_DAYS_MAX = settings.LINK_RESTRICT_DAYS_HARD_MAX

REASON_LABELS = {
    "ngword": "NGワード",
    "flood": "スパム（連投）",
    "repeat": "スパム（同一内容の繰り返し）",
    "mention": "メンション乱用",
    "invite": "招待リンク",
    "new_member_link": "新規メンバーのリンク制限",
}

PUNISHMENT_LABELS = {
    "warn": "警告",
    "timeout": f"タイムアウト（{TIMEOUT_MINUTES}分）",
    "kick": "キック",
    "ban": "BAN",
}

# 招待リンクのパターン (スラッシュまで含む形だけを対象にする)
INVITE_PATTERNS = (
    (re.compile(r"discord\.gg/", re.IGNORECASE), "discord.gg"),
    (re.compile(r"discord\.com/invite/", re.IGNORECASE), "discord.com/invite"),
    (
        re.compile(r"discordapp\.com/invite/", re.IGNORECASE),
        "discordapp.com/invite",
    ),
)
INVITE_REGEX = re.compile(
    r"(?:discord\.gg|discord\.com/invite|discordapp\.com/invite)/",
    re.IGNORECASE,
)

# http(s) リンクの検知 (スキーム付き URL のみ。ベアドメインは対象外)
LINK_REGEX = re.compile(r"https?://", re.IGNORECASE)


def punishment_for_count(count: int) -> str:
    """直近30日の違反回数 (今回を含む) に対する処罰キー。

    10回目までは警告のみで許容し、11回目から処罰が重くなる。
    """
    if count <= 10:
        return "warn"
    if count == 11:
        return "timeout"
    if count == 12:
        return "kick"
    return "ban"


def spam_reason(events, now: float):
    """スパム判定。events は (timestamp, content_hash) の列 (古い順)。

    理由キー: 'flood' (FLOOD_WINDOW_SECONDS 秒以内に FLOOD_COUNT 件以上) /
    'repeat' (最新発言と同一ハッシュが REPEAT_WINDOW_SECONDS 秒以内に
    REPEAT_COUNT 件以上)。どちらでもなければ None。
    """
    events = list(events or [])
    if not events:
        return None
    flood_hits = sum(
        1 for ts, _ in events if now - ts <= FLOOD_WINDOW_SECONDS
    )
    if flood_hits >= FLOOD_COUNT:
        return "flood"
    latest_hash = events[-1][1]
    repeat_hits = sum(
        1
        for ts, hashed in events
        if hashed == latest_hash and now - ts <= REPEAT_WINDOW_SECONDS
    )
    if repeat_hits >= REPEAT_COUNT:
        return "repeat"
    return None


def find_ngword(content, ngwords):
    """本文に含まれる NG ワードを返す (なければ None)。

    本文・ワードとも cogs.leveling の正規化 (空白畳み込み+小文字化) を
    かけてから部分一致で判定する。戻り値は登録されているワードそのもの。
    """
    normalized = normalize_content(content)
    if not normalized:
        return None
    for word in ngwords or []:
        normalized_word = normalize_content(word)
        if normalized_word and normalized_word in normalized:
            return word
    return None


def find_invite(content):
    """招待リンクがあればドメインパターン名を返す (なければ None)。

    URL そのものではなく 'discord.gg' のようなパターン名だけを返すので、
    ログに実際の招待コードを含めずに済む。
    """
    text = content or ""
    for pattern, label in INVITE_PATTERNS:
        if pattern.search(text):
            return label
    return None


def contains_link(content) -> bool:
    """本文に http:// または https:// のリンクが含まれるか (大文字小文字不問)。

    スキームなしのベアドメイン (example.com など) はリンクとみなさない。
    """
    return bool(LINK_REGEX.search(content or ""))


def is_link_restricted(joined_at, now, days) -> bool:
    """参加から days 日以内ならリンク制限の対象かどうか。

    days が 0 以下なら制限自体が無効。joined_at が None (参加日時不明)
    の場合は処罰しない (フェイルオープン)。ちょうど days 日経過した
    時点で制限は解ける。
    """
    if days is None or days <= 0:
        return False
    if joined_at is None:
        return False
    return now - joined_at < datetime.timedelta(days=days)


def link_restriction_violation(content, joined_at, now, days,
                               allow_links=False):
    """新規メンバーのリンク制限の違反判定。(理由キー, 詳細) か None を返す。

    allow_links が True (宣伝許可チャンネル) の場合は検知しない。
    詳細は DM・ログ向けに「参加から何日間はリンク不可」である旨を含める。
    """
    if allow_links:
        return None
    if not contains_link(content):
        return None
    if not is_link_restricted(joined_at, now, days):
        return None
    return (
        "new_member_link",
        f"サーバー参加から{days}日間はリンクを投稿できません",
    )


def content_violation(content, ngwords, mention_total, mention_limit,
                      invites_enabled, allow_invites=False):
    """本文ベースの違反判定。(理由キー, 詳細) か None を返す。

    スパム判定はバッファが要るので spam_reason 側で別に行う。
    詳細はログ表示用 (招待リンクはパターン名のみ、URL は含めない)。
    allow_invites が True (宣伝許可チャンネル) の場合は招待リンクだけ
    検知をスキップし、NGワードとメンションは通常どおり判定する。
    """
    word = find_ngword(content, ngwords)
    if word is not None:
        return "ngword", word
    if invites_enabled and not allow_invites:
        invite = find_invite(content)
        if invite is not None:
            return "invite", invite
    if mention_limit > 0 and mention_total >= mention_limit:
        return "mention", f"{mention_total}件のメンション"
    return None


class AutoModCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) -> [(timestamp, content_hash)] (古い順)
        self._spam_buffers: dict = {}
        # (guild_id, user_id) -> 最後に違反を記録した時刻
        self._last_infraction_at: dict = {}

    # ---------- 検知と処罰 ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        guild = message.guild
        member = message.author
        if not isinstance(member, discord.Member):
            return
        if not settings.get_automod_enabled(guild.id):
            return
        # 免除: オーナー / 管理者 / メッセージ管理権限 / 対象外チャンネル
        if member.id == guild.owner_id:
            return
        permissions = member.guild_permissions
        if permissions.administrator or permissions.manage_messages:
            return
        if message.channel.id in settings.get_automod_exempt_channel_ids(
            guild.id
        ):
            return

        now = time.time()
        content = message.content or ""

        # スパムバッファを更新 (ウィンドウ外の古いイベントは捨てる)
        key = (guild.id, member.id)
        buffer = self._spam_buffers.setdefault(key, [])
        buffer.append((now, content_hash(content)))
        cutoff = now - BUFFER_WINDOW_SECONDS
        buffer[:] = [event for event in buffer if event[0] >= cutoff]

        mention_total = len(message.mentions) + len(message.role_mentions)
        allow_invites = message.channel.id in (
            settings.get_automod_ad_channel_ids(guild.id)
        )
        violation = content_violation(
            content,
            settings.get_automod_ngwords(guild.id),
            mention_total,
            settings.get_automod_mention_limit(guild.id),
            settings.get_automod_invites_enabled(guild.id),
            allow_invites=allow_invites,
        )
        if violation is None:
            # 新規メンバーのリンク制限 (宣伝許可チャンネルでは検知しない)
            violation = link_restriction_violation(
                content,
                member.joined_at,
                discord.utils.utcnow(),
                settings.get_automod_link_restrict_days(guild.id),
                allow_links=allow_invites,
            )
        if violation is None:
            reason = spam_reason(buffer, now)
            if reason is not None:
                violation = (reason, REASON_LABELS[reason])
        if violation is None:
            return
        reason, detail = violation

        # バースト抑制: 記録直後は削除だけして追加記録しない
        # (1回の連投で処罰が何段も進まないようにする)
        last = self._last_infraction_at.get(key, 0.0)
        if now - last < BURST_SUPPRESS_SECONDS:
            await self._delete_quietly(message)
            return

        self._last_infraction_at[key] = now
        self._spam_buffers.pop(key, None)
        await self._delete_quietly(message)

        count = (
            db.count_infractions(
                guild.id, member.id, now - INFRACTION_WINDOW_SECONDS
            )
            + 1
        )
        punishment = punishment_for_count(count)
        db.add_infraction(guild.id, member.id, reason, detail, punishment, now)
        applied, note = await self._apply_punishment(member, reason, punishment)
        await self._send_log(
            guild, member, reason, detail, punishment, count, applied, note
        )
        await self._send_dm(guild, member, reason, detail, punishment)

    @staticmethod
    async def _delete_quietly(message: discord.Message):
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    async def _apply_punishment(self, member: discord.Member, reason: str,
                                punishment: str):
        """処罰を実行し (適用できたか, 備考) を返す。失敗しても例外を出さない。"""
        if punishment == "warn":
            # 警告は DM とログ通知そのものが処罰の実体
            return True, None
        guild = member.guild
        if member.id == guild.owner_id:
            return False, "サーバーオーナーのため処罰できません"
        me = guild.me
        if me is not None and member.top_role >= me.top_role:
            return False, "Bot と同等以上のロールを持つため処罰できません"
        audit_reason = f"自動処罰: {REASON_LABELS.get(reason, reason)}"
        try:
            if punishment == "timeout":
                await member.timeout(
                    datetime.timedelta(minutes=TIMEOUT_MINUTES),
                    reason=audit_reason,
                )
            elif punishment == "kick":
                await member.kick(reason=audit_reason)
            elif punishment == "ban":
                await member.ban(reason=audit_reason, delete_message_seconds=0)
        except discord.Forbidden:
            return False, "Bot の権限が不足しています"
        except discord.HTTPException as e:
            return False, f"処罰の適用に失敗しました ({e})"
        return True, None

    async def _send_log(self, guild, member, reason, detail, punishment,
                        count, applied, note):
        channel_id = settings.get_automod_log_channel_id(guild.id)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="🛡️ 自動処罰",
            color=COLOR_DANGER if applied else COLOR_WARN,
        )
        embed.add_field(
            name="対象", value=f"{member.mention} ({member})", inline=False
        )
        embed.add_field(
            name="理由", value=REASON_LABELS.get(reason, reason), inline=True
        )
        embed.add_field(name="詳細", value=detail or "-", inline=True)
        embed.add_field(
            name="処罰",
            value=PUNISHMENT_LABELS.get(punishment, punishment)
            + ("" if applied else "（失敗）"),
            inline=True,
        )
        embed.add_field(
            name="違反回数（直近30日）", value=f"{count} 回", inline=True
        )
        if note:
            embed.add_field(name="備考", value=note, inline=False)
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def _send_dm(self, guild, member, reason, detail, punishment):
        text = (
            f"⚠️ {guild.name} で自動モデレーションにより "
            f"**{PUNISHMENT_LABELS.get(punishment, punishment)}** "
            "の処罰を受けました。\n"
            f"理由: {REASON_LABELS.get(reason, reason)}"
        )
        if detail:
            text += f"（{detail}）"
        try:
            await member.send(text)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ---------- /automod グループ (管理者) ----------

    automod_group = app_commands.Group(
        name="automod",
        description="自動モデレーション管理（管理者専用）",
        default_permissions=discord.Permissions(administrator=True),
    )

    def _guard_admin_guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        member = interaction.user
        return (
            isinstance(member, discord.Member)
            and member.guild_permissions.administrator
        )

    async def _reject(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
        else:
            await reply_error(interaction, "このコマンドは管理者専用です。")

    @automod_group.command(
        name="toggle", description="自動モデレーションの有効/無効を切り替えます"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def automod_toggle(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        new_value = not settings.get_automod_enabled(gid)
        settings.set_value(gid, settings.AUTOMOD_ENABLED, new_value)
        text = (
            "✅ 自動モデレーションを "
            + ("**有効** にしました。" if new_value else "**無効** にしました。")
        )
        if new_value and not settings.get_automod_log_channel_id(gid):
            text += "\n処罰ログの送信先が未設定です。`/automod log-channel` で設定してください。"
        await interaction.response.send_message(text, ephemeral=True)

    @automod_group.command(
        name="status", description="自動モデレーションの現在の設定を表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def automod_status(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        gid = guild.id
        limit = settings.get_automod_mention_limit(gid)
        log_id = settings.get_automod_log_channel_id(gid)
        log_channel = guild.get_channel(log_id) if log_id else None
        lines = [
            f"状態: {'**有効**' if settings.get_automod_enabled(gid) else '**無効**'}",
            f"NGワード: {len(settings.get_automod_ngwords(gid))} 件",
            f"メンション上限: {limit if limit > 0 else '無効（0）'}",
            "招待リンク検知: "
            + ("ON" if settings.get_automod_invites_enabled(gid) else "OFF"),
            "新規メンバーのリンク制限: "
            + (
                f"参加から {settings.get_automod_link_restrict_days(gid)} 日間"
                if settings.get_automod_link_restrict_days(gid) > 0
                else "無効（0）"
            ),
            "ログチャンネル: "
            + (log_channel.mention if log_channel else "未設定"),
            "対象外チャンネル: "
            f"{len(settings.get_automod_exempt_channel_ids(gid))} 件",
            "宣伝許可チャンネル: "
            f"{len(settings.get_automod_ad_channel_ids(gid))} 件",
        ]
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )

    @automod_group.command(
        name="ngword-add", description="NGワードを追加します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(word="追加するNGワード")
    async def automod_ngword_add(
        self, interaction: discord.Interaction, word: str
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        normalized = (word or "").strip().casefold()
        if not normalized:
            await reply_error(interaction, "空のワードは登録できません。")
            return
        if len(normalized) > NGWORD_MAX_LENGTH:
            await reply_error(
                interaction,
                f"NGワードは {NGWORD_MAX_LENGTH} 文字以内で指定してください。",
            )
            return
        words = settings.get_automod_ngwords(gid)
        if normalized in words:
            await reply_error(
                interaction, f"「{normalized}」はすでに登録されています。"
            )
            return
        words.append(normalized)
        settings.set_value(gid, settings.AUTOMOD_NGWORDS, words)
        await interaction.response.send_message(
            f"✅ NGワード「{normalized}」を追加しました（現在 {len(words)} 件）。",
            ephemeral=True,
        )

    @automod_group.command(
        name="ngword-remove", description="NGワードを削除します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(word="削除するNGワード")
    async def automod_ngword_remove(
        self, interaction: discord.Interaction, word: str
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        normalized = (word or "").strip().casefold()
        words = settings.get_automod_ngwords(gid)
        if normalized not in words:
            await reply_error(
                interaction, f"「{normalized}」は登録されていません。"
            )
            return
        words.remove(normalized)
        settings.set_value(gid, settings.AUTOMOD_NGWORDS, words)
        await interaction.response.send_message(
            f"✅ NGワード「{normalized}」を削除しました（現在 {len(words)} 件）。",
            ephemeral=True,
        )

    @automod_group.command(
        name="ngword-list", description="登録中のNGワードを表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def automod_ngword_list(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        words = settings.get_automod_ngwords(gid)
        if not words:
            await interaction.response.send_message(
                "NGワードは登録されていません。", ephemeral=True
            )
            return
        lines = [f"登録中のNGワード（{len(words)} 件）:"]
        lines += [f"- {word}" for word in words]
        if not settings.automod_ngwords_customized(gid):
            lines.append(
                "（デフォルトのセットを使用中です。追加・削除すると"
                "この内容がこのサーバーのリストとして保存されます）"
            )
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )

    @automod_group.command(
        name="log-channel", description="処罰ログを送るチャンネルを設定します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="処罰ログの送信先チャンネル")
    async def automod_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.AUTOMOD_LOG_CHANNEL_ID, channel.id
        )
        await interaction.response.send_message(
            f"✅ 処罰ログの送信先を {channel.mention} に設定しました。",
            ephemeral=True,
        )

    @automod_group.command(
        name="mentions",
        description="メンション乱用のしきい値を設定します（0で検知無効）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(count="違反になるメンション数（0〜50）")
    async def automod_mentions(
        self, interaction: discord.Interaction, count: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        if not (0 <= count <= MENTION_LIMIT_MAX):
            await reply_error(
                interaction,
                f"メンション上限は 0 〜 {MENTION_LIMIT_MAX} で指定してください。",
            )
            return
        settings.set_value(
            interaction.guild.id, settings.AUTOMOD_MENTION_LIMIT, count
        )
        await interaction.response.send_message(
            "✅ メンション上限を "
            + (f"{count} 件に設定しました。" if count > 0 else "0（検知無効）にしました。"),
            ephemeral=True,
        )

    @automod_group.command(
        name="invites", description="招待リンク検知の有効/無効を切り替えます"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(enabled="招待リンクを検知するか")
    async def automod_invites(
        self, interaction: discord.Interaction, enabled: bool
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.AUTOMOD_INVITES_ENABLED, enabled
        )
        await interaction.response.send_message(
            "✅ 招待リンク検知を "
            + ("**有効** にしました。" if enabled else "**無効** にしました。"),
            ephemeral=True,
        )

    @automod_group.command(
        name="link-restrict",
        description="新規メンバーのリンク制限の日数を設定します（0で無効）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(days="参加後リンクを制限する日数（0〜90）")
    async def automod_link_restrict(
        self, interaction: discord.Interaction, days: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        if not (0 <= days <= LINK_RESTRICT_DAYS_MAX):
            await reply_error(
                interaction,
                f"リンク制限の日数は 0 〜 {LINK_RESTRICT_DAYS_MAX} で指定してください。",
            )
            return
        settings.set_value(
            interaction.guild.id, settings.AUTOMOD_LINK_RESTRICT_DAYS, days
        )
        await interaction.response.send_message(
            "✅ 新規メンバーのリンク制限を "
            + (
                f"参加から {days} 日間に設定しました。"
                if days > 0
                else "無効（0）にしました。"
            ),
            ephemeral=True,
        )

    @automod_group.command(
        name="exempt-channel",
        description="チャンネルの対象外/対象内を切り替えます",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="切り替えるチャンネル")
    async def automod_exempt_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        ids = settings.get_automod_exempt_channel_ids(gid)
        if channel.id in ids:
            ids.remove(channel.id)
            settings.set_value(gid, settings.AUTOMOD_EXEMPT_CHANNEL_IDS, ids)
            text = f"✅ {channel.mention} を自動モデレーションの**対象に戻しました**。"
        else:
            ids.append(channel.id)
            settings.set_value(gid, settings.AUTOMOD_EXEMPT_CHANNEL_IDS, ids)
            text = f"✅ {channel.mention} を自動モデレーションの**対象外にしました**。"
        await interaction.response.send_message(text, ephemeral=True)

    @automod_group.command(
        name="ad-channel",
        description="チャンネルの宣伝（招待リンク）許可/不許可を切り替えます",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="切り替えるチャンネル")
    async def automod_ad_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        ids = settings.get_automod_ad_channel_ids(gid)
        if channel.id in ids:
            ids.remove(channel.id)
            settings.set_value(gid, settings.AUTOMOD_AD_CHANNEL_IDS, ids)
            text = f"✅ {channel.mention} の宣伝を**不許可に戻しました**（招待リンク検知が有効になります）。"
        else:
            ids.append(channel.id)
            settings.set_value(gid, settings.AUTOMOD_AD_CHANNEL_IDS, ids)
            text = f"✅ {channel.mention} を**宣伝許可チャンネルにしました**（招待リンクと新規メンバーのリンク制限は検知されません。NGワード・スパム・メンション検知は有効なままです）。"
        await interaction.response.send_message(text, ephemeral=True)

    @automod_group.command(
        name="history", description="メンバーの違反履歴を表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="対象メンバー")
    async def automod_history(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        now = time.time()
        count = db.count_infractions(
            gid, member.id, now - INFRACTION_WINDOW_SECONDS
        )
        entries = db.recent_infractions(gid, member.id, 5)
        lines = [f"{member.mention} の違反履歴（直近30日: {count} 件）"]
        if entries:
            for entry in entries:
                date = datetime.datetime.fromtimestamp(
                    entry["created_at"]
                ).strftime("%Y-%m-%d %H:%M")
                reason_label = REASON_LABELS.get(
                    entry["reason"], entry["reason"]
                )
                punishment_label = PUNISHMENT_LABELS.get(
                    entry["punishment"], entry["punishment"]
                )
                detail = (
                    f"（{entry['detail']}）" if entry["detail"] else ""
                )
                lines.append(
                    f"・{date} {reason_label}{detail} → {punishment_label}"
                )
        else:
            lines.append("履歴はありません。")
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )

    @automod_group.command(
        name="reset", description="メンバーの違反履歴をリセットします"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="対象メンバー")
    async def automod_reset(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        removed = db.clear_infractions(interaction.guild.id, member.id)
        await interaction.response.send_message(
            f"✅ {member.mention} の違反履歴をリセットしました"
            f"（{removed} 件削除）。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoModCog(bot))
