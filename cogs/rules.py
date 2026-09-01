import json
import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands

class AgreeButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="同意する", style=discord.ButtonStyle.success, custom_id="agree_rules")
    async def agree_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("RulesCog")
        if cog is None:
            await interaction.response.send_message("⚠️ システムエラーが発生しました。", ephemeral=True)
            return

        guild_id = interaction.guild_id
        settings = cog.get_guild_settings(guild_id)
        role_id = settings.get("verified_role")

        if role_id is None:
            await interaction.response.send_message(
                "⚠️ 認証ロールが設定されていません。管理者が `/set-verified-role` で設定してください。",
                ephemeral=True
            )
            return

        role = interaction.guild.get_role(int(role_id))
        if role is None:
            await interaction.response.send_message(
                "⚠️ 設定されたロールが存在しません。管理者に確認してください。",
                ephemeral=True
            )
            return

        try:
            await interaction.user.add_roles(role, reason="ルール同意による認証")
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Botにロールを付与する権限がありません。",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "✅ サーバールールに同意しました。",
            ephemeral=True
        )

        await asyncio.sleep(5)
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass

class RulesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_path = "config/rules_config.json"
        self.guild_settings_path = "config/guild_settings.json"
        
        if not os.path.exists(self.guild_settings_path):
            os.makedirs(os.path.dirname(self.guild_settings_path), exist_ok=True)
            with open(self.guild_settings_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            sample = {
                "title": "サーバールール",
                "description": "このサーバーを利用する際のルールです。",
                "fields": [
                    {"name": "1. 敬語の使用", "value": "メンバー同士で敬語を使用してください。"},
                    {"name": "2. NSFW禁止", "value": "NSFWコンテンツの投稿は禁止です。"}
                ],
                "footer": "ルールは予告なく変更されることがあります。"
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, indent=2, ensure_ascii=False)

        self.config = self.load_config()

    def load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def make_rules_text(self) -> str:
        config = self.config
        lines = []
        lines.append("# " + config["title"])
        lines.append("")
        if config.get("description"):
            lines.append(config["description"])
            lines.append("")
        for field in config.get("fields", []):
            lines.append("## " + field["name"])
            lines.append(field["value"])
            lines.append("")
        if config.get("footer"):
            lines.append("-# " + config["footer"])
        return "\n".join(lines)

    def get_guild_settings(self, guild_id: int) -> dict:
        with open(self.guild_settings_path, "r", encoding="utf-8") as f:
            all_settings = json.load(f)
        return all_settings.get(str(guild_id), {})

    def save_guild_settings(self, guild_id: int, settings: dict):
        with open(self.guild_settings_path, "r", encoding="utf-8") as f:
            all_settings = json.load(f)
        all_settings[str(guild_id)] = settings
        with open(self.guild_settings_path, "w", encoding="utf-8") as f:
            json.dump(all_settings, f, indent=2)

    @app_commands.command(name="accept-rules", description="サーバールールを表示し、同意します。")
    async def accept_rules(self, interaction: discord.Interaction):
        text = self.make_rules_text()
        view = AgreeButtonView()
        await interaction.response.send_message(
            "以下のルールをお読みいただき、同意される場合は「同意する」ボタンを押してください。\n\n" + text,
            view=view
        )

    @app_commands.command(name="set-verified-role", description="ルール同意時に付与するロールを設定します（管理者専用）")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="付与するロールを選択してください")
    async def set_verified_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild_id
        settings = self.get_guild_settings(guild_id)
        settings["verified_role"] = role.id
        self.save_guild_settings(guild_id, settings)
        await interaction.response.send_message(
            f"✅ 認証ロールを {role.mention} に設定しました。",
            ephemeral=True
        )
        
async def setup(bot: commands.Bot):
    await bot.add_cog(RulesCog(bot))