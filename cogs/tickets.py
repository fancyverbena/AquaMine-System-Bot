import json
import os
import discord
from discord.ext import commands
from discord import app_commands

TICKET_CONFIG_PATH = "config/ticket_settings.json"

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 チケットを作成", style=discord.ButtonStyle.primary, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("TicketCog")
        if cog is None:
            await interaction.response.send_message("⚠️ システムエラーが発生しました。", ephemeral=True)
            return

        guild = interaction.guild
        user = interaction.user
        settings = cog.get_settings(guild.id)
        category_id = settings.get("category_id")
        support_role_id = settings.get("support_role_id")

        if not category_id or not support_role_id:
            await interaction.response.send_message(
                "⚠️ チケット機能が初期設定されていません。管理者が `/setup-ticket` を実行してください。",
                ephemeral=True
            )
            return

        category = guild.get_channel(int(category_id))
        if not category:
            await interaction.response.send_message("⚠️ 設定されたカテゴリが見つかりません。", ephemeral=True)
            return

        support_role = guild.get_role(int(support_role_id))
        if not support_role:
            await interaction.response.send_message("⚠️ 設定されたサポートロールが見つかりません。", ephemeral=True)
            return

        existing = discord.utils.get(category.channels, name=f"ticket-{user.name}")
        if existing:
            await interaction.response.send_message(
                f"⚠️ あなたは既にチケット `{existing.mention}` を持っています。",
                ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            support_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{user.name}",
                category=category,
                overwrites=overwrites,
                reason=f"{user} がチケットを作成しました。"
            )
        except discord.Forbidden:
            await interaction.response.send_message("⚠️ Botにカテゴリ内でチャンネルを作成する権限がありません。", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎫 サポートチケット",
            description=f"{user.mention} さん、ご用件をお聞かせください。\nサポートスタッフが対応します。",
            color=discord.Color.blue()
        )
        embed.set_footer(text="チケットを閉じるには /close を実行してください。")

        close_view = CloseView()
        await channel.send(content=f"{support_role.mention} {user.mention}", embed=embed, view=close_view)
        await interaction.response.send_message(
            f"✅ チケットを作成しました！ → {channel.mention}",
            ephemeral=True
        )

        log_channel_id = settings.get("log_channel_id")
        if log_channel_id:
            log_channel = guild.get_channel(int(log_channel_id))
            if log_channel:
                await log_channel.send(f"📩 {user.mention} がチケット `{channel.name}` を作成しました。")


class CloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 チケットを閉じる", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("TicketCog")
        if not cog:
            await interaction.response.send_message("エラー", ephemeral=True)
            return

        settings = cog.get_settings(interaction.guild_id)
        support_role_id = settings.get("support_role_id")
        support_role = interaction.guild.get_role(int(support_role_id)) if support_role_id else None

        if not (interaction.user == interaction.channel.owner or (support_role and support_role in interaction.user.roles)):
            await interaction.response.send_message("⚠️ このチケットを閉じる権限がありません。", ephemeral=True)
            return

        view = ConfirmCloseView(interaction.channel)
        await interaction.response.send_message(
            "本当にこのチケットを閉じますか？（この操作は取り消せません）",
            view=view,
            ephemeral=True
        )


class ConfirmCloseView(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=60)
        self.channel = channel

    @discord.ui.button(label="✅ はい、閉じます", style=discord.ButtonStyle.danger, custom_id="confirm_close")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            messages = []
            async for msg in self.channel.history(limit=100, oldest_first=True):
                messages.append(f"[{msg.created_at}] {msg.author}: {msg.content}")
            
            os.makedirs("transcripts", exist_ok=True)
            filename = f"transcripts/{self.channel.name}_{interaction.guild.id}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"チケット: {self.channel.name}\n")
                f.write(f"サーバー: {interaction.guild.name}\n")
                f.write("="*40 + "\n")
                f.write("\n".join(messages))
            
            await interaction.response.send_message("✅ チケットを閉鎖します。トランスクリプトを保存しました。", ephemeral=True)
            await self.channel.delete()
        except Exception as e:
            await interaction.response.send_message(f"⚠️ 閉鎖中にエラー: {e}", ephemeral=True)

    @discord.ui.button(label="❌ キャンセル", style=discord.ButtonStyle.secondary, custom_id="cancel_close")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("チケット閉鎖をキャンセルしました。", ephemeral=True)


class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_path = TICKET_CONFIG_PATH
        
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2)

    def get_settings(self, guild_id: int) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(str(guild_id), {})

    def save_settings(self, guild_id: int, settings: dict):
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data[str(guild_id)] = settings
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @app_commands.command(name="setup-ticket", description="チケット機能の初期設定を行います（管理者専用）")
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
        guild_id = interaction.guild_id
        settings = {
            "category_id": category.id,
            "support_role_id": support_role.id,
            "log_channel_id": log_channel.id if log_channel else None
        }
        self.save_settings(guild_id, settings)
        
        embed = discord.Embed(
            title="✅ チケット設定完了",
            description=f"カテゴリ: {category.mention}\nサポートロール: {support_role.mention}",
            color=discord.Color.green()
        )
        if log_channel:
            embed.add_field(name="ログチャンネル", value=log_channel.mention)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="send-ticket-panel", description="チケット作成ボタンを送信します（管理者専用）")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(channel="ボタンを表示するチャンネル")
    async def send_ticket_panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        embed = discord.Embed(
            title="🎫 サポートチケット",
            description="何か問題や質問がある場合は、下のボタンを押してチケットを作成してください。\nスタッフがプライベートチャンネルで対応します。",
            color=discord.Color.blue()
        )
        view = TicketView()
        
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ チケットパネルを {channel.mention} に送信しました。", ephemeral=True)

    @app_commands.command(name="sync", description="スラッシュコマンドを手動同期（管理者専用）")
    @app_commands.default_permissions(administrator=True)
    async def sync_commands(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            guild = discord.Object(id=interaction.guild_id)
            await interaction.client.tree.sync(guild=guild)
            await interaction.followup.send("✅ コマンドを同期しました！", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 同期エラー: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))