"""チケット機能 (v2)。

作成: パネルの種類セレクト -> 件名/詳細モーダル -> 専用チャンネル + DB 行。
対応: 担当する/閉じるボタン、/ticket assign|add|remove。
自動クローズ: 無発言が設定時間を超えたチケットを記録付きで閉鎖する。
評価: 閉鎖時に作成者へ DM で ⭐1〜5 の満足度を聞く (任意・1回だけ)。
      ボタンは DynamicItem 方式 (custom_id は
      `v2:ticketrate:<チケットID>:<星の数>`、チケットID = チャンネルID)
      で、評価できるのは作成者本人のみ。誰が評価できるかは閉鎖時に
      ticket_ratings へ「評価待ち (rating=0)」として記録しておく
      (tickets 行自体は閉鎖時に消えるため)。
担当通知: 種類ごとに通知ロールを設定すると (/ticket notify)、その種類の
      チケットが作られたときにチャンネル内でロールをメンションする。
"""
import os
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app import checks, settings
from app.config import TRANSCRIPT_DIR
from app.db import db
from app.ui import (
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    reply_error,
    sanitize_channel_part,
)

TICKET_TYPES = {
    "question": {
        "label": "質問・相談",
        "emoji": "❓",
        "description": "サーバーに関する質問や相談",
    },
    "report": {
        "label": "通報",
        "emoji": "🚨",
        "description": "違反行為やトラブルの報告",
    },
    "whitelist": {
        "label": "ホワイトリスト申請",
        "emoji": "📝",
        "description": "マイクラサーバーへの参加申請",
    },
    "other": {
        "label": "その他",
        "emoji": "💬",
        "description": "その他のお問い合わせ",
    },
}

ACTIVITY_WRITE_INTERVAL = 60  # 秒: last_activity_at の DB 書き込み間引き


def type_label(ticket_type: str) -> str:
    info = TICKET_TYPES.get(ticket_type, TICKET_TYPES["other"])
    return f"{info['emoji']} {info['label']}"


def valid_ticket_type(ticket_type: str) -> bool:
    """定義済みのチケット種類かどうか。"""
    return ticket_type in TICKET_TYPES


# ---------- 閉鎖後の満足度評価 ----------

RATING_TEMPLATE = r"v2:ticketrate:(?P<ticket_id>[0-9]+):(?P<stars>[0-9]+)"


def rating_block_reason(rating_row, clicker_id: int, stars: int):
    """評価ボタンを受け付けられない理由を返す純粋関数 (可能なら None)。

    rating_row は db.get_ticket_rating の戻り値 (評価待ちは rating=0)。
    作成者本人・未評価・⭐1〜5 のすべてを満たすときだけ None。
    """
    if rating_row is None:
        return "この評価は無効になっています（対象のチケットが見つかりません）。"
    if rating_row["user_id"] != clicker_id:
        return "評価できるのはチケットの作成者だけです。"
    if rating_row["rating"] != 0:
        return "このチケットはすでに評価済みです。"
    if not (1 <= stars <= 5):
        return "評価は ⭐1〜⭐5 で選んでください。"
    return None


def stars_text(stars: int) -> str:
    return "⭐" * stars


def build_rating_view(ticket_id: int, selected: int = None):
    """評価ボタン一式。selected を渡すと全ボタンを無効化し選択肢を強調する。"""
    view = discord.ui.View(timeout=None)
    for stars in range(1, 6):
        view.add_item(
            TicketRatingButton(
                ticket_id,
                stars,
                disabled=selected is not None,
                selected=selected == stars,
            )
        )
    return view


class TicketRatingButton(
    discord.ui.DynamicItem[discord.ui.Button], template=RATING_TEMPLATE
):
    """チケットID と星の数を custom_id に埋め込む動的ボタン (DM 用)。"""

    def __init__(
        self,
        ticket_id: int,
        stars: int,
        disabled: bool = False,
        selected: bool = False,
    ):
        super().__init__(
            discord.ui.Button(
                label=str(stars),
                emoji="⭐",
                style=(
                    discord.ButtonStyle.success
                    if selected
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"v2:ticketrate:{ticket_id}:{stars}",
                disabled=disabled,
            )
        )
        self.ticket_id = ticket_id
        self.stars = stars

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match,
        /,
    ):
        return cls(int(match["ticket_id"]), int(match["stars"]))

    async def callback(self, interaction: discord.Interaction):
        await handle_ticket_rating(interaction, self.ticket_id, self.stars)


async def handle_ticket_rating(
    interaction: discord.Interaction, ticket_id: int, stars: int
):
    """評価ボタンのクリック処理 (DynamicItem の callback から呼ばれる)。

    クリックは作成者の DM 内で起きるため interaction.guild は None。
    ギルドとログチャンネルは評価行 (ticket_ratings) 経由で引く。
    """
    clicker = interaction.user
    if clicker.bot:
        return
    row = db.get_ticket_rating(ticket_id)
    reason = rating_block_reason(row, clicker.id, stars)
    if reason is not None:
        await reply_error(interaction, reason)
        return

    if not db.complete_ticket_rating(ticket_id, clicker.id, stars, time.time()):
        await reply_error(interaction, "このチケットはすでに評価済みです。")
        return

    try:
        await interaction.response.edit_message(
            content=(
                f"⭐ 評価ありがとうございました！\n"
                f"あなたの評価: {stars_text(stars)}（{stars}/5）"
            ),
            view=build_rating_view(ticket_id, selected=stars),
        )
    except discord.HTTPException:
        # 評価自体は DB に記録済み。表示の更新失敗は無視する
        pass

    # ログチャンネルが設定されていれば結果を知らせる
    guild = interaction.client.get_guild(row["guild_id"])
    if guild is None:
        return
    log_id = settings.get(guild.id, settings.LOG_CHANNEL_ID)
    if not log_id:
        return
    log_channel = guild.get_channel(int(log_id))
    if not isinstance(log_channel, discord.TextChannel):
        return
    try:
        await log_channel.send(
            f"⭐ チケット評価: {stars_text(stars)}（{stars}/5）"
            f"（チケットID: {ticket_id}、作成者: <@{row['user_id']}>）"
        )
    except discord.HTTPException:
        pass


def build_ticket_panel():
    embed = discord.Embed(
        title="🎫 サポートチケット",
        description=(
            "お困りのことがあれば、下のメニューから種類を選んでください。\n"
            "種類を選ぶと件名と詳細を入力する画面が開き、"
            "スタッフだけが見られる専用チャンネルが作成されます。"
        ),
        color=COLOR_PRIMARY,
    )
    return embed, TicketPanelView()


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        select = discord.ui.Select(
            custom_id="v2:ticket_type",
            placeholder="チケットの種類を選んでください",
            options=[
                discord.SelectOption(
                    label=info["label"],
                    value=key,
                    emoji=info["emoji"],
                    description=info["description"],
                )
                for key, info in TICKET_TYPES.items()
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        select = self.children[0]
        ticket_type = select.values[0]
        await interaction.response.send_modal(TicketCreateModal(ticket_type))


class TicketCreateModal(discord.ui.Modal, title="チケットを作成"):
    subject = discord.ui.TextInput(
        label="件名",
        placeholder="例: ログインできません",
        required=True,
        max_length=100,
    )
    details = discord.ui.TextInput(
        label="用件の詳細",
        placeholder="状況をできるだけ詳しく書いてください",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, ticket_type: str):
        super().__init__()
        self.ticket_type = ticket_type

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("TicketsCog")
        if cog is None:
            await reply_error(interaction, "システムエラーが発生しました。")
            return
        await cog.create_ticket(
            interaction,
            self.ticket_type,
            str(self.subject.value).strip(),
            str(self.details.value).strip(),
        )


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="担当する",
        style=discord.ButtonStyle.primary,
        custom_id="v2:claim",
        emoji="🙋",
    )
    async def claim(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        cog = interaction.client.get_cog("TicketsCog")
        if cog is None:
            await reply_error(interaction, "システムエラーが発生しました。")
            return
        await cog.handle_claim(interaction)

    @discord.ui.button(
        label="閉じる",
        style=discord.ButtonStyle.danger,
        custom_id="v2:close",
        emoji="🔒",
    )
    async def close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        cog = interaction.client.get_cog("TicketsCog")
        if cog is None:
            await reply_error(interaction, "システムエラーが発生しました。")
            return
        await cog.handle_close_request(interaction)


class ConfirmCloseView(discord.ui.View):
    def __init__(self, cog: "TicketsCog", channel: discord.TextChannel):
        super().__init__(timeout=60)
        self.cog = cog
        self.channel = channel

    @discord.ui.button(label="はい、閉じます", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer(ephemeral=True)
        await self.cog.finalize_close(
            self.channel,
            closed_by=interaction.user,
            followup=interaction,
        )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_message(
            "チケットの閉鎖をキャンセルしました。", ephemeral=True
        )
        self.stop()


class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.auto_close_loop.start()

    def cog_unload(self):
        self.auto_close_loop.cancel()

    # ---------- 作成 ----------

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        ticket_type: str,
        subject: str,
        details: str,
    ):
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
            return

        missing = []
        category_id = settings.get(guild.id, settings.TICKET_CATEGORY_ID)
        support_role_id = settings.get(guild.id, settings.SUPPORT_ROLE_ID)
        if not category_id:
            missing.append("チケットカテゴリ")
        if not support_role_id:
            missing.append("サポートロール")
        if missing:
            await reply_error(
                interaction,
                "チケット機能の初期設定が済んでいません（未設定: "
                + "、".join(missing)
                + "）。管理者に `/setup` での設定をお願いしてください。",
            )
            return

        category = guild.get_channel(int(category_id))
        if not isinstance(category, discord.CategoryChannel):
            await reply_error(
                interaction, "設定されたチケットカテゴリが見つかりません。管理者に確認してください。"
            )
            return
        support_role = guild.get_role(int(support_role_id))
        if support_role is None:
            await reply_error(
                interaction, "設定されたサポートロールが見つかりません。管理者に確認してください。"
            )
            return

        existing = db.get_open_ticket_by_owner(guild.id, user.id)
        if existing:
            existing_channel = guild.get_channel(existing["channel_id"])
            if existing_channel is not None:
                await interaction.response.send_message(
                    f"⚠️ すでに開いているチケットがあります → {existing_channel.mention}",
                    ephemeral=True,
                )
                return
            db.delete_ticket(existing["channel_id"])

        bot_member = guild.me
        if bot_member is None:
            await reply_error(interaction, "Bot情報を取得できませんでした。")
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            support_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }

        channel_name = (
            f"ticket-{ticket_type}-{sanitize_channel_part(user.name)}"
        )[:100]
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"{user} がチケットを作成しました。",
            )
        except discord.Forbidden:
            await reply_error(
                interaction, "Botにチャンネルを作成する権限がありません。管理者に確認してください。"
            )
            return
        except discord.HTTPException as e:
            await reply_error(interaction, f"チャンネル作成中にエラーが発生しました。\n`{e}`")
            return

        now = time.time()
        db.create_ticket(
            channel_id=channel.id,
            guild_id=guild.id,
            owner_id=user.id,
            ticket_type=ticket_type,
            subject=subject,
            now=now,
        )

        embed = discord.Embed(
            title=f"🎫 {type_label(ticket_type)}",
            color=COLOR_PRIMARY,
        )
        embed.add_field(name="件名", value=subject or "(なし)", inline=False)
        embed.add_field(name="詳細", value=details or "(なし)", inline=False)
        embed.add_field(name="作成者", value=user.mention, inline=True)
        embed.add_field(name="ステータス", value="未担当", inline=True)
        embed.set_footer(text="対応が終わったら「閉じる」ボタンで閉じてください。")

        try:
            message = await channel.send(
                content=f"{support_role.mention} {user.mention}",
                embed=embed,
                view=TicketControlsView(),
            )
            db.set_ticket_message_id(channel.id, message.id)
        except discord.HTTPException as e:
            await channel.delete(reason="チケットの初期メッセージ送信失敗")
            db.delete_ticket(channel.id)
            await reply_error(
                interaction, f"チケットメッセージの送信に失敗しました。\n`{e}`"
            )
            return

        # 種類ごとの担当通知ロールが設定されていれば呼び出す
        notify_role_id = settings.get_ticket_notify_role(guild.id, ticket_type)
        if notify_role_id:
            notify_role = guild.get_role(notify_role_id)
            if notify_role is not None:
                try:
                    await channel.send(
                        f"🔔 {notify_role.mention}\n"
                        f"{type_label(ticket_type)} の担当者をお呼びしています。"
                        "対応までしばらくお待ちください。"
                    )
                except discord.HTTPException:
                    pass

        await interaction.response.send_message(
            f"✅ チケットを作成しました → {channel.mention}",
            ephemeral=True,
        )

    # ---------- 担当 / 閉じる ----------

    def _get_row_or_none(self, interaction: discord.Interaction):
        if interaction.channel is None:
            return None
        return db.get_ticket(interaction.channel.id)

    async def handle_claim(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
            return
        row = self._get_row_or_none(interaction)
        if row is None:
            await reply_error(interaction, "このチャンネルはチケットではありません。")
            return
        if not checks.member_is_support(member, guild.id):
            await reply_error(
                interaction, "担当できるのはサポートロール所持者と管理者だけです。"
            )
            return
        assignee_id = row["assignee_id"]
        if assignee_id == member.id:
            await interaction.response.send_message(
                "すでにあなたが担当しています。", ephemeral=True
            )
            return
        if assignee_id:
            await interaction.response.send_message(
                f"⚠️ このチケットはすでに <@{assignee_id}> が担当しています。",
                ephemeral=True,
            )
            return

        db.set_ticket_assignee(row["channel_id"], member.id)
        await interaction.response.send_message(
            f"🙋 {member.mention} がこのチケットの担当になりました。"
        )
        await self._update_status_field(row, f"担当: {member.mention}")

    async def _update_status_field(self, row: dict, status_text: str):
        message_id = row.get("message_id")
        channel_id = row["channel_id"]
        if not message_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return
        if not message.embeds:
            return
        embed = message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == "ステータス":
                embed.set_field_at(
                    index, name="ステータス", value=status_text, inline=True
                )
                break
        else:
            embed.add_field(name="ステータス", value=status_text, inline=True)
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass

    async def handle_close_request(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        channel = interaction.channel
        if guild is None or not isinstance(member, discord.Member) or not isinstance(
            channel, discord.TextChannel
        ):
            await reply_error(interaction, "この操作はチケットチャンネルでのみ使えます。")
            return
        row = self._get_row_or_none(interaction)
        if row is None:
            await reply_error(interaction, "このチャンネルはチケットではありません。")
            return
        is_owner = member.id == row["owner_id"]
        if not (is_owner or checks.member_is_support(member, guild.id)):
            await reply_error(
                interaction, "このチケットを閉じる権限がありません（作成者・サポート・管理者のみ）。"
            )
            return
        view = ConfirmCloseView(self, channel)
        await interaction.response.send_message(
            "本当にこのチケットを閉じますか？\n"
            "会話の記録（トランスクリプト）が保存され、チャンネルは削除されます。",
            view=view,
            ephemeral=True,
        )

    # ---------- トランスクリプト & 閉鎖の共通処理 ----------

    async def build_transcript(self, channel: discord.TextChannel) -> str:
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
        lines = [
            f"チケット: {channel.name} (#{channel.id})",
            f"サーバー: {channel.guild.name} ({channel.guild.id})",
            "=" * 60,
            "",
        ]
        async for msg in channel.history(limit=None, oldest_first=True):
            content = msg.content or "[添付ファイル・埋め込み等]"
            lines.append(
                f"[{msg.created_at}] {msg.author} ({msg.author.id}): {content}"
            )
        path = os.path.join(
            TRANSCRIPT_DIR, f"{channel.name}-{channel.id}.txt"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path

    async def finalize_close(
        self,
        channel: discord.TextChannel,
        *,
        auto: bool = False,
        closed_by: discord.abc.User = None,
        followup: discord.Interaction = None,
    ):
        row = db.get_ticket(channel.id)
        if row is None:
            return False
        guild = channel.guild

        transcript_path = None
        try:
            transcript_path = await self.build_transcript(channel)
        except (discord.HTTPException, OSError) as e:
            print(f"⚠️ トランスクリプト生成に失敗しました: {e}")

        log_id = settings.get(guild.id, settings.LOG_CHANNEL_ID)
        if log_id:
            log_channel = guild.get_channel(int(log_id))
            if isinstance(log_channel, discord.TextChannel):
                label = "🤖 自動クローズ" if auto else "🔒 チケット閉鎖"
                text = (
                    f"{label}: `{channel.name}`"
                    f"（作成者: <@{row['owner_id']}>、種類: {type_label(row['ticket_type'])}）"
                )
                try:
                    if transcript_path:
                        await log_channel.send(
                            text, file=discord.File(transcript_path)
                        )
                    else:
                        await log_channel.send(text)
                except discord.HTTPException:
                    pass

        owner_id = row["owner_id"]
        if closed_by is None or closed_by.id != owner_id:
            owner = guild.get_member(owner_id)
            if owner is None:
                try:
                    owner = await self.bot.fetch_user(owner_id)
                except discord.HTTPException:
                    owner = None
            if owner is not None:
                reason = (
                    "一定時間発言がなかったため自動で閉じられました。"
                    if auto
                    else "スタッフまたは作成者によって閉じられました。"
                )
                try:
                    await owner.send(
                        f"🔒 チケット `{channel.name}` は閉じられました。{reason}\n"
                        f"件名: {row['subject'] or '(なし)'}"
                    )
                except discord.HTTPException:
                    pass

        if settings.get_ticket_rating_enabled(guild.id):
            await self._send_rating_prompt(guild, row, channel.name)

        db.delete_ticket(channel.id)

        if followup is not None:
            try:
                await followup.followup.send(
                    "✅ チケットを閉鎖しました。記録はログチャンネルに保存されました。",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

        try:
            await channel.delete(
                reason="自動クローズ" if auto else "チケット閉鎖"
            )
        except discord.HTTPException:
            pass
        return True

    async def _send_rating_prompt(
        self, guild: discord.Guild, row: dict, channel_name: str
    ):
        """閉鎖したチケットの作成者へ評価 DM を送る (失敗は静かに諦める)。"""
        owner_id = row["owner_id"]
        owner = guild.get_member(owner_id)
        if owner is None:
            try:
                owner = await self.bot.fetch_user(owner_id)
            except discord.HTTPException:
                owner = None
        if owner is None or owner.bot:
            return
        # クリック時に「作成者本人か」を再起動後も検証できるよう、
        # tickets 行が消える前に評価待ち (rating=0) として記録しておく
        db.create_ticket_rating_prompt(
            row["channel_id"], guild.id, owner_id, time.time()
        )
        try:
            await owner.send(
                f"⭐ チケット `{channel_name}` の対応はどうでしたか？\n"
                f"件名: {row['subject'] or '(なし)'}\n"
                "下のボタンで評価してください（⭐1〜⭐5、任意・1回だけ）。",
                view=build_rating_view(row["channel_id"]),
            )
        except discord.HTTPException:
            # DM 拒否などで送れなかった評価待ち行は残しても意味がないので消す
            db.delete_ticket_rating(row["channel_id"])

    # ---------- /ticket コマンド群 ----------

    ticket_group = app_commands.Group(
        name="ticket", description="チケット操作（チャンネル内で使用）"
    )

    def _require_ticket(self, interaction: discord.Interaction):
        row = self._get_row_or_none(interaction)
        return row

    def _can_manage(self, interaction: discord.Interaction, row: dict) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            return False
        return (
            member.id == row["owner_id"]
            or checks.member_is_support(member, interaction.guild.id)
        )

    @ticket_group.command(name="assign", description="担当者を指定します（サポート/管理者）")
    @app_commands.describe(member="担当にするメンバー")
    async def ticket_assign(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        row = self._require_ticket(interaction)
        if row is None or interaction.guild is None:
            await reply_error(interaction, "このコマンドはチケットチャンネルでのみ使えます。")
            return
        if not checks.is_support_or_admin(interaction, interaction.guild.id):
            await reply_error(interaction, "担当を指定できるのはサポートと管理者だけです。")
            return
        db.set_ticket_assignee(row["channel_id"], member.id)
        row = db.get_ticket(row["channel_id"])
        await self._update_status_field(row, f"担当: {member.mention}")
        await interaction.response.send_message(
            f"🙋 担当を {member.mention} に設定しました。"
        )

    @ticket_group.command(name="add", description="メンバーをチケットに追加します")
    @app_commands.describe(member="追加するメンバー")
    async def ticket_add(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        row = self._require_ticket(interaction)
        channel = interaction.channel
        if row is None or not isinstance(channel, discord.TextChannel):
            await reply_error(interaction, "このコマンドはチケットチャンネルでのみ使えます。")
            return
        if not self._can_manage(interaction, row):
            await reply_error(interaction, "メンバーを追加する権限がありません。")
            return
        try:
            await channel.set_permissions(
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason="チケットへのメンバー追加",
            )
        except discord.HTTPException as e:
            await reply_error(interaction, f"追加に失敗しました。\n`{e}`")
            return
        await interaction.response.send_message(
            f"➕ {member.mention} をこのチケットに追加しました。"
        )

    @ticket_group.command(name="remove", description="メンバーをチケットから外します")
    @app_commands.describe(member="外すメンバー")
    async def ticket_remove(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        row = self._require_ticket(interaction)
        channel = interaction.channel
        guild = interaction.guild
        if row is None or not isinstance(channel, discord.TextChannel) or guild is None:
            await reply_error(interaction, "このコマンドはチケットチャンネルでのみ使えます。")
            return
        if not self._can_manage(interaction, row):
            await reply_error(interaction, "メンバーを外す権限がありません。")
            return
        if member.id == row["owner_id"]:
            await reply_error(interaction, "チケットの作成者は外せません。")
            return
        support_role_id = settings.get(guild.id, settings.SUPPORT_ROLE_ID)
        if support_role_id and any(
            role.id == int(support_role_id) for role in member.roles
        ):
            await reply_error(interaction, "サポートロールのメンバーは外せません。")
            return
        try:
            await channel.set_permissions(
                member, overwrite=None, reason="チケットからのメンバー削除"
            )
        except discord.HTTPException as e:
            await reply_error(interaction, f"削除に失敗しました。\n`{e}`")
            return
        if row["assignee_id"] == member.id:
            db.set_ticket_assignee(row["channel_id"], None)
            row = db.get_ticket(row["channel_id"])
            await self._update_status_field(row, "未担当")
        await interaction.response.send_message(
            f"➖ {member.mention} をこのチケットから外しました。"
        )

    @ticket_group.command(
        name="notify",
        description="種類ごとの担当通知ロールを設定します（サポート/管理者）",
    )
    @app_commands.describe(
        ticket_type="チケットの種類",
        role="通知するロール（省略すると解除）",
    )
    @app_commands.choices(
        ticket_type=[
            app_commands.Choice(
                name=f"{info['emoji']} {info['label']}", value=key
            )
            for key, info in TICKET_TYPES.items()
        ]
    )
    async def ticket_notify(
        self,
        interaction: discord.Interaction,
        ticket_type: str,
        role: discord.Role = None,
    ):
        guild = interaction.guild
        if guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        if not checks.is_support_or_admin(interaction, guild.id):
            await reply_error(
                interaction, "担当通知を設定できるのはサポートと管理者だけです。"
            )
            return
        if not valid_ticket_type(ticket_type):
            await reply_error(interaction, "不明なチケットの種類です。")
            return
        label = type_label(ticket_type)
        if role is None:
            current = settings.get_ticket_notify_role(guild.id, ticket_type)
            settings.set_ticket_notify_role(guild.id, ticket_type, None)
            if current:
                await interaction.response.send_message(
                    f"✅ {label} の担当通知ロールを解除しました。",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"{label} の担当通知ロールは未設定です。",
                    ephemeral=True,
                )
            return
        settings.set_ticket_notify_role(guild.id, ticket_type, role.id)
        await interaction.response.send_message(
            f"✅ {label} の担当通知ロールを {role.mention} に設定しました。\n"
            f"この種類のチケットが作られると、チャンネル内で担当者をお呼びします。",
            ephemeral=True,
        )

    @ticket_group.command(
        name="ratings",
        description="チケット評価の集計を表示します（サポート/管理者）",
    )
    async def ticket_ratings(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        if not checks.is_support_or_admin(interaction, guild.id):
            await reply_error(
                interaction, "評価の集計を見られるのはサポートと管理者だけです。"
            )
            return
        stats = db.ticket_rating_stats(guild.id)
        if stats["count"] == 0:
            await interaction.response.send_message(
                "まだチケット評価がありません。", ephemeral=True
            )
            return
        distribution = " / ".join(
            f"⭐{stars}: {stats['distribution'][stars]}件"
            for stars in range(5, 0, -1)
        )
        await interaction.response.send_message(
            f"⭐ チケット評価の集計\n"
            f"平均: ⭐{stats['average']:.1f}（{stats['count']}件）\n"
            f"内訳: {distribution}",
            ephemeral=True,
        )

    # ---------- アクティビティ記録 & 自動クローズ ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        row = db.get_ticket(message.channel.id)
        if row is None:
            return
        now = time.time()
        if now - row["last_activity_at"] >= ACTIVITY_WRITE_INTERVAL:
            db.touch_ticket(message.channel.id, now)

    @tasks.loop(minutes=10)
    async def auto_close_loop(self):
        now = time.time()
        for row in db.list_tickets():
            hours = settings.get_auto_close_hours(row["guild_id"])
            if hours <= 0:
                continue
            if now - row["last_activity_at"] < hours * 3600:
                continue
            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                db.delete_ticket(row["channel_id"])
                continue
            if isinstance(channel, discord.TextChannel):
                await self.finalize_close(channel, auto=True)

    @auto_close_loop.before_loop
    async def before_auto_close_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
