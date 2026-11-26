"""DISBOARD の Bump リマインダー。

DISBOARD (Bot ユーザー ID: 302050872383242240) の /bump が成功すると、
次の bump ができるようになるのは2時間後。DISBOARD の成功メッセージを
検知したら、その時刻を DB に予約しておき、2時間後に同じチャンネルへ
「/bump できる時間になったよ」と通知する。

永続化の方式:
  予約は DB (bump_reminders) に1ギルド1件だけ保存する。新しく bump
  されたら予約は上書き (リセット) される。通知は discord.ext.tasks の
  ループ (60秒間隔) が期限の来た予約を処理する形で、Bot が落ちていた
  間に時刻を迎えた予約も再起動直後の最初のループで通知される。

検知について:
  成功判定は DISBOARD の Embed の説明文に成功フレーズが含まれるかで
  行う。DISBOARD 側の文言が変わると検知できなくなるため、フレーズは
  SUCCESS_MARKERS に集約してある (追加・変更はここだけ直せばよい)。
  他 Bot の Embed を読むには MESSAGE CONTENT INTENT が必要 (この Bot
  では有効化が必須になっている)。
"""
import datetime
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app import settings
from app.db import db
from app.ui import reply_error

DISBOARD_BOT_ID = 302050872383242240

# bump のクールダウン (DISBOARD の仕様で2時間)
BUMP_INTERVAL_SECONDS = 2 * 3600
# /bump test で使う遅延 (1分)
TEST_DELAY_SECONDS = 60

# DISBOARD の成功メッセージに含まれるフレーズ (部分一致、小文字化して比較)。
# 日本語の成功文と英語の成功文の両方に対応する。文言が変わったらここを直す。
SUCCESS_MARKERS = (
    "表示順をアップ",
    "サーバーの表示順",
    "bump done",
    "bumped the server",
)

_MENTION_RE = re.compile(r"<@!?([0-9]+)>")


def is_bump_success(author_id: int, embed_descriptions) -> bool:
    """DISBOARD の bump 成功メッセージかどうかを判定する純粋関数。

    投稿者が DISBOARD 本人で、Embed の説明文のどれかに成功フレーズが
    含まれていれば True。embed_descriptions は説明文 (str) の列で、
    None や空文字が混ざっていてもよい。
    """
    if author_id != DISBOARD_BOT_ID:
        return False
    for description in embed_descriptions or []:
        if not description:
            continue
        text = str(description).casefold()
        if any(marker.casefold() in text for marker in SUCCESS_MARKERS):
            return True
    return False


def extract_bumper_id(text):
    """テキスト中の最初のユーザーメンションから ID を取り出す。

    見つからなければ None。DISBOARD の成功文には bump した人の
    メンションが含まれることがあるので、通知先の手がかりに使う
    (取れなくても動作に支障はない)。
    """
    if not text:
        return None
    match = _MENTION_RE.search(str(text))
    if match is None:
        return None
    return int(match.group(1))


def _format_dt(timestamp: float, style: str) -> str:
    dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    return discord.utils.format_dt(dt, style)


class BumpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    # ---------- 検知 ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if message.author.id != DISBOARD_BOT_ID:
            return
        if not settings.get_bump_reminder_enabled(message.guild.id):
            return
        descriptions = [
            embed.description for embed in message.embeds if embed.description
        ]
        if not is_bump_success(message.author.id, descriptions):
            return
        bumper_id = None
        for description in descriptions:
            bumper_id = extract_bumper_id(description)
            if bumper_id is not None:
                break
        now = time.time()
        db.set_bump_reminder(
            message.guild.id,
            message.channel.id,
            bumper_id,
            now + BUMP_INTERVAL_SECONDS,
        )

    # ---------- 通知 (ループ) ----------

    @tasks.loop(seconds=60)
    async def reminder_loop(self):
        now = time.time()
        for row in db.get_due_bump_reminders(now):
            try:
                await self._fire_reminder(row)
            except Exception as error:  # 1件の失敗でループ自体を止めない
                print(
                    f"⚠️ ギルド {row['guild_id']} の Bump リマインダー"
                    f"通知に失敗: {error}"
                )

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    async def _fetch_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _fire_reminder(self, row):
        """予約を1件通知し、必ず予約を消す (チャンネル消失時も)。"""
        guild_id = row["guild_id"]
        try:
            channel = await self._fetch_channel(row["channel_id"])
            if channel is None:
                return
            role_id = settings.get_bump_notify_role_id(guild_id)
            if role_id:
                mention = f"<@&{role_id}> "
            elif row.get("bumper_id"):
                mention = f"<@{row['bumper_id']}> "
            else:
                mention = ""
            try:
                await channel.send(
                    f"⏰ {mention}/bump できる時間になったよ！",
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=True, roles=True
                    ),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        finally:
            db.clear_bump_reminder(guild_id)

    # ---------- /bump グループ (管理者) ----------

    bump_group = app_commands.Group(
        name="bump",
        description="Bumpリマインダー管理（管理者専用）",
        default_permissions=discord.Permissions(administrator=True),
    )

    def _guard_admin_guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        member = interaction.user
        return (
            isinstance(member, discord.Member)
            and member.guild_permissions.administrator
        )

    async def _reject(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
        else:
            await reply_error(interaction, "このコマンドは管理者専用です。")

    @bump_group.command(
        name="toggle", description="Bumpリマインダーの有効/無効を切り替えます"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def bump_toggle(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        new_value = not settings.get_bump_reminder_enabled(gid)
        settings.set_value(gid, settings.BUMP_REMINDER_ENABLED, new_value)
        await interaction.response.send_message(
            "✅ Bumpリマインダーを "
            + ("**有効** にしました。" if new_value else "**無効** にしました。"),
            ephemeral=True,
        )

    @bump_group.command(
        name="role",
        description="リマインダーでメンションするロールを設定します（省略すると解除）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(role="メンションするロール（省略すると解除）")
    async def bump_role(
        self, interaction: discord.Interaction, role: discord.Role = None
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        if role is None:
            settings.set_value(gid, settings.BUMP_NOTIFY_ROLE_ID, 0)
            await interaction.response.send_message(
                "✅ 通知ロールを解除しました"
                "（通知時は bump した人にメンションします）。",
                ephemeral=True,
            )
            return
        settings.set_value(gid, settings.BUMP_NOTIFY_ROLE_ID, role.id)
        await interaction.response.send_message(
            f"✅ 通知ロールを {role.mention} に設定しました。",
            ephemeral=True,
        )

    @bump_group.command(
        name="status", description="Bumpリマインダーの現在の設定と予約を表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def bump_status(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        guild = interaction.guild
        role_id = settings.get_bump_notify_role_id(gid)
        role = guild.get_role(role_id) if role_id else None
        lines = [
            "状態: "
            + (
                "**有効**"
                if settings.get_bump_reminder_enabled(gid)
                else "**無効**"
            ),
            "通知ロール: " + (role.mention if role else "未設定"),
        ]
        row = db.get_bump_reminder(gid)
        if row is None:
            lines.append("予約中のリマインダー: なし")
        else:
            lines.append(
                "予約中のリマインダー: "
                f"<#{row['channel_id']}> に "
                f"{_format_dt(row['remind_at'], 'R')}"
                f"（{_format_dt(row['remind_at'], 'F')}）"
            )
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )

    @bump_group.command(
        name="test",
        description="テスト用に1分後のリマインダーをこのチャンネルに予約します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def bump_test(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        channel = interaction.channel
        if channel is None:
            await reply_error(interaction, "チャンネルが取得できませんでした。")
            return
        now = time.time()
        db.set_bump_reminder(
            interaction.guild.id,
            channel.id,
            interaction.user.id,
            now + TEST_DELAY_SECONDS,
        )
        await interaction.response.send_message(
            "✅ テスト用リマインダーを予約しました。"
            "約1分後にこのチャンネルへ通知が届きます。\n"
            "（予約中のリマインダーがあった場合は上書きされます）",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BumpCog(bot))
