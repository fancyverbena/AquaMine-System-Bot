import os
import discord
from discord.ext import commands
from discord import app_commands

import jsonstore

DEFAULT_CONFIG = {
    "title": "サーバールール",
    "description": "このサーバーを利用する際のルールです。",
    "color": "#3498db",
    "fields": [],
    "footer": ""
}

SAMPLE_CONFIG = {
    "title": "サーバールール",
    "description": "このサーバーを利用する際のルールです。",
    "color": "#3498db",
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

# Embedの制限 (フィールド値1024文字 / 1つのEmbedは25フィールドまで /
# 全体の文字数6000文字まで) に収まるよう分割するための上限
MAX_FIELD_VALUE = 1024
MAX_FIELDS_PER_EMBED = 25
MAX_EMBED_TOTAL = 6000


def parse_color(value) -> discord.Color:
    """設定ファイルの color を discord.Color に変換する。

    "#3498db" / "3498db" / 数値 に対応し、不正な値はデフォルト色に
    フォールバックする。
    """
    try:
        if isinstance(value, int):
            return discord.Color(value)

        if isinstance(value, str):
            text = value.strip()

            if text.startswith("#"):
                text = text[1:]

            return discord.Color(int(text, 16))

    except (ValueError, TypeError):
        pass

    return discord.Color.blurple()


def split_field_value(value: str):
    """1024文字を超えるフィールド値を複数に分割する。"""
    if len(value) <= MAX_FIELD_VALUE:
        return [value]

    chunks = []

    rest = value

    while len(rest) > MAX_FIELD_VALUE:
        cut = rest.rfind("\n", 0, MAX_FIELD_VALUE)

        if cut <= 0:
            cut = MAX_FIELD_VALUE

        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")

    if rest:
        chunks.append(rest)

    return chunks


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

        if isinstance(interaction.user, discord.Member) \
                and role in interaction.user.roles:
            await interaction.response.send_message(
                "✅ すでに認証済みです。",
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


class RulesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.config_path = "config/rules_config.json"
        self.guild_settings_path = "config/guild_settings.json"

        os.makedirs("config", exist_ok=True)

        if not os.path.exists(self.guild_settings_path):
            jsonstore.write_json(self.guild_settings_path, {})

        if not os.path.exists(self.config_path):
            jsonstore.write_json(self.config_path, SAMPLE_CONFIG)

        self.config = self.load_config()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = (
                "⏳ コマンドのクールダウン中です。"
                f"あと {error.retry_after:.0f} 秒待ってから"
                "もう一度実行してください。"
            )
        else:
            message = (
                "⚠️ コマンドの実行中にエラーが発生しました。\n"
                f"`{type(error).__name__}: {error}`"
            )

        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )

    def load_config(self) -> dict:
        return jsonstore.read_json(
            self.config_path,
            DEFAULT_CONFIG
        )

    def make_rules_embeds(self):
        """現在の設定からルール表示用のEmbedリストを作る。

        Embedの制限を超える場合は複数のEmbedに分割する。
        """
        config = self.config

        color = parse_color(config.get("color"))

        title = config.get("title", "サーバールール")
        description = config.get("description") or None

        embeds = []
        current = discord.Embed(
            title=title,
            description=description,
            color=color
        )
        current_total = len(title) + len(description or "")

        def flush():
            nonlocal current, current_total

            embeds.append(current)
            current = discord.Embed(color=color)
            current_total = 0

        for field in config.get("fields", []):
            name = field.get("name", "")
            value = field.get("value", "")

            chunks = split_field_value(value)

            for index, chunk in enumerate(chunks):
                chunk_name = (
                    name
                    if index == 0
                    else f"{name}（続き）"
                )

                needed = len(chunk_name) + len(chunk)

                if (
                    len(current.fields) >= MAX_FIELDS_PER_EMBED
                    or current_total + needed > MAX_EMBED_TOTAL
                ):
                    flush()

                current.add_field(
                    name=chunk_name,
                    value=chunk,
                    inline=False
                )
                current_total += needed

        if current.fields or not embeds:
            embeds.append(current)

        footer = config.get("footer")

        if footer:
            embeds[-1].set_footer(text=footer)

        return embeds

    def get_guild_settings(self, guild_id: int) -> dict:
        all_settings = jsonstore.read_json(
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
        def mutator(all_settings):
            all_settings[str(guild_id)] = settings
            return all_settings

        jsonstore.update_json(
            self.guild_settings_path,
            {},
            mutator
        )

    @app_commands.command(
        name="accept-rules",
        description="サーバールールを表示し、同意します。"
    )
    @app_commands.checks.cooldown(1, 30.0)
    async def accept_rules(
        self,
        interaction: discord.Interaction
    ):
        # 設定ファイルの編集を再起動なしで反映するため毎回読み直す
        self.config = self.load_config()

        embeds = self.make_rules_embeds()
        view = AgreeButtonView()

        await interaction.response.send_message(
            embeds=embeds,
            view=view
        )

    @app_commands.command(
        name="set-verified-role",
        description="ルール同意時に付与するロールを設定します（管理者専用）"
    )
    @app_commands.default_permissions(
        administrator=True
    )
    @app_commands.checks.has_permissions(
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
