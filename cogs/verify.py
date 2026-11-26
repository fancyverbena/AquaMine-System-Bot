"""ルール同意による認証 (verify)。

ルール本文は config/rules.json を表示のたびに読み直す (再起動不要で編集反映)。
"""
import datetime
import json
import os

import discord
from discord.ext import commands

from app import settings
from app.config import RULES_PATH
from app.ui import reply_error

DEFAULT_RULES = {
    "title": "📜 サーバールール",
    "description": "このサーバーを利用する際のルールです。同意すると認証ロールが付与されます。",
    "color": "#3498db",
    "fields": [],
    "footer": "ルールは予告なく変更されることがあります。",
}


def load_rules() -> dict:
    if not os.path.exists(RULES_PATH):
        return dict(DEFAULT_RULES)
    try:
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_RULES)
        merged = dict(DEFAULT_RULES)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return dict(DEFAULT_RULES)


def build_rules_text() -> str:
    """ルールを通常メッセージ (Markdownテキスト) として組み立てる。"""
    rules = load_rules()
    lines = []

    lines.append("# " + str(rules.get("title") or "📜 サーバールール"))
    lines.append("")

    if rules.get("description"):
        lines.append(str(rules["description"]))
        lines.append("")

    for field in rules.get("fields", []):
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", ""))
        value = str(field.get("value", ""))
        if name:
            lines.append("## " + name)
        if value:
            lines.append(value)
        lines.append("")

    if rules.get("footer"):
        lines.append("-# " + str(rules["footer"]))

    return "\n".join(lines).strip()


def split_message(text: str, limit: int = 2000) -> list:
    """Discord の文字数上限に収まるよう、行境界で分割する。"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""

    for line in text.split("\n"):
        # 1行だけで上限を超える場合は強制的に割る
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]

        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def build_rules_panel():
    """(text_chunks, view) を返す。/setup のパネル設置からも使う。

    同意ボタンは最後のチャンクに付ける。
    """
    return split_message(build_rules_text()), AgreeView()


class AgreeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="同意する",
        style=discord.ButtonStyle.success,
        custom_id="v2:agree",
        emoji="✅",
    )
    async def agree(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
            return

        role_id = settings.get(guild.id, settings.VERIFIED_ROLE_ID)
        if not role_id:
            await reply_error(
                interaction,
                "認証ロールがまだ設定されていません。管理者に `/setup` での設定をお願いしてください。",
            )
            return

        role = guild.get_role(int(role_id))
        if role is None:
            await reply_error(
                interaction,
                "設定された認証ロールが見つかりません。管理者に確認してください。",
            )
            return

        if role in member.roles:
            await interaction.response.send_message(
                "✅ すでに認証済みです。", ephemeral=True
            )
            return

        try:
            await member.add_roles(role, reason="ルール同意による認証 (v2)")
        except discord.Forbidden:
            await reply_error(
                interaction, "Botにロールを付与する権限がありません。管理者に確認してください。"
            )
            return
        except discord.HTTPException:
            await reply_error(interaction, "ロール付与中にエラーが発生しました。")
            return

        await interaction.response.send_message(
            f"✅ サーバールールに同意しました。{role.mention} を付与しました。",
            ephemeral=True,
        )

        log_id = settings.get(guild.id, settings.LOG_CHANNEL_ID)
        if log_id:
            log_channel = guild.get_channel(int(log_id))
            if isinstance(log_channel, discord.TextChannel):
                now = datetime.datetime.now(datetime.timezone.utc)
                try:
                    await log_channel.send(
                        f"✅ 認証ログ: {member.mention} がルールに同意し、"
                        f"{role.mention} が付与されました。"
                        f"（{now.strftime('%Y-%m-%d %H:%M UTC')}）"
                    )
                except discord.HTTPException:
                    pass


class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))
