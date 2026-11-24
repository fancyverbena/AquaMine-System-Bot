"""共通 UI ヘルパー: 色、Embed 組み立て、進捗バー、チャンネル名整形。"""
import re

import discord

COLOR_PRIMARY = discord.Color.blurple()
COLOR_SUCCESS = discord.Color.green()
COLOR_WARN = discord.Color.orange()
COLOR_DANGER = discord.Color.red()
COLOR_GOLD = discord.Color.gold()


def progress_bar(fraction: float, width: int = 20) -> str:
    """0.0〜1.0 の進捗を █/░ のバーで表す。"""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return "█" * filled + "░" * (width - filled)


_CHANNEL_INVALID = re.compile(r"[^a-z0-9\-_ぁ-んァ-ヶ一-龠々ー]")


def sanitize_channel_part(name: str, fallback: str = "user", max_length: int = 30) -> str:
    """Discord チャンネル名に使える形に整形する。"""
    cleaned = _CHANNEL_INVALID.sub("-", (name or "").lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length].strip("-_") or fallback


def parse_color(value, fallback: discord.Color = COLOR_PRIMARY) -> discord.Color:
    if isinstance(value, int):
        try:
            return discord.Color(value)
        except (TypeError, ValueError):
            return fallback
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        try:
            return discord.Color(int(text, 16))
        except (TypeError, ValueError):
            return fallback
    return fallback


def base_embed(title: str, description: str = None, color=COLOR_PRIMARY) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    if description:
        embed.description = description
    return embed


async def reply_error(interaction: discord.Interaction, message: str):
    """ephemeral なエラーメッセージを安全に返す。"""
    text = f"⚠️ {message}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass
