"""レベリング機能 (v2)。

XP は発言ごとに付与される。入手量・クールダウン・最小文字数はギルド設定
(/level config で変更可能、DB の guild_settings に保存)。
スパム対策として、テキストなし・短すぎる発言・直前と同一内容の連投には
XP を付与しない (判定は xp_block_reason に集約)。
レベル L に必要な累計 XP は 0 (L=0) または 5*L^2 + 50*L + 100。
"""
import hashlib
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from app import settings
from app.db import db
from app.ui import COLOR_GOLD, COLOR_PRIMARY, progress_bar, reply_error

PAGE_SIZE = 10
# 重複判定で保持する「XP がカウントされた発言」のハッシュ履歴件数
RECENT_CONTENT_HASH_LIMIT = 5


def xp_threshold(level: int) -> int:
    """レベル level に到達するのに必要な累計 XP。"""
    if level <= 0:
        return 0
    return 5 * (level ** 2) + 50 * level + 100


def calc_level(total_xp: int) -> int:
    level = 0
    while total_xp >= xp_threshold(level + 1):
        level += 1
    return level


def level_progress(total_xp: int):
    """(現在レベル, 次レベルまでの進捗率 0.0-1.0, 次レベルまでの残り XP)。"""
    level = calc_level(total_xp)
    current = xp_threshold(level)
    nxt = xp_threshold(level + 1)
    span = nxt - current
    fraction = (total_xp - current) / span if span > 0 else 0.0
    return level, fraction, nxt - total_xp


def normalize_content(content) -> str:
    """重複判定用に発言内容を正規化する。

    前後の空白を除き、連続する空白 (改行・タブ含む) を半角スペース1つに
    畳んで、小文字化する。
    """
    return " ".join((content or "").split()).casefold()


def content_hash(content) -> str:
    """正規化した発言内容の sha256 ハッシュ。"""
    normalized = normalize_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def xp_block_reason(
    content,
    recent_hashes,
    now: float,
    last_message_time: float,
    config: dict,
):
    """XP を付与できない理由を返す。付与できる場合は None。

    理由キー: 'cooldown' / 'no_text' / 'too_short' / 'duplicate'
    recent_hashes は「XP がカウントされた直近の発言」のハッシュ履歴
    (最大 RECENT_CONTENT_HASH_LIMIT 件)。config は
    settings.get_xp_config() の戻り値 (dict) を想定。
    """
    if now - last_message_time < config["xp_cooldown_seconds"]:
        return "cooldown"
    stripped = (content or "").strip()
    if not stripped:
        # スタンプ・添付ファイルのみ等、テキストがない発言。
        # (絵文字だけの投稿も content が空で届くためここに含まれる。
        #  テキスト中に絵文字が含まれる場合は通常の文字として数える)
        return "no_text"
    if len(stripped) < config["xp_min_length"]:
        return "too_short"
    if recent_hashes and content_hash(content) in recent_hashes:
        # 直近で XP を得た発言のどれかと同一内容。間に別の発言を挟んでも、
        # 時間を空けても、別チャンネルでも弾く (履歴はユーザー×ギルド単位)
        return "duplicate"
    return None


def next_recent_hashes(recent_hashes, new_hash: str) -> list:
    """履歴に新しいハッシュを追加し、直近 RECENT_CONTENT_HASH_LIMIT 件に保つ。"""
    updated = list(recent_hashes or [])
    updated.append(new_hash)
    return updated[-RECENT_CONTENT_HASH_LIMIT:]


def validate_xp_config(
    xp_min: int, xp_max: int, cooldown_seconds: int, min_length: int
):
    """XP 設定の検証。問題があれば日本語のエラーメッセージを返す。"""
    if not (0 <= xp_min <= xp_max <= settings.XP_HARD_MAX):
        return (
            f"XP入手量は 0 〜 {settings.XP_HARD_MAX} の範囲で、"
            "最小 ≦ 最大 になるよう指定してください。"
        )
    if not (0 <= cooldown_seconds <= settings.COOLDOWN_HARD_MAX):
        return f"クールダウンは 0 〜 {settings.COOLDOWN_HARD_MAX} 秒で指定してください。"
    if not (0 <= min_length <= settings.MIN_LENGTH_HARD_MAX):
        return f"最小文字数は 0 〜 {settings.MIN_LENGTH_HARD_MAX} で指定してください。"
    return None


class LeaderboardView(discord.ui.View):
    def __init__(self, cog: "LevelingCog", guild: discord.Guild, total: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild = guild
        self.total = total
        self.page = 0
        self.max_page = max(0, (total - 1) // PAGE_SIZE)
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.max_page
        self.page_label.label = f"{self.page + 1} / {self.max_page + 1}"

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.cog.build_leaderboard_embed(self.guild, self.page),
            view=self,
        )

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_label(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.page = min(self.max_page, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.cog.build_leaderboard_embed(self.guild, self.page),
            view=self,
        )


class LevelingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- XP 付与 ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        guild = message.guild
        user = message.author
        if not isinstance(user, discord.Member):
            return

        data = db.get_xp(user.id, guild.id, create=True)
        now = time.time()
        config = settings.get_xp_config(guild.id)
        reason = xp_block_reason(
            message.content,
            data["recent_content_hashes"],
            now,
            data["last_message_time"],
            config,
        )
        if reason is not None:
            # ブロック時は last_message_time / ハッシュ履歴を更新しない
            # (クールダウンが延びたり、重複ブロックが自己継続したりしない)
            return

        new_xp = data["xp"] + random.randint(config["xp_min"], config["xp_max"])
        old_level = data["level"]
        new_level = calc_level(new_xp)
        db.upsert_xp(
            user.id,
            guild.id,
            new_xp,
            new_level,
            now,
            recent_content_hashes=next_recent_hashes(
                data["recent_content_hashes"],
                content_hash(message.content),
            ),
        )

        if new_level > old_level:
            await self._on_level_up(message, user, new_level)

    async def _on_level_up(
        self, message: discord.Message, member: discord.Member, new_level: int
    ):
        guild = message.guild
        level_roles = settings.get_level_roles(guild.id)
        for level_str, role_id_str in level_roles.items():
            try:
                role_level = int(level_str)
                role_id = int(role_id_str)
            except (TypeError, ValueError):
                continue
            if new_level < role_level:
                continue
            role = guild.get_role(role_id)
            if role is None or role in member.roles:
                continue
            try:
                await member.add_roles(
                    role, reason=f"レベル {role_level} 達成による自動付与"
                )
            except discord.Forbidden:
                print(f"⚠️ ロール {role.name} を付与する権限がありません。")
            except discord.HTTPException as e:
                print(f"⚠️ レベルロール付与エラー: {e}")

        embed = discord.Embed(
            title="🎉 レベルアップ！",
            description=f"{member.mention} が **レベル {new_level}** に上がりました！",
            color=COLOR_GOLD,
        )
        target = message.channel
        levelup_id = settings.get(guild.id, settings.LEVELUP_CHANNEL_ID)
        if levelup_id:
            channel = guild.get_channel(int(levelup_id))
            if isinstance(channel, discord.TextChannel):
                target = channel
        try:
            await target.send(embed=embed)
        except discord.HTTPException:
            pass

    # ---------- /rank ----------

    @app_commands.command(name="rank", description="自分のレベルとランクを表示します")
    async def rank(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        if guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        data = db.get_xp(user.id, guild.id)  # 読み取りでは行を作らない
        xp = data["xp"]
        level, fraction, remaining = level_progress(xp)
        rank = db.get_rank(user.id, guild.id)

        embed = discord.Embed(
            title=f"📊 {user.display_name} のランク",
            color=COLOR_PRIMARY,
        )
        embed.add_field(name="ランキング", value=f"#{rank}", inline=True)
        embed.add_field(name="レベル", value=f"Lv.{level}", inline=True)
        embed.add_field(name="総XP", value=f"{xp} XP", inline=True)
        embed.add_field(
            name=f"次のレベルまで（あと {remaining} XP）",
            value=f"{progress_bar(fraction)} {int(fraction * 100)}%",
            inline=False,
        )
        cooldown = settings.get_xp_config(guild.id)["xp_cooldown_seconds"]
        embed.set_footer(
            text=f"スパム防止のため、XPは{cooldown}秒に1回だけ加算されます。"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- /leaderboard ----------

    def build_leaderboard_embed(
        self, guild: discord.Guild, page: int
    ) -> discord.Embed:
        top = db.get_top_users(guild.id, PAGE_SIZE, page * PAGE_SIZE)
        embed = discord.Embed(
            title=f"🏆 {guild.name} レベルランキング",
            color=COLOR_GOLD,
        )
        lines = []
        for i, (user_id, xp, level) in enumerate(top, start=page * PAGE_SIZE + 1):
            member = guild.get_member(user_id)
            name = member.display_name if member else f"不明なユーザー (ID: {user_id})"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} **{name}** - Lv.{level} ({xp} XP)")
        embed.description = "\n".join(lines) if lines else "まだデータがありません。"
        return embed

    @app_commands.command(
        name="leaderboard", description="サーバーのXPランキングを表示します"
    )
    async def leaderboard(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        total = db.count_users(guild.id)
        if total == 0:
            await interaction.response.send_message(
                "まだデータがありません。", ephemeral=True
            )
            return
        view = LeaderboardView(self, guild, total)
        await interaction.response.send_message(
            embed=self.build_leaderboard_embed(guild, 0),
            view=view,
        )

    # ---------- /level グループ (管理者) ----------

    level_group = app_commands.Group(
        name="level",
        description="レベリング管理（管理者専用）",
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

    @level_group.command(name="set-role", description="レベル到達時に付与するロールを設定します")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(level="対象レベル（1以上）", role="付与するロール")
    async def level_set_role(
        self, interaction: discord.Interaction, level: int, role: discord.Role
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        if level < 1:
            await reply_error(interaction, "レベルは1以上を指定してください。")
            return
        settings.set_level_role(interaction.guild.id, level, role.id)
        await interaction.response.send_message(
            f"✅ レベル **{level}** 到達時に {role.mention} を付与するよう設定しました。",
            ephemeral=True,
        )

    @level_group.command(name="remove-role", description="レベルロールの設定を解除します")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(level="解除するレベル")
    async def level_remove_role(
        self, interaction: discord.Interaction, level: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        if settings.remove_level_role(interaction.guild.id, level):
            await interaction.response.send_message(
                f"✅ レベル **{level}** のロール設定を解除しました。",
                ephemeral=True,
            )
        else:
            await reply_error(interaction, f"レベル **{level}** の設定はありません。")

    @level_group.command(name="give-xp", description="メンバーのXPを増減します")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="対象メンバー", amount="増減量（マイナス可）")
    async def level_give_xp(
        self, interaction: discord.Interaction, member: discord.Member, amount: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        data = db.get_xp(member.id, guild.id, create=True)
        new_xp = max(0, data["xp"] + amount)
        new_level = calc_level(new_xp)
        db.upsert_xp(
            member.id,
            guild.id,
            new_xp,
            new_level,
            data["last_message_time"],
            recent_content_hashes=data["recent_content_hashes"],
        )
        await interaction.response.send_message(
            f"✅ {member.mention} のXPを {data['xp']} → {new_xp} に変更しました"
            f"（Lv.{data['level']} → Lv.{new_level}）。",
            ephemeral=True,
        )

    @level_group.command(
        name="config",
        description="XP入手量・クールダウン・最小文字数を変更します（指定した項目だけ更新）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        xp_min="1回の発言で得るXPの最小値（0〜10000）",
        xp_max="1回の発言で得るXPの最大値（0〜10000）",
        cooldown_seconds="XPが貯まる間隔（秒、0〜3600）",
        min_length="XP対象になる最小の文字数（0〜200）",
    )
    async def level_config(
        self,
        interaction: discord.Interaction,
        xp_min: int = None,
        xp_max: int = None,
        cooldown_seconds: int = None,
        min_length: int = None,
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        config = settings.get_xp_config(guild.id)

        new_min = xp_min if xp_min is not None else config["xp_min"]
        new_max = xp_max if xp_max is not None else config["xp_max"]
        new_cooldown = (
            cooldown_seconds
            if cooldown_seconds is not None
            else config["xp_cooldown_seconds"]
        )
        new_min_length = (
            min_length if min_length is not None else config["xp_min_length"]
        )

        error = validate_xp_config(
            new_min, new_max, new_cooldown, new_min_length
        )
        if error is not None:
            await reply_error(interaction, error)
            return

        if xp_min is not None:
            settings.set_value(guild.id, settings.XP_MIN, xp_min)
        if xp_max is not None:
            settings.set_value(guild.id, settings.XP_MAX, xp_max)
        if cooldown_seconds is not None:
            settings.set_value(
                guild.id, settings.XP_COOLDOWN_SECONDS, cooldown_seconds
            )
        if min_length is not None:
            settings.set_value(guild.id, settings.XP_MIN_LENGTH, min_length)

        effective = settings.get_xp_config(guild.id)
        await interaction.response.send_message(
            "✅ レベリング設定を更新しました。\n"
            f"・XP入手量: {effective['xp_min']} 〜 {effective['xp_max']}\n"
            f"・クールダウン: {effective['xp_cooldown_seconds']} 秒\n"
            f"・最小文字数: {effective['xp_min_length']} 文字",
            ephemeral=True,
        )

    @level_group.command(name="reset", description="メンバーのXPとレベルをリセットします")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="対象メンバー")
    async def level_reset(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        db.delete_xp(member.id, interaction.guild.id)
        await interaction.response.send_message(
            f"✅ {member.mention} のXPとレベルをリセットしました。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelingCog(bot))
