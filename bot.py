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
    print("⚠️ GUILD_ID が設定されていません。グローバル同期になります（反映に時間がかかります）。")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot起動完了: {bot.user}")
    
    from cogs.rules import AgreeButtonView
    bot.add_view(AgreeButtonView())

    from cogs.tickets import TicketView, CloseView, ConfirmCloseView
    bot.add_view(TicketView())
    bot.add_view(CloseView())
    bot.add_view(ConfirmCloseView(None))
    
    await bot.load_extension("cogs.rules")
    await bot.load_extension("cogs.tickets")
    
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=guild)
        print(f"✅ コマンドをギルド {GUILD_ID} に同期しました。")

if __name__ == "__main__":
    bot.run(TOKEN)