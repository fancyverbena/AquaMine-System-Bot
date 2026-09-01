import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

synced_guilds = set()


async def setup_hook():
    # Cogを読み込む
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and filename != "__init__.py":
            await bot.load_extension(f"cogs.{filename[:-3]}")
    print("✅ Cogを読み込みました。")


@bot.event
async def on_ready():
    print(f"✅ {bot.user} としてログインしました。")
    for guild in bot.guilds:
        if guild.id in synced_guilds:
            continue
        try:
            await bot.tree.sync(guild=guild)
            synced_guilds.add(guild.id)
            print(f"✅ {guild.name} にコマンドを同期しました。")
        except discord.HTTPException as e:
            print(f"❌ {guild.name} への同期エラー: {e}")


@bot.event
async def on_guild_join(guild):
    try:
        await bot.tree.sync(guild=guild)
        synced_guilds.add(guild.id)
        print(f"✅ 新規サーバー {guild.name} にコマンドを同期しました。")
    except Exception as e:
        print(f"❌ {guild.name} への同期エラー: {e}")


@bot.command(name="sync")
async def sync_commands(ctx):
    await ctx.send("同期を開始します...")
    try:
        await bot.tree.sync(guild=ctx.guild)
        synced_guilds.add(ctx.guild.id)
        await ctx.send("✅ このサーバーにコマンドを同期しました。")
    except Exception as e:
        await ctx.send(f"❌ 同期エラー: {e}")


bot.setup_hook = setup_hook


if __name__ == "__main__":
    if TOKEN is None:
        print("❌ 環境変数 DISCORD_TOKEN が設定されていません。")
    else:
        bot.run(TOKEN)