"""レベリング機能 (v2)。

XP は発言ごと (30秒クールダウン、DB の last_message_time で判定)。
レベル L に必要な累計 XP は 0 (L=0) または 5*L^2 + 50*L + 100。
"""
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from app import settings
from app.db import db
from app.ui import COLOR_GOLD, COLOR_PRIMARY, progress_bar, reply_error

COOLDOWN_SECONDS = 30
XP_MIN = 10
XP_MAX = 25
PAGE_SIZE = 10


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
        if now - data["last_message_time"] < COOLDOWN_SECONDS:
            return

        new_xp = data["xp"] + random.randint(XP_MIN, XP_MAX)
        old_level = data["level"]
        new_level = calc_level(new_xp)
        db.upsert_xp(user.id, guild.id, new_xp, new_level, now)

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
        embed.set_footer(text="スパム防止のため、XPは30秒に1回だけ加算されます。")
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
            member.id, guild.id, new_xp, new_level, data["last_message_time"]
        )
        await interaction.response.send_message(
            f"✅ {member.mention} のXPを {data['xp']} → {new_xp} に変更しました"
            f"（Lv.{data['level']} → Lv.{new_level}）。",
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
