import os
import re
import time

import discord
from discord.ext import commands, tasks
from discord import app_commands

import jsonstore
from ticket_store import TicketStore

TICKET_CONFIG_PATH = "config/ticket_settings.json"
TRANSCRIPT_DIR = "data/transcripts"

# チケットの種類 (値 -> 表示名)
TICKET_TYPES = {
    "question": "質問・相談",
    "report": "通報",
    "whitelist": "ホワイトリスト申請",
    "other": "その他"
}

DEFAULT_AUTO_CLOSE_HOURS = 48


def parse_topic_owner_id(channel) -> int:
    """旧方式の topic (`ticket_owner:<id>`) から所有者IDを取り出す。"""
    topic = getattr(channel, "topic", None)

    if topic and topic.startswith("ticket_owner:"):
        try:
            return int(topic.split(":", 1)[1])
        except ValueError:
            return None

    return None


def sanitize_channel_name(name: str) -> str:
    """Discordのチャンネル名に使えない文字を取り除く。"""
    text = (name or "").lower()
    text = re.sub(r"[^a-z0-9\-_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)

    return text.strip("-_")


def ticket_channel_name(ticket_type: str, user) -> str:
    base = sanitize_channel_name(user.name) or "user"

    return f"ticket-{ticket_type}-{base}"[:100]


class TicketView(discord.ui.View):
    """チケットパネル用View (種類選択セレクト + 旧作成ボタン)。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        custom_id="ticket_type_select",
        placeholder="チケットの種類を選んでください",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label=label,
                value=value
            )
            for value, label in TICKET_TYPES.items()
        ]
    )
    async def ticket_type_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select
    ):
        cog = interaction.client.get_cog("TicketCog")

        if cog is None:
            await interaction.response.send_message(
                "⚠️ システムエラーが発生しました。",
                ephemeral=True
            )
            return

        await cog.create_ticket_channel(
            interaction,
            ticket_type=select.values[0]
        )

    @discord.ui.button(
        label="🎫 チケットを作成",
        style=discord.ButtonStyle.primary,
        custom_id="create_ticket"
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        cog = interaction.client.get_cog("TicketCog")

        if cog is None:
            await interaction.response.send_message(
                "⚠️ システムエラーが発生しました。",
                ephemeral=True
            )
            return

        # 旧パネルのボタンは種類「その他」で作成する
        await cog.create_ticket_channel(
            interaction,
            ticket_type="other"
        )


class TicketControlsView(discord.ui.View):
    """チケットチャンネル内の操作View (閉じる + 担当する)。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🙋 担当する",
        style=discord.ButtonStyle.primary,
        custom_id="claim_ticket"
    )
    async def claim_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        cog = interaction.client.get_cog("TicketCog")

        if cog is None:
            await interaction.response.send_message(
                "⚠️ システムエラーが発生しました。",
                ephemeral=True
            )
            return

        guild = interaction.guild
        channel = interaction.channel

        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "⚠️ この操作はチケットチャンネルでのみ使用できます。",
                ephemeral=True
            )
            return

        record = cog.get_ticket_record(channel)

        if record is None:
            await interaction.response.send_message(
                "⚠️ このチャンネルはチケットではありません。",
                ephemeral=True
            )
            return

        settings = cog.get_settings(guild.id)

        if not cog.is_support_or_admin(interaction.user, settings):
            await interaction.response.send_message(
                "⚠️ 担当できるのはサポートスタッフまたは管理者だけです。",
                ephemeral=True
            )
            return

        assignee_id = record.get("assignee_id")

        if assignee_id == interaction.user.id:
            await interaction.response.send_message(
                "✅ あなたはすでにこのチケットの担当です。",
                ephemeral=True
            )
            return

        if assignee_id:
            await interaction.response.send_message(
                f"⚠️ このチケットはすでに <@{assignee_id}> が担当しています。\n"
                "担当を変える場合は `/ticket-assign` を使ってください。",
                ephemeral=True
            )
            return

        cog.ensure_ticket_row(channel, record)
        cog.store.set_assignee(
            channel.id,
            interaction.user.id
        )

        await channel.send(
            f"🙋 {interaction.user.mention} が"
            "このチケットの担当になりました。"
        )

        # このメッセージの担当ボタンだけを無効化する
        new_view = TicketControlsView()

        for child in new_view.children:
            if getattr(child, "custom_id", None) == "claim_ticket":
                child.disabled = True
                child.label = "担当済み"

        try:
            await interaction.response.edit_message(
                view=new_view
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "🙋 担当になりました。",
                ephemeral=True
            )

    @discord.ui.button(
        label="🔒 チケットを閉じる",
        style=discord.ButtonStyle.danger,
        custom_id="close_ticket"
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        cog = interaction.client.get_cog("TicketCog")

        if cog is None:
            await interaction.response.send_message(
                "⚠️ システムエラーが発生しました。",
                ephemeral=True
            )
            return

        guild = interaction.guild
        channel = interaction.channel

        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "⚠️ この操作はチケットチャンネルでのみ使用できます。",
                ephemeral=True
            )
            return

        settings = cog.get_settings(guild.id)

        support_role_id = settings.get("support_role_id")

        support_role = None

        if support_role_id:
            support_role = guild.get_role(int(support_role_id))

        is_support = (
            support_role is not None
            and isinstance(interaction.user, discord.Member)
            and support_role in interaction.user.roles
        )

        owner_id = cog.get_owner_id(channel)

        is_owner = interaction.user.id == owner_id

        if not (is_owner or is_support):
            await interaction.response.send_message(
                "⚠️ このチケットを閉じる権限がありません。",
                ephemeral=True
            )
            return

        view = ConfirmCloseView(channel)

        await interaction.response.send_message(
            "本当にこのチケットを閉じますか？\n"
            "この操作は取り消せません。",
            view=view,
            ephemeral=True
        )


# 旧名 (後方互換)
CloseView = TicketControlsView


class ConfirmCloseView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel):
        super().__init__(timeout=60)
        self.channel = channel

    @discord.ui.button(
        label="✅ はい、閉じます",
        style=discord.ButtonStyle.danger,
        custom_id="confirm_close"
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        cog = interaction.client.get_cog("TicketCog")

        if cog is None:
            await interaction.response.send_message(
                "⚠️ システムエラーが発生しました。",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            filename = await cog.build_transcript(
                self.channel
            )

            settings = cog.get_settings(interaction.guild.id)
            log_channel_id = settings.get("log_channel_id")

            if log_channel_id:
                log_channel = interaction.guild.get_channel(
                    int(log_channel_id)
                )

                if isinstance(log_channel, discord.TextChannel):
                    try:
                        await log_channel.send(
                            f"🔒 チケット `{self.channel.name}` "
                            f"が閉鎖されました。",
                            file=discord.File(filename)
                        )
                    except discord.HTTPException:
                        pass

            cog.store.delete_ticket(self.channel.id)

            await interaction.followup.send(
                "✅ チケットを閉鎖します。\n"
                "トランスクリプトを保存しました。",
                ephemeral=True
            )

            await self.channel.delete(
                reason=f"{interaction.user} がチケットを閉鎖しました。"
            )

        except discord.NotFound:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ チャンネルがすでに削除されています。",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "⚠️ チャンネルがすでに削除されています。",
                    ephemeral=True
                )

        except Exception as e:
            await interaction.followup.send(
                f"⚠️ 閉鎖中にエラーが発生しました。\n"
                f"`{type(e).__name__}: {e}`",
                ephemeral=True
            )

    @discord.ui.button(
        label="❌ キャンセル",
        style=discord.ButtonStyle.secondary,
        custom_id="cancel_close"
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_message(
            "チケット閉鎖をキャンセルしました。",
            ephemeral=True
        )


class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_path = TICKET_CONFIG_PATH
        self.store = TicketStore()

        os.makedirs(
            os.path.dirname(self.config_path),
            exist_ok=True
        )

        if not os.path.exists(self.config_path):
            jsonstore.write_json(self.config_path, {})

        self.auto_close_loop.start()

    def cog_unload(self):
        self.auto_close_loop.cancel()

    # ---------- 設定 ----------

    def get_settings(self, guild_id: int) -> dict:
        data = jsonstore.read_json(
            self.config_path,
            {}
        )
        return data.get(str(guild_id), {})

    def save_settings(
        self,
        guild_id: int,
        settings: dict
    ):
        def mutator(data):
            data[str(guild_id)] = settings
            return data

        jsonstore.update_json(
            self.config_path,
            {},
            mutator
        )

    # ---------- チケット記録 ----------

    def get_ticket_record(self, channel):
        """DBのチケット記録を返す。なければ旧topic方式も確認する。"""
        row = self.store.get_ticket(channel.id)

        if row is not None:
            return row

        owner_id = parse_topic_owner_id(channel)

        if owner_id is None:
            return None

        return {
            "channel_id": channel.id,
            "guild_id": (
                channel.guild.id
                if channel.guild
                else None
            ),
            "owner_id": owner_id,
            "assignee_id": None,
            "ticket_type": "other",
            "created_at": 0,
            "last_activity_at": 0,
            "legacy": True
        }

    def ensure_ticket_row(self, channel, record):
        """旧方式チケットのDB行がなければ作成し、記録を返す。"""
        row = self.store.get_ticket(channel.id)

        if row is not None:
            return row

        return self.store.create_ticket(
            channel_id=channel.id,
            guild_id=record["guild_id"],
            owner_id=record["owner_id"],
            ticket_type=record.get("ticket_type", "other"),
            assignee_id=record.get("assignee_id"),
            now=time.time()
        )

    def get_owner_id(self, channel) -> int:
        """チケットの所有者IDを返す (DB優先、旧topicにフォールバック)。"""
        record = self.get_ticket_record(channel)

        if record is None:
            return None

        return record["owner_id"]

    # ---------- 権限判定 ----------

    def is_support_member(self, member, settings) -> bool:
        if not isinstance(member, discord.Member):
            return False

        support_role_id = settings.get("support_role_id")

        if not support_role_id:
            return False

        return any(
            role.id == int(support_role_id)
            for role in member.roles
        )

    def is_support_or_admin(self, member, settings) -> bool:
        if not isinstance(member, discord.Member):
            return False

        if member.guild_permissions.administrator:
            return True

        return self.is_support_member(member, settings)

    def can_manage_ticket(self, member, record, settings) -> bool:
        if not isinstance(member, discord.Member):
            return False

        if member.id == record["owner_id"]:
            return True

        return self.is_support_or_admin(member, settings)

    # ---------- トランスクリプト ----------

    async def build_transcript(self, channel) -> str:
        """チケットの会話ログをファイルに書き出し、そのパスを返す。

        手動クローズと自動クローズで共通利用する。
        保存先はDockerのvolume内 (data/transcripts/)。
        """
        messages = []

        async for msg in channel.history(
            limit=None,
            oldest_first=True
        ):
            content = msg.content

            if not content:
                content = "[添付ファイル・埋め込み等]"

            messages.append(
                f"[{msg.created_at}] {msg.author} ({msg.author.id}): "
                f"{content}"
            )

        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

        filename = (
            f"{TRANSCRIPT_DIR}/"
            f"{channel.name}_"
            f"{channel.guild.id}.txt"
        )

        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"チケット: {channel.name}\n")
            f.write(f"サーバー: {channel.guild.name}\n")
            f.write(f"サーバーID: {channel.guild.id}\n")
            f.write("=" * 60 + "\n\n")
            f.write("\n".join(messages))

        return filename

    # ---------- チケット作成 ----------

    async def create_ticket_channel(
        self,
        interaction: discord.Interaction,
        ticket_type: str = "other"
    ):
        if ticket_type not in TICKET_TYPES:
            ticket_type = "other"

        guild = interaction.guild
        user = interaction.user

        if guild is None:
            await interaction.response.send_message(
                "⚠️ この操作はサーバー内でのみ使用できます。",
                ephemeral=True
            )
            return

        settings = self.get_settings(guild.id)

        category_id = settings.get("category_id")
        support_role_id = settings.get("support_role_id")

        if not category_id or not support_role_id:
            await interaction.response.send_message(
                "⚠️ チケット機能が初期設定されていません。"
                "管理者が `/setup-ticket` を実行してください。",
                ephemeral=True
            )
            return

        category = guild.get_channel(int(category_id))

        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "⚠️ 設定されたカテゴリが見つかりません。",
                ephemeral=True
            )
            return

        support_role = guild.get_role(int(support_role_id))

        if support_role is None:
            await interaction.response.send_message(
                "⚠️ 設定されたサポートロールが見つかりません。",
                ephemeral=True
            )
            return

        existing = None

        # 既存チケットの確認はDBを優先し、旧方式のtopicにも対応する
        row = self.store.get_open_ticket_by_owner(
            guild.id,
            user.id
        )

        if row is not None:
            existing_channel = guild.get_channel(
                row["channel_id"]
            )

            if isinstance(existing_channel, discord.TextChannel):
                existing = existing_channel

        if existing is None:
            for channel in category.channels:
                if channel.name.startswith("ticket-"):
                    if parse_topic_owner_id(channel) == user.id:
                        existing = channel
                        break

        if existing:
            await interaction.response.send_message(
                f"⚠️ あなたは既にチケット {existing.mention} を持っています。",
                ephemeral=True
            )
            return

        bot_member = guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "⚠️ Bot情報を取得できませんでした。",
                ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            ),
            support_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True
            )
        }

        try:
            channel = await guild.create_text_channel(
                name=ticket_channel_name(ticket_type, user),
                category=category,
                topic=f"ticket_owner:{user.id}",
                overwrites=overwrites,
                reason=f"{user} がチケットを作成しました。"
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Botにチャンネルを作成する権限がありません。",
                ephemeral=True
            )
            return

        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"⚠️ チャンネル作成中にエラーが発生しました。\n`{e}`",
                ephemeral=True
            )
            return

        self.store.create_ticket(
            channel_id=channel.id,
            guild_id=guild.id,
            owner_id=user.id,
            ticket_type=ticket_type,
            now=time.time()
        )

        embed = discord.Embed(
            title="🎫 サポートチケット",
            description=(
                f"{user.mention} さん、ご用件をお聞かせください。\n"
                "サポートスタッフが対応します。"
            ),
            color=discord.Color.blue()
        )

        embed.add_field(
            name="チケットの種類",
            value=TICKET_TYPES[ticket_type],
            inline=False
        )

        embed.add_field(
            name="チケット作成者",
            value=user.mention,
            inline=False
        )

        embed.set_footer(
            text="チケットを閉じるには下のボタンを押してください。"
        )

        controls_view = TicketControlsView()

        try:
            await channel.send(
                content=f"{support_role.mention} {user.mention}",
                embed=embed,
                view=controls_view
            )
        except discord.HTTPException as e:
            self.store.delete_ticket(channel.id)
            await channel.delete(reason="チケットメッセージ送信失敗")
            await interaction.response.send_message(
                f"⚠️ チケットメッセージの送信に失敗しました。\n`{e}`",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ チケットを作成しました！ → {channel.mention}",
            ephemeral=True
        )

        log_channel_id = settings.get("log_channel_id")

        if log_channel_id:
            log_channel = guild.get_channel(int(log_channel_id))

            if isinstance(log_channel, discord.TextChannel):
                try:
                    await log_channel.send(
                        f"📩 {user.mention} がチケット "
                        f"`{channel.name}` "
                        f"（{TICKET_TYPES[ticket_type]}）を作成しました。"
                    )
                except discord.HTTPException:
                    pass

    # ---------- アクティビティ記録 / 自動クローズ ----------

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):
        if message.author.bot:
            return

        if not message.guild:
            return

        if not isinstance(message.channel, discord.TextChannel):
            return

        row = self.store.get_ticket(message.channel.id)

        if row is None:
            return

        now = time.time()

        # 書き込み頻度を抑えるため、60秒以上古い場合だけ更新する
        if now - row["last_activity_at"] >= 60:
            self.store.touch_activity(
                message.channel.id,
                now
            )

    @tasks.loop(minutes=10)
    async def auto_close_loop(self):
        now = time.time()

        for row in self.store.list_tickets():
            try:
                await self._auto_close_if_inactive(row, now)
            except Exception as e:
                print(
                    "⚠️ 自動クローズ処理でエラーが発生しました "
                    f"(channel_id={row['channel_id']}): {e}"
                )

    @auto_close_loop.before_loop
    async def before_auto_close_loop(self):
        await self.bot.wait_until_ready()

    async def _auto_close_if_inactive(self, row: dict, now: float):
        settings = self.get_settings(row["guild_id"])

        hours = settings.get(
            "auto_close_hours",
            DEFAULT_AUTO_CLOSE_HOURS
        )

        try:
            hours = float(hours)
        except (TypeError, ValueError):
            return

        if hours <= 0:
            return

        if now - row["last_activity_at"] < hours * 3600:
            return

        guild = self.bot.get_guild(row["guild_id"])

        if guild is None:
            return

        channel = guild.get_channel(row["channel_id"])

        if not isinstance(channel, discord.TextChannel):
            # チャンネルが既に消えている場合は記録だけ掃除する
            self.store.delete_ticket(row["channel_id"])
            return

        filename = await self.build_transcript(channel)

        log_channel_id = settings.get("log_channel_id")

        if log_channel_id:
            log_channel = guild.get_channel(int(log_channel_id))

            if isinstance(log_channel, discord.TextChannel):
                try:
                    await log_channel.send(
                        f"🔒 チケット `{channel.name}` は "
                        f"{hours:g} 時間発言がなかったため"
                        "自動クローズされました。",
                        file=discord.File(filename)
                    )
                except discord.HTTPException:
                    pass

        owner = guild.get_member(row["owner_id"])

        if owner is None:
            owner = self.bot.get_user(row["owner_id"])

        if owner is not None:
            try:
                await owner.send(
                    f"🔒 サーバー「{guild.name}」のチケット "
                    f"`{channel.name}` は、一定時間発言がなかったため"
                    "自動的に閉じられました。\n"
                    "ご用件が解決していない場合は、"
                    "新しいチケットを作成してください。"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        self.store.delete_ticket(channel.id)

        await channel.delete(
            reason="一定時間発言がなかったため自動クローズしました。"
        )

    # ---------- コマンド共通チェック ----------

    async def _ticket_context(self, interaction: discord.Interaction):
        """チケットコマンド共通の事前チェック。

        (record, channel) を返す。問題がある場合はエラーを返信済みで
        (None, None) を返す。
        """
        guild = interaction.guild
        channel = interaction.channel

        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "⚠️ このコマンドはチケットチャンネルでのみ使用できます。",
                ephemeral=True
            )
            return None, None

        record = self.get_ticket_record(channel)

        if record is None:
            await interaction.response.send_message(
                "⚠️ このチャンネルはチケットではありません。",
                ephemeral=True
            )
            return None, None

        return record, channel

    # ---------- スラッシュコマンド ----------

    @app_commands.command(
        name="setup-ticket",
        description="チケット機能の初期設定を行います（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        category="チケットを作成するカテゴリ",
        support_role="サポートスタッフのロール",
        log_channel="ログを送信するチャンネル（任意）",
        auto_close_hours="自動クローズまでの無発言時間（時間、0で無効、既定48）"
    )
    async def setup_ticket(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        support_role: discord.Role,
        log_channel: discord.TextChannel = None,
        auto_close_hours: int = DEFAULT_AUTO_CLOSE_HOURS
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "⚠️ このコマンドはサーバー内でのみ使用できます。",
                ephemeral=True
            )
            return

        if auto_close_hours < 0:
            await interaction.response.send_message(
                "⚠️ auto_close_hours は0以上を指定してください。",
                ephemeral=True
            )
            return

        guild_id = interaction.guild_id

        settings = {
            "category_id": category.id,
            "support_role_id": support_role.id,
            "log_channel_id": (
                log_channel.id
                if log_channel
                else None
            ),
            "auto_close_hours": auto_close_hours
        }

        try:
            self.save_settings(
                guild_id,
                settings
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ 設定の保存に失敗しました。\n"
                f"`{type(e).__name__}: {e}`",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="✅ チケット設定完了",
            description=(
                f"カテゴリ: {category.mention}\n"
                f"サポートロール: {support_role.mention}"
            ),
            color=discord.Color.green()
        )

        if log_channel:
            embed.add_field(
                name="ログチャンネル",
                value=log_channel.mention,
                inline=False
            )

        embed.add_field(
            name="自動クローズ",
            value=(
                f"{auto_close_hours} 時間発言がない場合に自動クローズ"
                if auto_close_hours > 0
                else "無効"
            ),
            inline=False
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @app_commands.command(
        name="send-ticket-panel",
        description="チケット作成パネルを送信します（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        channel="パネルを表示するチャンネル"
    )
    async def send_ticket_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "⚠️ このコマンドはサーバー内でのみ使用できます。",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎫 サポートチケット",
            description=(
                "何か問題や質問がある場合は、"
                "下のセレクトメニューからチケットの種類を選んでください。\n"
                "スタッフがプライベートチャンネルで対応します。\n"
                "（ボタンからも「その他」チケットを作成できます）"
            ),
            color=discord.Color.blue()
        )

        view = TicketView()

        try:
            await channel.send(
                embed=embed,
                view=view
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Botにそのチャンネルへメッセージを送信する権限がありません。",
                ephemeral=True
            )
            return

        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"⚠️ パネル送信中にエラーが発生しました。\n"
                f"`{e}`",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ チケットパネルを {channel.mention} に送信しました。",
            ephemeral=True
        )

    @app_commands.command(
        name="ticket-assign",
        description="チケットの担当者を指定します（サポート/管理者専用）"
    )
    @app_commands.describe(
        member="担当にするメンバー"
    )
    async def ticket_assign(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        record, channel = await self._ticket_context(interaction)

        if record is None:
            return

        settings = self.get_settings(interaction.guild.id)

        if not self.is_support_or_admin(interaction.user, settings):
            await interaction.response.send_message(
                "⚠️ 担当を指定できるのはサポートスタッフまたは管理者だけです。",
                ephemeral=True
            )
            return

        self.ensure_ticket_row(channel, record)
        self.store.set_assignee(
            channel.id,
            member.id
        )

        await channel.send(
            f"🙋 {member.mention} が"
            "このチケットの担当になりました。"
        )

        await interaction.response.send_message(
            f"✅ 担当者を {member.mention} に設定しました。",
            ephemeral=True
        )

    @app_commands.command(
        name="ticket-add",
        description="チケットにメンバーを追加します"
    )
    @app_commands.describe(
        member="追加するメンバー"
    )
    async def ticket_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        record, channel = await self._ticket_context(interaction)

        if record is None:
            return

        settings = self.get_settings(interaction.guild.id)

        if not self.can_manage_ticket(
            interaction.user,
            record,
            settings
        ):
            await interaction.response.send_message(
                "⚠️ メンバーを追加できるのはチケット作成者・"
                "サポートスタッフ・管理者だけです。",
                ephemeral=True
            )
            return

        try:
            await channel.set_permissions(
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason=f"{interaction.user} がチケットにメンバーを追加しました。"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Botにチャンネルの権限を変更する権限がありません。",
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"⚠️ メンバーの追加中にエラーが発生しました。\n`{e}`",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ {member.mention} をチケットに追加しました。",
            ephemeral=True
        )

    @app_commands.command(
        name="ticket-remove",
        description="チケットからメンバーを削除します"
    )
    @app_commands.describe(
        member="削除するメンバー"
    )
    async def ticket_remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        record, channel = await self._ticket_context(interaction)

        if record is None:
            return

        settings = self.get_settings(interaction.guild.id)

        if not self.can_manage_ticket(
            interaction.user,
            record,
            settings
        ):
            await interaction.response.send_message(
                "⚠️ メンバーを削除できるのはチケット作成者・"
                "サポートスタッフ・管理者だけです。",
                ephemeral=True
            )
            return

        if member.id == record["owner_id"]:
            await interaction.response.send_message(
                "⚠️ チケットの作成者は削除できません。",
                ephemeral=True
            )
            return

        if self.is_support_member(member, settings):
            await interaction.response.send_message(
                "⚠️ サポートスタッフは削除できません。",
                ephemeral=True
            )
            return

        try:
            await channel.set_permissions(
                member,
                overwrite=None,
                reason=f"{interaction.user} がチケットからメンバーを削除しました。"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Botにチャンネルの権限を変更する権限がありません。",
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"⚠️ メンバーの削除中にエラーが発生しました。\n`{e}`",
                ephemeral=True
            )
            return

        if record.get("assignee_id") == member.id:
            self.ensure_ticket_row(channel, record)
            self.store.set_assignee(
                channel.id,
                None
            )

        await interaction.response.send_message(
            f"✅ {member.mention} をチケットから削除しました。",
            ephemeral=True
        )

    @app_commands.command(
        name="sync",
        description="スラッシュコマンドを手動同期します（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_commands(
        self,
        interaction: discord.Interaction
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "⚠️ このコマンドはサーバー内でのみ使用できます。",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            guild = discord.Object(
                id=interaction.guild_id
            )

            interaction.client.tree.copy_global_to(
                guild=guild
            )

            synced = await interaction.client.tree.sync(
                guild=guild
            )

            command_names = [
                command.name
                for command in synced
            ]

            await interaction.followup.send(
                "✅ コマンドを同期しました！\n"
                f"登録数: {len(command_names)}\n"
                f"コマンド: {', '.join(command_names)}",
                ephemeral=True
            )

        except Exception as e:
            await interaction.followup.send(
                f"❌ 同期エラー:\n"
                f"`{type(e).__name__}: {e}`",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
