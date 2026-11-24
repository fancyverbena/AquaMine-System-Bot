"""権限チェックの共通ヘルパー。"""
import discord

from . import settings


def is_admin(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return (
        isinstance(user, discord.Member)
        and user.guild_permissions.administrator
    )


def is_support_or_admin(interaction: discord.Interaction, guild_id: int) -> bool:
    if is_admin(interaction):
        return True
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    support_role_id = settings.get(guild_id, settings.SUPPORT_ROLE_ID)
    if not support_role_id:
        return False
    return any(role.id == int(support_role_id) for role in user.roles)


def member_is_support(member: discord.Member, guild_id: int) -> bool:
    if member.guild_permissions.administrator:
        return True
    support_role_id = settings.get(guild_id, settings.SUPPORT_ROLE_ID)
    if not support_role_id:
        return False
    return any(role.id == int(support_role_id) for role in member.roles)
