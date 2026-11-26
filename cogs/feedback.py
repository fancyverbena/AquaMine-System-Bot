"""提案箱とバグ報告 (フィードバック)。

メンバーは /suggest (提案) と /bugreport (バグ報告) からモーダルで投稿
でき、投稿はそれぞれ設定されたチャンネルに Embed で転送される。
提案には 👍/👎 の投票ボタンが付き、管理者はステータスボタンで
提案 (採用/見送り) とバグ (対応中/完了/見送り) を管理できる。

永続化の方式:
  投稿と投票はすべて DB (feedback / feedback_votes) に保存する。
  ボタンはロールパネル・抽選と同じ DynamicItem 方式で、custom_id は
  `v2:fbvote:<id>:<up|down>` (投票) と `v2:fbstatus:<id>:<status>` (状態変更)。
  bot.py の setup_hook で add_dynamic_items に登録するので、Bot 再起動後も
  既存の投稿のボタンがそのまま動く。クリック時は DB を引き直して
  現在のステータスで判定するため、終了済みの投稿への投票や状態変更は
  その場で弾く。
"""
import datetime
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from app import settings
from app.db import db
from app.ui import COLOR_SUCCESS, COLOR_WARN, base_embed, reply_error

KIND_SUGGESTION = "suggestion"
KIND_BUG = "bug"

SUGGESTION_STATUSES = {
    "pending": "検討中",
    "adopted": "採用",
    "rejected": "見送り",
}
BUG_STATUSES = {
    "open": "未対応",
    "working": "対応中",
    "fixed": "完了",
    "rejected": "見送り",
}
STATUS_LABELS = {**SUGGESTION_STATUSES, **BUG_STATUSES}
TERMINAL_STATUSES = {"adopted", "fixed", "rejected"}

# 許可する状態遷移 (現在 → 遷移先の集合)。終端状態はキー自体を持たない。
TRANSITIONS = {
    KIND_SUGGESTION: {
        "pending": {"adopted", "rejected"},
    },
    KIND_BUG: {
        "open": {"working", "fixed", "rejected"},
        "working": {"fixed", "rejected"},
    },
}

# ステータス変更ボタンの並び (kind ごと)。(status, ラベル, スタイル)
STATUS_BUTTONS = {
    KIND_SUGGESTION: [
        ("adopted", "採用", discord.ButtonStyle.success),
        ("rejected", "見送り", discord.ButtonStyle.secondary),
    ],
    KIND_BUG: [
        ("working", "対応中", discord.ButtonStyle.primary),
        ("fixed", "完了", discord.ButtonStyle.success),
        ("rejected", "見送り", discord.ButtonStyle.secondary),
    ],
}

VOTE_TEMPLATE = r"v2:fbvote:(?P<feedback_id>[0-9]+):(?P<side>up|down)"
STATUS_TEMPLATE = r"v2:fbstatus:(?P<feedback_id>[0-9]+):(?P<status>[a-z]+)"

SUGGESTION_CONTENT_MAX = 1000
BUG_TITLE_MAX = 100
BUG_CONTENT_MAX = 1500


def can_transition(kind: str, current: str, target: str) -> bool:
    """ステータス遷移が許可されているかを返す純粋関数。

    終端状態 (採用・完了・見送り) からはどこへも遷移できない。
    kind に存在しないステータスへの遷移も不可。
    """
    return target in TRANSITIONS.get(kind, {}).get(current, set())


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def status_color(status: str) -> discord.Color:
    """ステータスに対応する Embed の色。"""
    if status in ("pending", "open"):
        return COLOR_WARN
    if status == "working":
        return discord.Color.blue()
    if status in ("adopted", "fixed"):
        return COLOR_SUCCESS
    if status == "rejected":
        return discord.Color.dark_grey()
    return COLOR_WARN


def build_feedback_embed(row) -> discord.Embed:
    """投稿 Embed。row は db.get_feedback の戻り値。"""
    if row["kind"] == KIND_SUGGESTION:
        title = f"💡 提案 #{row['id']}"
        description = row["content"]
    else:
        title = f"🐛 バグ報告 #{row['id']}: {row['title']}"
        description = row["content"]
    embed = base_embed(
        title, description, color=status_color(row["status"])
    )
    embed.add_field(name="投稿者", value=f"<@{row['author_id']}>", inline=True)
    embed.add_field(
        name="ステータス", value=status_label(row["status"]), inline=True
    )
    embed.set_footer(text=f"ステータス: {status_label(row['status'])}")
    embed.timestamp = datetime.datetime.fromtimestamp(
        row["created_at"], tz=datetime.timezone.utc
    )
    return embed


def build_feedback_view(row, up_count: int = 0, down_count: int = 0):
    """投稿に付けるボタン一式 (投票 + ステータス変更)。

    終端状態ではすべて無効化する (提案は投票も締め切る)。
    現在のステータスと同じボタンも押せないよう無効化する。
    """
    terminal = row["status"] in TERMINAL_STATUSES
    view = discord.ui.View(timeout=None)
    if row["kind"] == KIND_SUGGESTION:
        view.add_item(
            FeedbackVoteButton(
                row["id"], "up", up_count, disabled=terminal
            )
        )
        view.add_item(
            FeedbackVoteButton(
                row["id"], "down", down_count, disabled=terminal
            )
        )
    for status_key, label, style in STATUS_BUTTONS[row["kind"]]:
        view.add_item(
            FeedbackStatusButton(
                row["id"],
                status_key,
                label,
                style,
                disabled=terminal or status_key == row["status"],
            )
        )
    return view


async def handle_feedback_vote(
    interaction: discord.Interaction, feedback_id: int, side: str
):
    """投票ボタンのクリック処理 (DynamicItem の callback から呼ばれる)。"""
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
        return
    if member.bot:
        return

    row = db.get_feedback(feedback_id)
    if row is None or row["guild_id"] != guild.id:
        await reply_error(interaction, "対象の提案が見つかりません。")
        return
    if row["kind"] != KIND_SUGGESTION:
        await reply_error(interaction, "この投稿には投票できません。")
        return
    if row["status"] in TERMINAL_STATUSES:
        await reply_error(
            interaction,
            f"この提案は「{status_label(row['status'])}」で終了したため、投票は締め切られています。",
        )
        return

    vote = 1 if side == "up" else -1
    up_count, down_count = db.set_vote(feedback_id, member.id, vote)

    try:
        await interaction.response.edit_message(
            embed=build_feedback_embed(row),
            view=build_feedback_view(row, up_count, down_count),
        )
    except discord.HTTPException:
        # 投票自体は DB に記録済み。表示の更新失敗は無視する
        pass


async def handle_feedback_status(
    interaction: discord.Interaction, feedback_id: int, target_status: str
):
    """ステータスボタンのクリック処理 (管理者のみ)。"""
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
        return
    if member.bot:
        return
    if not member.guild_permissions.administrator:
        await reply_error(interaction, "ステータスの変更は管理者専用です。")
        return

    row = db.get_feedback(feedback_id)
    if row is None or row["guild_id"] != guild.id:
        await reply_error(interaction, "対象の投稿が見つかりません。")
        return
    if not can_transition(row["kind"], row["status"], target_status):
        await reply_error(
            interaction,
            f"現在のステータス「{status_label(row['status'])}」から"
            f"「{status_label(target_status)}」には変更できません。",
        )
        return

    db.set_feedback_status(feedback_id, target_status)
    row = db.get_feedback(feedback_id)
    up_count, down_count = db.count_votes(feedback_id)
    try:
        await interaction.response.edit_message(
            embed=build_feedback_embed(row),
            view=build_feedback_view(row, up_count, down_count),
        )
    except discord.HTTPException:
        # ステータス自体は DB に記録済み。表示の更新失敗は無視する
        pass
    try:
        await interaction.followup.send(
            f"✅ #{feedback_id} のステータスを"
            f"「{status_label(target_status)}」に変更しました。",
            ephemeral=True,
        )
    except discord.HTTPException:
        pass


class FeedbackVoteButton(
    discord.ui.DynamicItem[discord.ui.Button], template=VOTE_TEMPLATE
):
    """提案 ID と投票方向を custom_id に埋め込む動的ボタン。"""

    def __init__(
        self,
        feedback_id: int,
        side: str,
        count: int = 0,
        disabled: bool = False,
    ):
        emoji = "👍" if side == "up" else "👎"
        super().__init__(
            discord.ui.Button(
                label=f"{emoji} {count}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"v2:fbvote:{feedback_id}:{side}",
                disabled=disabled,
            )
        )
        self.feedback_id = feedback_id
        self.side = side

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match,
        /,
    ):
        return cls(int(match["feedback_id"]), match["side"])

    async def callback(self, interaction: discord.Interaction):
        await handle_feedback_vote(interaction, self.feedback_id, self.side)


class FeedbackStatusButton(
    discord.ui.DynamicItem[discord.ui.Button], template=STATUS_TEMPLATE
):
    """提案/バグ ID と遷移先ステータスを custom_id に埋め込む動的ボタン。"""

    def __init__(
        self,
        feedback_id: int,
        status_key: str,
        label: str = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ):
        super().__init__(
            discord.ui.Button(
                label=label or status_label(status_key),
                style=style,
                custom_id=f"v2:fbstatus:{feedback_id}:{status_key}",
                disabled=disabled,
            )
        )
        self.feedback_id = feedback_id
        self.status_key = status_key

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match,
        /,
    ):
        label = getattr(item, "label", None)
        return cls(int(match["feedback_id"]), match["status"], label=label)

    async def callback(self, interaction: discord.Interaction):
        await handle_feedback_status(
            interaction, self.feedback_id, self.status_key
        )


class SuggestModal(discord.ui.Modal, title="提案を送る"):
    content = discord.ui.TextInput(
        label="提案内容",
        placeholder="サーバーへの提案を書いてください",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=SUGGESTION_CONTENT_MAX,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FeedbackCog")
        if cog is None:
            await reply_error(interaction, "システムエラーが発生しました。")
            return
        await cog.submit_feedback(
            interaction,
            KIND_SUGGESTION,
            "",
            str(self.content.value).strip(),
        )


class BugReportModal(discord.ui.Modal, title="バグ報告を送る"):
    title_input = discord.ui.TextInput(
        label="タイトル",
        placeholder="例: ログインできません",
        required=True,
        max_length=BUG_TITLE_MAX,
    )
    content = discord.ui.TextInput(
        label="内容",
        placeholder="再現手順・期待した動作・実際の動作を書いてください",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=BUG_CONTENT_MAX,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FeedbackCog")
        if cog is None:
            await reply_error(interaction, "システムエラーが発生しました。")
            return
        await cog.submit_feedback(
            interaction,
            KIND_BUG,
            str(self.title_input.value).strip(),
            str(self.content.value).strip(),
        )


class FeedbackCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- 投稿 (モーダル送信先) ----------

    @staticmethod
    def _channel_id_for(guild_id: int, kind: str) -> int:
        if kind == KIND_SUGGESTION:
            return settings.get_suggestion_channel_id(guild_id)
        return settings.get_bug_channel_id(guild_id)

    async def submit_feedback(
        self,
        interaction: discord.Interaction,
        kind: str,
        title: str,
        content: str,
    ):
        """モーダルの内容を DB に保存し、設定チャンネルへ投稿する。"""
        guild = interaction.guild
        if guild is None:
            await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
            return
        kind_label = "提案" if kind == KIND_SUGGESTION else "バグ報告"
        channel_id = self._channel_id_for(guild.id, kind)
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await reply_error(
                interaction,
                f"{kind_label}の投稿先チャンネルが未設定です。"
                "管理者に /feedback での設定を依頼してください。",
            )
            return

        feedback_id = db.create_feedback(
            guild.id,
            kind,
            interaction.user.id,
            title,
            content,
            channel.id,
            time.time(),
        )
        row = db.get_feedback(feedback_id)
        try:
            message = await channel.send(
                embed=build_feedback_embed(row),
                view=build_feedback_view(row, 0, 0),
            )
        except (discord.Forbidden, discord.HTTPException):
            await reply_error(
                interaction,
                f"{channel.mention} への投稿に失敗しました。"
                f"（{kind_label}自体は #{feedback_id} として記録されています。"
                "Bot の権限を管理者に確認してもらってください）",
            )
            return
        db.set_feedback_message(feedback_id, message.id)
        await interaction.response.send_message(
            f"✅ {kind_label} #{feedback_id} を {channel.mention} に投稿しました。\n"
            f"{message.jump_url}",
            ephemeral=True,
        )

    async def _open_modal(
        self, interaction: discord.Interaction, kind: str, modal
    ):
        if interaction.guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        kind_label = "提案" if kind == KIND_SUGGESTION else "バグ報告"
        if not self._channel_id_for(interaction.guild.id, kind):
            await reply_error(
                interaction,
                f"{kind_label}の投稿先チャンネルが未設定です。"
                "管理者に /feedback での設定を依頼してください。",
            )
            return
        await interaction.response.send_modal(modal)

    # ---------- /suggest /bugreport (全員) ----------

    @app_commands.command(name="suggest", description="サーバーへの提案を送ります")
    async def suggest(self, interaction: discord.Interaction):
        await self._open_modal(interaction, KIND_SUGGESTION, SuggestModal())

    @app_commands.command(name="bugreport", description="バグ報告を送ります")
    async def bugreport(self, interaction: discord.Interaction):
        await self._open_modal(interaction, KIND_BUG, BugReportModal())

    # ---------- /feedback グループ (管理者) ----------

    feedback_group = app_commands.Group(
        name="feedback",
        description="提案箱・バグ報告の管理（管理者専用）",
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

    @feedback_group.command(
        name="suggest-channel", description="提案の投稿先チャンネルを設定します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="提案を投稿するチャンネル")
    async def feedback_suggest_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.SUGGESTION_CHANNEL_ID, channel.id
        )
        await interaction.response.send_message(
            f"✅ 提案の投稿先を {channel.mention} に設定しました。",
            ephemeral=True,
        )

    @feedback_group.command(
        name="bug-channel", description="バグ報告の投稿先チャンネルを設定します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="バグ報告を投稿するチャンネル")
    async def feedback_bug_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.BUG_CHANNEL_ID, channel.id
        )
        await interaction.response.send_message(
            f"✅ バグ報告の投稿先を {channel.mention} に設定しました。",
            ephemeral=True,
        )

    @feedback_group.command(
        name="list", description="最近の提案・バグ報告とステータスを表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(kind="表示する種類（省略時は両方）")
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="提案のみ", value=KIND_SUGGESTION),
            app_commands.Choice(name="バグ報告のみ", value=KIND_BUG),
        ]
    )
    async def feedback_list(
        self, interaction: discord.Interaction, kind: str = None
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        rows = db.list_feedback(guild.id, kind=kind, limit=10)
        if not rows:
            await interaction.response.send_message(
                "まだ投稿がありません。", ephemeral=True
            )
            return
        lines = ["最近のフィードバック（新しい順）:"]
        for row in rows:
            date = datetime.datetime.fromtimestamp(
                row["created_at"]
            ).strftime("%Y-%m-%d")
            if row["kind"] == KIND_SUGGESTION:
                icon = "💡"
                summary = row["content"].replace("\n", " ")[:40]
                up_count, down_count = db.count_votes(row["id"])
                votes = f" 👍{up_count} 👎{down_count}"
            else:
                icon = "🐛"
                summary = row["title"][:40]
                votes = ""
            lines.append(
                f"#{row['id']} {icon} [{status_label(row['status'])}]"
                f" {summary} — <@{row['author_id']}>（{date}）{votes}"
            )
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(FeedbackCog(bot))
