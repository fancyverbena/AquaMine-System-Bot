import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN が .env に設定されていません。")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot起動完了: {bot.user} (ID: {bot.user.id})")
    from cogs.rules import AgreeButtonView
    bot.add_view(AgreeButtonView())
    await bot.load_extension("cogs.rules")
    print("✅ ルール認証 Cog を読み込みました。")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.send(f"⚠️ エラーが発生しました: {error}")

# 起動
if __name__ == "__main__":
    bot.run(TOKEN)