"""/setup パネルと /settings (管理者向け)。

/setup は引数なしで、セレクトメニューとボタンだけで初期設定が完結する。
"""
import discord
from discord import app_commands
from discord.ext import commands

from app import settings
from app.ui import COLOR_PRIMARY, COLOR_WARN, reply_error


def _mark(ok: bool) -> str:
    return "✅" if ok else "⚠️"


def build_setup_embed(guild: discord.Guild) -> discord.Embed:
    gid = guild.id

    verified_id = settings.get(gid, settings.VERIFIED_ROLE_ID)
    support_id = settings.get(gid, settings.SUPPORT_ROLE_ID)
    category_id = settings.get(gid, settings.TICKET_CATEGORY_ID)
    log_id = settings.get(gid, settings.LOG_CHANNEL_ID)
    levelup_id = settings.get(gid, settings.LEVELUP_CHANNEL_ID)
    auto_hours = settings.get_auto_close_hours(gid)

    def role_text(role_id):
        role = guild.get_role(int(role_id)) if role_id else None
        return role.mention if role else "未設定"

    def channel_text(channel_id):
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        return channel.mention if channel else "未設定"

    lines = [
        f"{_mark(bool(verified_id))} **認証ロール**: {role_text(verified_id)}",
        f"{_mark(bool(support_id))} **サポートロール**: {role_text(support_id)}",
        f"{_mark(bool(category_id))} **チケットカテゴリ**: {channel_text(category_id)}",
        f"{_mark(bool(log_id))} **ログチャンネル**: {channel_text(log_id)}",
        f"{_mark(bool(levelup_id))} **レベル通知チャンネル**: {channel_text(levelup_id)}",
        f"✅ **自動クローズ**: "
        + (f"{auto_hours} 時間" if auto_hours > 0 else "無効"),
    ]

    embed = discord.Embed(
        title="⚙️ AquaMine Bot セットアップ",
        description=(
            "下のメニューから選ぶだけで設定できます。ID の入力は不要です。\n\n"
            + "\n".join(lines)
        ),
        color=COLOR_PRIMARY,
    )
    embed.set_footer(text="このパネルは15分で操作できなくなります")
    return embed


class AutoCloseModal(discord.ui.Modal, title="自動クローズ設定"):
    hours = discord.ui.TextInput(
        label="無発言が続いたら閉じるまでの時間（時間）",
        placeholder="48（0で無効、最大720）",
        required=True,
        max_length=3,
    )

    def __init__(self, view: "SetupView"):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.hours.value or "").strip()
        try:
            value = int(raw)
        except ValueError:
            await reply_error(interaction, "数字で入力してください。")
            return
        if not 0 <= value <= settings.MAX_AUTO_CLOSE_HOURS:
            await reply_error(
                interaction,
                f"0〜{settings.MAX_AUTO_CLOSE_HOURS} の範囲で入力してください。",
            )
            return
        settings.set_value(
            interaction.guild_id, settings.AUTO_CLOSE_HOURS, value
        )
        await interaction.response.send_message(
            "✅ 自動クローズを "
            + (f"{value} 時間に設定しました。" if value > 0 else "無効にしました。"),
            ephemeral=True,
        )
        await self.view_ref.refresh_panel()


class SetupView(discord.ui.View):
    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=900)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.panel_message: discord.Message | None = None

    async def refresh_panel(self):
        if self.panel_message is None or self.panel_message.guild is None:
            return
        try:
            await self.panel_message.edit(
                embed=build_setup_embed(self.panel_message.guild),
                view=self,
            )
        except discord.HTTPException:
            pass

    async def _save_and_refresh(
        self, interaction: discord.Interaction, key: str, value, label: str
    ):
        settings.set_value(self.guild_id, key, value)
        await interaction.response.edit_message(
            embed=build_setup_embed(interaction.guild),
            view=self,
        )

    # --- selects ---

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="認証ロールを選ぶ（ルール同意で付与）",
        custom_id="v2:setup:verified_role",
        row=0,
    )
    async def select_verified_role(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect
    ):
        role = select.values[0]
        await self._save_and_refresh(
            interaction, settings.VERIFIED_ROLE_ID, role.id, "認証ロール"
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="サポートロールを選ぶ（チケット対応）",
        custom_id="v2:setup:support_role",
        row=1,
    )
    async def select_support_role(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect
    ):
        role = select.values[0]
        await self._save_and_refresh(
            interaction, settings.SUPPORT_ROLE_ID, role.id, "サポートロール"
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="チケットを作成するカテゴリを選ぶ",
        custom_id="v2:setup:category",
        channel_types=[discord.ChannelType.category],
        row=2,
    )
    async def select_category(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ):
        channel = select.values[0]
        await self._save_and_refresh(
            interaction, settings.TICKET_CATEGORY_ID, channel.id, "チケットカテゴリ"
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="ログチャンネルを選ぶ（認証ログ・チケット記録）",
        custom_id="v2:setup:log_channel",
        channel_types=[discord.ChannelType.text],
        row=3,
    )
    async def select_log_channel(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ):
        channel = select.values[0]
        await self._save_and_refresh(
            interaction, settings.LOG_CHANNEL_ID, channel.id, "ログチャンネル"
        )

    # --- buttons ---

    @discord.ui.button(
        label="認証パネルを設置", style=discord.ButtonStyle.success, row=4
    )
    async def post_verify_panel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not settings.get(self.guild_id, settings.VERIFIED_ROLE_ID):
            await reply_error(
                interaction,
                "先に認証ロールを選んでください（パネルの一番上のメニュー）。",
            )
            return
        from .verify import build_rules_panel

        embed, view = build_rules_panel()
        channel = interaction.guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await reply_error(interaction, "設置先のチャンネルが見つかりません。")
            return
        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            await reply_error(interaction, f"パネルの設置に失敗しました。\n`{e}`")
            return
        await interaction.response.send_message(
            "✅ 認証パネルをこのチャンネルに設置しました。", ephemeral=True
        )

    @discord.ui.button(
        label="チケットパネルを設置", style=discord.ButtonStyle.primary, row=4
    )
    async def post_ticket_panel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        missing = []
        if not settings.get(self.guild_id, settings.TICKET_CATEGORY_ID):
            missing.append("チケットカテゴリ")
        if not settings.get(self.guild_id, settings.SUPPORT_ROLE_ID):
            missing.append("サポートロール")
        if missing:
            await reply_error(
                interaction,
                "先に次の設定を済ませてください: " + "、".join(missing),
            )
            return
        from .tickets import build_ticket_panel

        embed, view = build_ticket_panel()
        channel = interaction.guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await reply_error(interaction, "設置先のチャンネルが見つかりません。")
            return
        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            await reply_error(interaction, f"パネルの設置に失敗しました。\n`{e}`")
            return
        await interaction.response.send_message(
            "✅ チケットパネルをこのチャンネルに設置しました。", ephemeral=True
        )

    @discord.ui.button(
        label="レベル通知chにここを設定", style=discord.ButtonStyle.secondary, row=4
    )
    async def set_levelup_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        settings.set_value(
            self.guild_id, settings.LEVELUP_CHANNEL_ID, self.channel_id
        )
        await interaction.response.edit_message(
            embed=build_setup_embed(interaction.guild),
            view=self,
        )

    @discord.ui.button(
        label="自動クローズ設定", style=discord.ButtonStyle.secondary, row=4
    )
    async def set_auto_close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(AutoCloseModal(self))


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup", description="設定パネルを開きます（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_panel(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.channel is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        view = SetupView(interaction.guild.id, interaction.channel.id)
        await interaction.response.send_message(
            embed=build_setup_embed(interaction.guild),
            view=view,
            ephemeral=True,
        )
        view.panel_message = await interaction.original_response()

    @app_commands.command(
        name="settings", description="現在の設定を表示します（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def show_settings(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        gid = guild.id

        def role_text(key):
            role_id = settings.get(gid, key)
            role = guild.get_role(int(role_id)) if role_id else None
            return role.mention if role else "未設定"

        def channel_text(key):
            channel_id = settings.get(gid, key)
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            return channel.mention if channel else "未設定"

        auto_hours = settings.get_auto_close_hours(gid)

        embed = discord.Embed(title="⚙️ 現在の設定", color=COLOR_PRIMARY)
        embed.add_field(name="認証ロール", value=role_text(settings.VERIFIED_ROLE_ID))
        embed.add_field(name="サポートロール", value=role_text(settings.SUPPORT_ROLE_ID))
        embed.add_field(
            name="チケットカテゴリ", value=channel_text(settings.TICKET_CATEGORY_ID)
        )
        embed.add_field(name="ログチャンネル", value=channel_text(settings.LOG_CHANNEL_ID))
        embed.add_field(
            name="レベル通知チャンネル",
            value=channel_text(settings.LEVELUP_CHANNEL_ID),
        )
        embed.add_field(
            name="自動クローズ",
            value=f"{auto_hours} 時間" if auto_hours > 0 else "無効",
        )

        level_roles = settings.get_level_roles(gid)
        if level_roles:
            lines = []
            for level_str in sorted(level_roles, key=lambda s: int(s)):
                role = guild.get_role(int(level_roles[level_str]))
                lines.append(
                    f"Lv.{level_str}: {role.mention if role else '(削除済みロール)'}"
                )
            embed.add_field(
                name="レベルロール", value="\n".join(lines), inline=False
            )
        else:
            embed.add_field(name="レベルロール", value="未設定", inline=False)

        embed.color = COLOR_PRIMARY if level_roles else COLOR_WARN
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
