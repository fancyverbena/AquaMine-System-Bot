import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN が .env に設定されていません。")

if GUILD_ID:
    try:
        GUILD_ID = int(GUILD_ID)
    except ValueError:
        print("⚠️ GUILD_ID が不正な値です。グローバル同期にフォールバックします。")
        GUILD_ID = None
else:
    print("⚠️ GUILD_ID が設定されていません。グローバル同期になります。")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True


class Bot(commands.Bot):
    async def setup_hook(self):
        from cogs.rules import AgreeButtonView
        from cogs.tickets import TicketView, CloseView

        self.add_view(AgreeButtonView())
        self.add_view(TicketView())
        self.add_view(CloseView())

        await self.load_extension("cogs.rules")
        await self.load_extension("cogs.tickets")
        await self.load_extension("cogs.leveling")

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)

            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)

            print(f"✅ コマンドをギルド {GUILD_ID} に同期しました。")
            print(f"📋 登録コマンド: {[cmd.name for cmd in synced]}")

            cmds = await self.tree.fetch_commands(guild=guild)
            print(f"📋 Discord側の登録コマンド: {[cmd.name for cmd in cmds]}")
        else:
            synced = await self.tree.sync()

            print("✅ コマンドをグローバルに同期しました。")
            print(f"📋 登録コマンド: {[cmd.name for cmd in synced]}")

            cmds = await self.tree.fetch_commands()
            print(f"📋 Discord側の登録コマンド: {[cmd.name for cmd in cmds]}")


bot = Bot(
    command_prefix="/",
    intents=intents
)


@bot.event
async def on_ready():
    print(f"✅ Bot起動完了: {bot.user} (ID: {bot.user.id})")


if __name__ == "__main__":
    bot.run(TOKEN)