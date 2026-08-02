import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def setup_hook():
    bot.tree.clear_commands(guild=None)
    for guild in bot.guilds:
        bot.tree.clear_commands(guild=guild)

    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and filename != "__init__.py":
            await bot.load_extension(f"cogs.{filename[:-3]}")

    await bot.tree.sync()
    print("✅ スラッシュコマンドをクリーン同期しました。")


bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    print(f"✅ {bot.user} としてログインしました。")


if __name__ == "__main__":
    if TOKEN is None:
        print("❌ 環境変数 DISCORD_TOKEN が設定されていません。")
    else:
        bot.run(TOKEN)