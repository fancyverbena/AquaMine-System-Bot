import os
import sys
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
    print("🔵 on_ready が呼び出されました！", flush=True)
    print(f"✅ Bot起動完了: {bot.user} (ID: {bot.user.id})", flush=True)
    
    try:
        from cogs.rules import AgreeButtonView
        bot.add_view(AgreeButtonView())
        print("✅ AgreeButtonView 登録完了", flush=True)
    except Exception as e:
        print(f"❌ AgreeButtonView 登録エラー: {e}", flush=True)
    
    try:
        from cogs.tickets import TicketView, CloseView
        bot.add_view(TicketView())
        bot.add_view(CloseView())
        print("✅ TicketView / CloseView 登録完了", flush=True)
    except Exception as e:
        print(f"❌ TicketView 登録エラー: {e}", flush=True)
    
    try:
        await bot.load_extension("cogs.rules")
        print("✅ rules Cog 読み込み成功", flush=True)
    except Exception as e:
        print(f"❌ rules Cog 読み込み失敗: {e}", flush=True)
    
    try:
        await bot.load_extension("cogs.tickets")
        print("✅ tickets Cog 読み込み成功", flush=True)
    except Exception as e:
        print(f"❌ tickets Cog 読み込み失敗: {e}", flush=True)
    
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        try:
            await bot.tree.sync(guild=guild)
            print(f"✅ コマンドをギルド {GUILD_ID} に同期しました（即時反映）。", flush=True)
        except Exception as e:
            print(f"❌ ギルド同期エラー: {e}", flush=True)
    else:
        try:
            await bot.tree.sync()
            print("✅ コマンドをグローバルに同期しました（反映に最大1時間）。", flush=True)
        except Exception as e:
            print(f"❌ グローバル同期エラー: {e}", flush=True)

if __name__ == "__main__":
    print("🚀 ボットを起動します...", flush=True)
    bot.run(TOKEN)