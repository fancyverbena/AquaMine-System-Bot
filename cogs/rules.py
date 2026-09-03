import json
import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands


class AgreeButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="同意する",
        style=discord.ButtonStyle.success,
        custom_id="agree_rules"
    )
    async def agree_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        cog = interaction.client.get_cog("RulesCog")

        if cog is None:
            await interaction.response.send_message(
                "⚠️ システムエラーが発生しました。",
                ephemeral=True
            )
            return

        guild_id = interaction.guild_id

        if guild_id is None:
            await interaction.response.send_message(
                "⚠️ この操作はサーバー内でのみ使用できます。",
                ephemeral=True
            )
            return

        settings = cog.get_guild_settings(guild_id)
        role_id = settings.get("verified_role")

        if role_id is None:
            await interaction.response.send_message(
                "⚠️ 認証ロールが設定されていません。"
                "管理者が `/set-verified-role` で設定してください。",
                ephemeral=True
            )
            return

        role = interaction.guild.get_role(int(role_id))

        if role is None:
            await interaction.response.send_message(
                "⚠️ 設定されたロールが存在しません。"
                "管理者に確認してください。",
                ephemeral=True
            )
            return

        try:
            await interaction.user.add_roles(
                role,
                reason="ルール同意による認証"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Botにロールを付与する権限がありません。",
                ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "⚠️ ロール付与中にエラーが発生しました。",
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

        os.makedirs("config", exist_ok=True)

        if not os.path.exists(self.guild_settings_path):
            self.write_json(
                self.guild_settings_path,
                {}
            )

        if not os.path.exists(self.config_path):
            sample = {
                "title": "サーバールール",
                "description": "このサーバーを利用する際のルールです。",
                "fields": [
                    {
                        "name": "1. 敬語の使用",
                        "value": "メンバー同士で敬語を使用してください。"
                    },
                    {
                        "name": "2. NSFW禁止",
                        "value": "NSFWコンテンツの投稿は禁止です。"
                    }
                ],
                "footer": "ルールは予告なく変更されることがあります。"
            }

            self.write_json(
                self.config_path,
                sample
            )

        self.config = self.load_config()

    def read_json(self, path: str, default):
        try:
            if not os.path.exists(path):
                self.write_json(path, default)
                return default

            with open(
                path,
                "r",
                encoding="utf-8"
            ) as f:
                content = f.read().strip()

            if not content:
                self.write_json(path, default)
                return default

            data = json.loads(content)

            if not isinstance(data, type(default)):
                self.write_json(path, default)
                return default

            return data

        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError
        ):
            self.write_json(path, default)
            return default

    def write_json(self, path: str, data):
        os.makedirs(
            os.path.dirname(path),
            exist_ok=True
        )

        temp_path = f"{path}.tmp"

        with open(
            temp_path,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            temp_path,
            path
        )

    def load_config(self) -> dict:
        default = {
            "title": "サーバールール",
            "description": "このサーバーを利用する際のルールです。",
            "fields": [],
            "footer": ""
        }

        return self.read_json(
            self.config_path,
            default
        )

    def make_rules_text(self) -> str:
        config = self.config

        lines = []

        lines.append(
            "# " + config.get(
                "title",
                "サーバールール"
            )
        )

        lines.append("")

        if config.get("description"):
            lines.append(
                config["description"]
            )
            lines.append("")

        for field in config.get("fields", []):
            lines.append(
                "## " + field.get("name", "")
            )
            lines.append(
                field.get("value", "")
            )
            lines.append("")

        if config.get("footer"):
            lines.append(
                "-# " + config["footer"]
            )

        return "\n".join(lines)

    def get_guild_settings(self, guild_id: int) -> dict:
        all_settings = self.read_json(
            self.guild_settings_path,
            {}
        )

        return all_settings.get(
            str(guild_id),
            {}
        )

    def save_guild_settings(
        self,
        guild_id: int,
        settings: dict
    ):
        all_settings = self.read_json(
            self.guild_settings_path,
            {}
        )

        all_settings[str(guild_id)] = settings

        self.write_json(
            self.guild_settings_path,
            all_settings
        )

    @app_commands.command(
        name="accept-rules",
        description="サーバールールを表示し、同意します。"
    )
    async def accept_rules(
        self,
        interaction: discord.Interaction
    ):
        text = self.make_rules_text()
        view = AgreeButtonView()

        await interaction.response.send_message(
            "以下のルールをお読みいただき、"
            "同意される場合は「同意する」ボタンを押してください。\n\n"
            + text,
            view=view
        )

    @app_commands.command(
        name="set-verified-role",
        description="ルール同意時に付与するロールを設定します（管理者専用）"
    )
    @app_commands.default_permissions(
        administrator=True
    )
    @app_commands.describe(
        role="付与するロールを選択してください"
    )
    async def set_verified_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role
    ):
        guild_id = interaction.guild_id

        if guild_id is None:
            await interaction.response.send_message(
                "⚠️ このコマンドはサーバー内でのみ使用できます。",
                ephemeral=True
            )
            return

        settings = self.get_guild_settings(
            guild_id
        )

        settings["verified_role"] = role.id

        try:
            self.save_guild_settings(
                guild_id,
                settings
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ 設定の保存に失敗しました。\n"
                f"`{type(e).__name__}: {e}`",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ 認証ロールを {role.mention} に設定しました。",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        RulesCog(bot)
    )