"""AquaMine System Bot v2 — エントリーポイント。

構成:
  app/   共通基盤 (設定, DB, UI, 権限チェック)
  cogs/  機能ごと (admin, verify, tickets, leveling, automod, roles,
         giveaway, feedback, help)
データはすべて data/bot.db (SQLite) に保存される。
"""
import discord
from discord import app_commands
from discord.ext import commands

from app import config


class Bot(commands.Bot):
    async def setup_hook(self):
        from cogs.feedback import FeedbackStatusButton, FeedbackVoteButton
        from cogs.giveaway import GiveawayButton
        from cogs.roles import RoleButton
        from cogs.tickets import TicketControlsView, TicketPanelView
        from cogs.verify import AgreeView

        # 永続 View (再起動後も既存パネルのボタン/セレクトが効くように)
        self.add_view(AgreeView())
        self.add_view(TicketPanelView())
        self.add_view(TicketControlsView())
        # ロールパネル・抽選・フィードバックのボタンは
        # custom_id に ID を埋め込む動的アイテム
        self.add_dynamic_items(RoleButton)
        self.add_dynamic_items(GiveawayButton)
        self.add_dynamic_items(FeedbackVoteButton)
        self.add_dynamic_items(FeedbackStatusButton)

        for extension in (
            "cogs.admin",
            "cogs.verify",
            "cogs.tickets",
            "cogs.leveling",
            "cogs.automod",
            "cogs.roles",
            "cogs.giveaway",
            "cogs.feedback",
            "cogs.help",
        ):
            await self.load_extension(extension)

        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"✅ コマンドをギルド {config.GUILD_ID} に同期しました。")
        else:
            synced = await self.tree.sync()
            print("✅ コマンドをグローバルに同期しました。")
        print(f"📋 登録コマンド: {[cmd.name for cmd in synced]}")


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
# 発言内容の判定 (自動モデレーション / レベリングの重複判定) に必須。
# Developer Portal で MESSAGE CONTENT INTENT を有効化していないとログインできない。
intents.message_content = True

bot = Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Bot起動完了: {bot.user} (ID: {bot.user.id})")


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    """アプリコマンド共通のエラーハンドラ。"""
    async def notify(message: str):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    if isinstance(error, app_commands.CommandOnCooldown):
        await notify(
            f"⏳ クールダウン中です。あと {error.retry_after:.0f} 秒待ってください。"
        )
    elif isinstance(error, app_commands.MissingPermissions):
        await notify("⚠️ このコマンドを実行する権限がありません。")
    elif isinstance(error, app_commands.CheckFailure):
        await notify("⚠️ このコマンドはここでは使えないか、権限がありません。")
    else:
        print(f"⚠️ アプリコマンドで予期しないエラー: {type(error).__name__}: {error}")
        await notify("⚠️ エラーが発生しました。時間をおいて再度お試しください。")


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise ValueError("DISCORD_TOKEN が .env に設定されていません。")
    bot.run(config.DISCORD_TOKEN)
