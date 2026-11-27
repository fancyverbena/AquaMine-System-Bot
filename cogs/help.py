"""/help — 権限に応じて見える案内を変える。"""
import discord
from discord import app_commands
from discord.ext import commands

from app import checks
from app.ui import COLOR_PRIMARY


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="このBotの使い方を表示します")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="❓ AquaMine Bot ヘルプ",
            color=COLOR_PRIMARY,
        )
        embed.add_field(
            name="👥 メンバー",
            value=(
                "**ルール同意**: 認証パネルの「同意する」ボタンを押すと認証ロールが付与されます。\n"
                "**/rank**: 自分のレベル・順位・次のレベルまでの進捗を表示します。\n"
                "**/leaderboard**: サーバーのXPランキングを表示します（発言でXPが貯まります）。\n"
                "**/weekly ranking**: 今週のXP増加量ランキング（途中経過）を表示します。\n"
                "**チケット**: チケットパネルで種類を選ぶと、件名と詳細を入力して"
                "スタッフ専用の相談チャンネルを作れます。"
            ),
            inline=False,
        )

        guild = interaction.guild
        if guild is not None and checks.is_support_or_admin(interaction, guild.id):
            embed.add_field(
                name="🛠️ スタッフ（サポート/管理者）",
                value=(
                    "チケット内の **「担当する」** ボタン、または **/ticket assign** で担当を設定。\n"
                    "**/ticket add / /ticket remove**: メンバーのチケットへの出入りを管理。\n"
                    "チケット内の **「閉じる」** ボタンで、記録を残して閉鎖できます。"
                ),
                inline=False,
            )

        if checks.is_admin(interaction):
            embed.add_field(
                name="⚙️ 管理者",
                value=(
                    "**/setup**: 設定パネル（ロール/チャンネルの選択、パネル設置、自動クローズ設定）。\n"
                    "**/settings**: 現在の設定の一覧。\n"
                    "**/level set-role / remove-role**: レベル到達で付与するロール（種別で発言/VCを個別に設定）。\n"
                    "**/level give-xp / reset**: メンバーのXP調整とリセット。"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
