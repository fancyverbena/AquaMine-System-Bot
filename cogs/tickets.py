import json
import os

import discord
from discord.ext import commands
from discord import app_commands


TICKET_CONFIG_PATH = "config/ticket_settings.json"


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

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

        guild = interaction.guild
        user = interaction.user

        if guild is None:
            await interaction.response.send_message(
                "⚠️ このボタンはサーバー内でのみ使用できます。",
                ephemeral=True
            )
            return

        settings = cog.get_settings(guild.id)

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

        for channel in category.channels:
            if channel.name.startswith("ticket-"):
                if channel.topic == f"ticket_owner:{user.id}":
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
                name=f"ticket-{user.name}",
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

        embed = discord.Embed(
            title="🎫 サポートチケット",
            description=(
                f"{user.mention} さん、ご用件をお聞かせください。\n"
                "サポートスタッフが対応します。"
            ),
            color=discord.Color.blue()
        )

        embed.add_field(
            name="チケット作成者",
            value=user.mention,
            inline=False
        )

        embed.set_footer(
            text="チケットを閉じるには下のボタンを押してください。"
        )

        close_view = CloseView()

        try:
            await channel.send(
                content=f"{support_role.mention} {user.mention}",
                embed=embed,
                view=close_view
            )
        except discord.HTTPException as e:
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
                        f"`{channel.name}` を作成しました。"
                    )
                except discord.HTTPException:
                    pass


class CloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

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

        owner_id = None

        if channel.topic and channel.topic.startswith("ticket_owner:"):
            try:
                owner_id = int(channel.topic.split(":", 1)[1])
            except ValueError:
                owner_id = None

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
        await interaction.response.defer(ephemeral=True)

        try:
            messages = []

            async for msg in self.channel.history(
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

            os.makedirs("transcripts", exist_ok=True)

            filename = (
                f"transcripts/"
                f"{self.channel.name}_"
                f"{interaction.guild.id}.txt"
            )

            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"チケット: {self.channel.name}\n")
                f.write(f"サーバー: {interaction.guild.name}\n")
                f.write(f"サーバーID: {interaction.guild.id}\n")
                f.write("=" * 60 + "\n\n")
                f.write("\n".join(messages))

            cog = interaction.client.get_cog("TicketCog")

            if cog:
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

        os.makedirs(
            os.path.dirname(self.config_path),
            exist_ok=True
        )

        if not os.path.exists(self.config_path):
            self._write_config({})

    def _read_config(self) -> dict:
        try:
            with open(
                self.config_path,
                "r",
                encoding="utf-8"
            ) as f:
                content = f.read().strip()

            if not content:
                return {}

            data = json.loads(content)

            if not isinstance(data, dict):
                return {}

            return data

        except (
            FileNotFoundError,
            json.JSONDecodeError,
            UnicodeDecodeError
        ):
            self._write_config({})
            return {}

    def _write_config(self, data: dict):
        os.makedirs(
            os.path.dirname(self.config_path),
            exist_ok=True
        )

        temp_path = f"{self.config_path}.tmp"

        with open(
            temp_path,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            temp_path,
            self.config_path
        )

    def get_settings(self, guild_id: int) -> dict:
        data = self._read_config()
        return data.get(str(guild_id), {})

    def save_settings(
        self,
        guild_id: int,
        settings: dict
    ):
        data = self._read_config()

        data[str(guild_id)] = settings

        self._write_config(data)

    @app_commands.command(
        name="setup-ticket",
        description="チケット機能の初期設定を行います（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        category="チケットを作成するカテゴリ",
        support_role="サポートスタッフのロール",
        log_channel="ログを送信するチャンネル（任意）"
    )
    async def setup_ticket(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        support_role: discord.Role,
        log_channel: discord.TextChannel = None
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "⚠️ このコマンドはサーバー内でのみ使用できます。",
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
            )
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

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @app_commands.command(
        name="send-ticket-panel",
        description="チケット作成ボタンを送信します（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        channel="ボタンを表示するチャンネル"
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
                "下のボタンを押してチケットを作成してください。\n"
                "スタッフがプライベートチャンネルで対応します。"
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
        name="sync",
        description="スラッシュコマンドを手動同期します（管理者専用）"
    )
    @app_commands.default_permissions(administrator=True)
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