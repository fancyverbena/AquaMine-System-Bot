import json
import discord
from discord.ext import commands
from discord import app_commands


class RulesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = self.load_config()

    def load_config(self) -> dict:
        with open("config/rules_config.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def make_rules_embed(self) -> discord.Embed:
        config = self.config
        embed = discord.Embed(
            title=config["title"],
            description=config.get("description", ""),
            color=discord.Color.from_str(config.get("color", "#3498db"))
        )
        for field in config.get("fields", []):
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=False
            )
        if "footer" in config:
            embed.set_footer(text=config["footer"])
        return embed

    class AgreeButtonView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="同意する", style=discord.ButtonStyle.success, custom_id="agree_rules")
        async def agree_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(
                f"✅ **{interaction.user.mention} さんがサーバールールに同意しました。**"
            )

    @app_commands.command(name="accept-rules", description="サーバールールを表示し、同意します。")
    async def accept_rules(self, interaction: discord.Interaction):
        embed = self.make_rules_embed()
        view = self.AgreeButtonView(self)
        await interaction.response.send_message(
            "以下のルールをお読みいただき、同意される場合は「同意する」ボタンを押してください。",
            embed=embed,
            view=view
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RulesCog(bot))