import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True


class AquaMineBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.synced_guilds = set()

    async def setup_hook(self):
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py") and filename != "__init__.py":
                await self.load_extension(f"cogs.{filename[:-3]}")
        print("✅ Cogを読み込みました。")


bot = AquaMineBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ {bot.user} としてログインしました。")
    print(f"現在参加しているサーバー数: {len(bot.guilds)}")
    for guild in bot.guilds:
        print(f" - {guild.name} (ID: {guild.id})")
        if guild.id in bot.synced_guilds:
            continue

        try:
            await bot.tree.sync(guild=guild)
            bot.synced_guilds.add(guild.id)
            print(f"✅ {guild.name} にコマンドを同期しました。")
        except discord.HTTPException as e:
            print(f"❌ {guild.name} への同期エラー: {e}")


@bot.event
async def on_guild_join(guild):
    if guild.id in bot.synced_guilds:
        return

    try:
        await bot.tree.sync(guild=guild)
        bot.synced_guilds.add(guild.id)
        print(f"✅ 新規サーバー {guild.name} にコマンドを同期しました。")
    except Exception as e:
        print(f"❌ {guild.name} への同期エラー: {e}")


@bot.command(name="sync")
async def sync_commands(ctx):
    if ctx.guild is None:
        await ctx.send("❌ このコマンドはサーバー内で実行してください。")
        return

    await ctx.send("同期を開始します...")
    try:
        await bot.tree.sync(guild=ctx.guild)
        bot.synced_guilds.add(ctx.guild.id)
        await ctx.send("✅ このサーバーにコマンドを同期しました。")
    except Exception as e:
        await ctx.send(f"❌ 同期エラー: {e}")


if __name__ == "__main__":
    if TOKEN is None:
        print("❌ 環境変数 DISCORD_TOKEN が設定されていません。")
    else:
        bot.run(TOKEN)