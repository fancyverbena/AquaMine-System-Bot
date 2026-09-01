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
    print(f"✅ Bot起動完了: {bot.user} (ID: {bot.user.id})")
    
    from cogs.rules import AgreeButtonView
    bot.add_view(AgreeButtonView())
    
    from cogs.tickets import TicketView, CloseView
    bot.add_view(TicketView())
    bot.add_view(CloseView())
    
    await bot.load_extension("cogs.rules")
    await bot.load_extension("cogs.tickets")
    
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        try:
            await bot.tree.sync(guild=guild)
            print(f"✅ コマンドをギルド {GUILD_ID} に同期しました。")
            
            cmds = await bot.tree.fetch_commands(guild=guild)
            cmd_names = [cmd.name for cmd in cmds]
            print(f"📋 登録済みコマンド一覧: {cmd_names}")
            
        except Exception as e:
            print(f"❌ ギルド同期エラー: {e}")
    else:
        try:
            await bot.tree.sync()
            print("✅ コマンドをグローバルに同期しました。")
            cmds = await bot.tree.fetch_commands()
            cmd_names = [cmd.name for cmd in cmds]
            print(f"📋 登録済みコマンド一覧: {cmd_names}")
        except Exception as e:
            print(f"❌ グローバル同期エラー: {e}")

if __name__ == "__main__":
    bot.run(TOKEN)