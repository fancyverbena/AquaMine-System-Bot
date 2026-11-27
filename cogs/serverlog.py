"""ウェルカムメッセージと監査ログ。

ウェルカム:
  メンバーが参加したら、設定したチャンネルへ歓迎メッセージ (Embed) を
  投稿する。文面はテンプレートで差し替え可能で、{member} {name}
  {server} {count} のプレースホルダが使える。任意で同じ文面を
  参加者本人へ DM でも送れる (既定はオフ)。

監査ログ:
  設定した専用チャンネルへ、メンバーの退出・メッセージの削除/編集・
  VC の出入りを記録する。項目ごとにオン/オフでき、既定は
  削除=ON / 編集=ON / VC=OFF (VC は件数が多いため)。
  Bot の投稿と DM は対象外で、監査ログチャンネル自体の出来事も
  記録しない (ログ投稿への反応でログが増えるのを防ぐ)。
  削除/編集の内容は Discord のキャッシュにある場合だけ分かり、
  古いメッセージなどは「内容不明（キャッシュなし）」と記録される。
"""
import re

import discord
from discord import app_commands
from discord.ext import commands

from app import settings
from app.ui import (
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARN,
    base_embed,
    reply_error,
)

# 文面未設定時に使うデフォルトのウェルカムメッセージ
DEFAULT_WELCOME_MESSAGE = (
    "{member} さん、{server} へようこそ！🎉\n"
    "現在のメンバー数は {count} 人です。まずはルールチャンネルを確認してね。"
)

# キャッシュに内容が残っていない場合の表示
UNKNOWN_CONTENT_TEXT = "内容不明（キャッシュなし）"

# ウェルカム文面の上限 (Embed の説明文上限 4096 に余裕を持たせた値)
MAX_WELCOME_MESSAGE_LENGTH = 2000

# 監査ログの Embed フィールドに収めるための内容の上限
MAX_LOG_CONTENT_LENGTH = 1000

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


def render_welcome(
    template, *, member_mention, name, server, count
) -> str:
    """ウェルカム文面のプレースホルダを置換する純粋関数。

    {member} {name} {server} {count} を置換し、それ以外の
    {…} はそのまま残す。
    """
    values = {
        "member": str(member_mention),
        "name": str(name),
        "server": str(server),
        "count": str(count),
    }

    def _replace(match):
        return values.get(match.group(1), match.group(0))

    return _PLACEHOLDER_RE.sub(_replace, template or "")


def effective_welcome_template(stored) -> str:
    """保存済みテンプレートから実際に使う文面を決める純粋関数。

    未設定 (空文字) や文字列でない値ならデフォルト文面を返す。
    """
    if isinstance(stored, str) and stored.strip():
        return stored
    return DEFAULT_WELCOME_MESSAGE


def content_or_unknown(content) -> str:
    """ログ表示用の本文。空なら「内容不明（キャッシュなし）」。"""
    if content and str(content).strip():
        return str(content)
    return UNKNOWN_CONTENT_TEXT


def should_log_edit(before_content, after_content) -> bool:
    """編集をログすべきかどうか (本文が変わった場合だけ True)。

    Embed の展開など本文が変わらない更新は False になる。
    """
    return (before_content or "") != (after_content or "")


def truncate_content(content, limit: int = MAX_LOG_CONTENT_LENGTH) -> str:
    """Embed フィールドに収まるよう本文を切り詰める。"""
    text = str(content)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class ServerLogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- 共通 ----------

    async def _fetch_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _send_audit(self, guild: discord.Guild, embed: discord.Embed):
        """監査ログチャンネルが設定されていれば Embed を投稿する。"""
        channel_id = settings.get_audit_log_channel_id(guild.id)
        if not channel_id:
            return
        channel = await self._fetch_channel(channel_id)
        if channel is None:
            return
        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            print(
                f"⚠️ ギルド {guild.id} の監査ログ投稿に失敗: {error}"
            )

    # ---------- ウェルカム ----------

    def _render_for_member(self, member: discord.Member) -> str:
        guild = member.guild
        template = effective_welcome_template(
            settings.get_welcome_message(guild.id)
        )
        return render_welcome(
            template,
            member_mention=member.mention,
            name=member.display_name,
            server=guild.name,
            count=guild.member_count,
        )

    def _build_welcome_embed(self, member: discord.Member) -> discord.Embed:
        embed = base_embed(
            "ようこそ！", self._render_for_member(member), COLOR_SUCCESS
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="アカウント作成日",
            value=discord.utils.format_dt(member.created_at, "F"),
            inline=False,
        )
        embed.add_field(
            name="メンバー数",
            value=f"{member.guild.member_count} 人",
            inline=False,
        )
        embed.set_footer(text=f"ユーザーID: {member.id}")
        return embed

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        gid = guild.id
        if settings.get_welcome_dm_enabled(gid):
            try:
                await member.send(self._render_for_member(member))
            except (discord.Forbidden, discord.HTTPException):
                pass  # DM が閉じられている場合は何もしない
        channel_id = settings.get_welcome_channel_id(gid)
        if not channel_id:
            return
        channel = await self._fetch_channel(channel_id)
        if channel is None:
            return
        try:
            await channel.send(
                embed=self._build_welcome_embed(member),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, users=True, roles=False
                ),
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            print(
                f"⚠️ ギルド {gid} のウェルカム投稿に失敗: {error}"
            )

    # ---------- 監査ログ ----------

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        if not settings.get_audit_log_channel_id(guild.id):
            return
        embed = base_embed("🚪 メンバー退出", color=COLOR_WARN)
        embed.description = (
            f"{member.mention}（{member.display_name} / ID: {member.id}）"
            " がサーバーを退出しました。"
        )
        if member.joined_at is not None:
            embed.add_field(
                name="参加日",
                value=discord.utils.format_dt(member.joined_at, "F"),
                inline=False,
            )
        embed.add_field(
            name="メンバー数",
            value=f"{guild.member_count} 人",
            inline=False,
        )
        await self._send_audit(guild, embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None:
            return
        if message.author is None or message.author.bot:
            return
        gid = message.guild.id
        audit_channel_id = settings.get_audit_log_channel_id(gid)
        if not audit_channel_id:
            return
        if message.channel.id == audit_channel_id:
            return
        if not settings.get_audit_message_delete_enabled(gid):
            return
        embed = base_embed("🗑️ メッセージ削除", color=COLOR_DANGER)
        embed.add_field(
            name="投稿者",
            value=f"{message.author.mention}（ID: {message.author.id}）",
            inline=False,
        )
        embed.add_field(
            name="チャンネル", value=message.channel.mention, inline=False
        )
        embed.add_field(
            name="内容",
            value=truncate_content(content_or_unknown(message.content)),
            inline=False,
        )
        await self._send_audit(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ):
        if after.guild is None:
            return
        if after.author is None or after.author.bot:
            return
        gid = after.guild.id
        audit_channel_id = settings.get_audit_log_channel_id(gid)
        if not audit_channel_id:
            return
        if after.channel.id == audit_channel_id:
            return
        if not settings.get_audit_message_edit_enabled(gid):
            return
        if not should_log_edit(before.content, after.content):
            return
        embed = base_embed("✏️ メッセージ編集", color=COLOR_PRIMARY)
        embed.add_field(
            name="投稿者",
            value=f"{after.author.mention}（ID: {after.author.id}）",
            inline=False,
        )
        embed.add_field(
            name="チャンネル",
            value=(
                f"{after.channel.mention}"
                f"（[メッセージへジャンプ]({after.jump_url})）"
            ),
            inline=False,
        )
        embed.add_field(
            name="編集前",
            value=truncate_content(content_or_unknown(before.content)),
            inline=False,
        )
        embed.add_field(
            name="編集後",
            value=truncate_content(content_or_unknown(after.content)),
            inline=False,
        )
        await self._send_audit(after.guild, embed)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        guild = member.guild
        gid = guild.id
        if not settings.get_audit_log_channel_id(gid):
            return
        if not settings.get_audit_voice_enabled(gid):
            return
        before_channel = before.channel
        after_channel = after.channel
        if before_channel is None and after_channel is not None:
            action = f"🔊 {after_channel.mention} に接続しました"
        elif after_channel is None and before_channel is not None:
            action = f"🔇 {before_channel.mention} から切断しました"
        elif (
            before_channel is not None
            and after_channel is not None
            and before_channel.id != after_channel.id
        ):
            action = (
                f"🔀 {before_channel.mention} から "
                f"{after_channel.mention} へ移動しました"
            )
        else:
            return  # ミュート等の状態変化だけの場合は記録しない
        embed = base_embed("🎙️ ボイスチャンネル", color=COLOR_PRIMARY)
        embed.description = (
            f"{member.mention}（{member.display_name}）が {action}。"
        )
        await self._send_audit(guild, embed)

    # ---------- 管理者ガード (他の Cog と同じ方式) ----------

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

    # ---------- /welcome グループ (管理者) ----------

    welcome_group = app_commands.Group(
        name="welcome",
        description="ウェルカムメッセージ管理（管理者専用）",
        default_permissions=discord.Permissions(administrator=True),
    )

    @welcome_group.command(
        name="channel",
        description="ウェルカムメッセージの投稿先チャンネルを設定します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="ウェルカムメッセージを投稿するチャンネル")
    async def welcome_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.WELCOME_CHANNEL_ID, channel.id
        )
        await interaction.response.send_message(
            f"✅ ウェルカムメッセージの投稿先を {channel.mention} に設定しました。",
            ephemeral=True,
        )

    @welcome_group.command(
        name="message",
        description="ウェルカムメッセージの文面を設定します（defaultで初期文面に戻す）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        text="文面（{member} {name} {server} {count} が使えます）。"
        "default と入力すると初期文面に戻します"
    )
    async def welcome_message(
        self, interaction: discord.Interaction, text: str
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        if text.strip().casefold() == "default":
            settings.set_value(gid, settings.WELCOME_MESSAGE, "")
            await interaction.response.send_message(
                "✅ ウェルカムメッセージを初期文面に戻しました。",
                ephemeral=True,
            )
            return
        if len(text) > MAX_WELCOME_MESSAGE_LENGTH:
            await reply_error(
                interaction,
                f"文面は {MAX_WELCOME_MESSAGE_LENGTH} 文字以内で指定してください。",
            )
            return
        settings.set_value(gid, settings.WELCOME_MESSAGE, text)
        await interaction.response.send_message(
            "✅ ウェルカムメッセージの文面を設定しました。"
            "確認は `/welcome test` でできます。",
            ephemeral=True,
        )

    @welcome_group.command(
        name="dm",
        description="参加時にウェルカムメッセージをDMでも送るかを切り替えます",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_dm(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        new_value = not settings.get_welcome_dm_enabled(gid)
        settings.set_value(gid, settings.WELCOME_DM_ENABLED, new_value)
        await interaction.response.send_message(
            "✅ ウェルカムDMを "
            + ("**有効** にしました。" if new_value else "**無効** にしました。"),
            ephemeral=True,
        )

    @welcome_group.command(
        name="test",
        description="ウェルカムメッセージのプレビューを投稿先チャンネルに投稿します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_test(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        channel_id = settings.get_welcome_channel_id(gid)
        if not channel_id:
            await reply_error(
                interaction,
                "投稿先が未設定です。先に `/welcome channel` で設定してください。",
            )
            return
        channel = await self._fetch_channel(channel_id)
        if channel is None:
            await reply_error(
                interaction, "投稿先チャンネルが見つかりませんでした。"
            )
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            await reply_error(interaction, "メンバー情報が取得できませんでした。")
            return
        try:
            await channel.send(
                embed=self._build_welcome_embed(member),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, users=True, roles=False
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            await reply_error(
                interaction,
                "プレビューの投稿に失敗しました。チャンネルの権限を確認してください。",
            )
            return
        await interaction.response.send_message(
            f"✅ {channel.mention} にプレビューを投稿しました。",
            ephemeral=True,
        )

    # ---------- /auditlog グループ (管理者) ----------

    auditlog_group = app_commands.Group(
        name="auditlog",
        description="監査ログ管理（管理者専用）",
        default_permissions=discord.Permissions(administrator=True),
    )

    @auditlog_group.command(
        name="channel", description="監査ログの投稿先チャンネルを設定します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="監査ログを投稿するチャンネル")
    async def auditlog_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.AUDIT_LOG_CHANNEL_ID, channel.id
        )
        await interaction.response.send_message(
            f"✅ 監査ログの投稿先を {channel.mention} に設定しました。",
            ephemeral=True,
        )

    @auditlog_group.command(
        name="toggle", description="監査ログの記録対象を切り替えます"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(event="切り替える記録対象")
    @app_commands.choices(
        event=[
            app_commands.Choice(name="メッセージ削除", value="delete"),
            app_commands.Choice(name="メッセージ編集", value="edit"),
            app_commands.Choice(name="ボイスチャンネル出入り", value="voice"),
        ]
    )
    async def auditlog_toggle(
        self, interaction: discord.Interaction, event: str
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        targets = {
            "delete": (
                settings.AUDIT_MESSAGE_DELETE,
                settings.get_audit_message_delete_enabled,
                "メッセージ削除",
            ),
            "edit": (
                settings.AUDIT_MESSAGE_EDIT,
                settings.get_audit_message_edit_enabled,
                "メッセージ編集",
            ),
            "voice": (
                settings.AUDIT_VOICE,
                settings.get_audit_voice_enabled,
                "ボイスチャンネル出入り",
            ),
        }
        target = targets.get(event)
        if target is None:
            await reply_error(interaction, "記録対象の指定が正しくありません。")
            return
        key, getter, label = target
        new_value = not getter(gid)
        settings.set_value(gid, key, new_value)
        await interaction.response.send_message(
            f"✅ 監査ログの「{label}」を "
            + ("**有効** にしました。" if new_value else "**無効** にしました。"),
            ephemeral=True,
        )

    @auditlog_group.command(
        name="status", description="監査ログとウェルカムの現在の設定を表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def auditlog_status(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        gid = guild.id

        def on_off(enabled: bool) -> str:
            return "ON" if enabled else "OFF"

        audit_id = settings.get_audit_log_channel_id(gid)
        audit_channel = guild.get_channel(audit_id) if audit_id else None
        welcome_id = settings.get_welcome_channel_id(gid)
        welcome_channel = (
            guild.get_channel(welcome_id) if welcome_id else None
        )
        lines = [
            "監査ログチャンネル: "
            + (audit_channel.mention if audit_channel else "未設定"),
            "メッセージ削除: "
            + on_off(settings.get_audit_message_delete_enabled(gid)),
            "メッセージ編集: "
            + on_off(settings.get_audit_message_edit_enabled(gid)),
            "ボイスチャンネル出入り: "
            + on_off(settings.get_audit_voice_enabled(gid)),
            "ウェルカムチャンネル: "
            + (welcome_channel.mention if welcome_channel else "未設定"),
            "ウェルカムDM: " + on_off(settings.get_welcome_dm_enabled(gid)),
            "ウェルカム文面: "
            + (
                "カスタム"
                if settings.get_welcome_message(gid)
                else "デフォルト"
            ),
        ]
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerLogCog(bot))
