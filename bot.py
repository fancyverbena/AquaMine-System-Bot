import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} としてログインしました。")

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

if TOKEN is None:
    print("❌ 環境変数 DISCORD_TOKEN が設定されていません。")
else:
    bot.run(TOKEN)