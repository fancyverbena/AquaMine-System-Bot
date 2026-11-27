"""DB の自動バックアップ。

Bot の全データ (設定・XP・チケットなど) は単一の SQLite ファイル
(data/bot.db) に集約されている。そのスナップショットを設定した
チャンネルへ定期的に投稿しておくことで、ホストやコンテナの障害で
ファイルごと失われても、投稿済みの .db から復旧できるようにする。

スナップショットは sqlite3 の backup API (Database.backup_to) で
作るため、Bot が稼働中でも一貫したコピーが得られる。

自動投稿は discord.ext.tasks のループ (30分間隔) が担当する。
最終実施時刻はギルド設定 (backup_last_at) に残るため、再起動を
挟んでも間隔は維持される。投稿先チャンネルが未設定のギルドや、
間隔が 0 (自動オフ) のギルドは対象外。
"""
import datetime
import os
import tempfile
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app import settings
from app.db import db
from app.ui import reply_error

# Discord のアップロード上限 (約25MB) に余裕を持たせた上限。
# これを超えるスナップショットはアップロードせず警告だけを投稿する。
MAX_UPLOAD_BYTES = 24 * 1024 * 1024


def is_due(last_backup_at, now: float, interval_hours: int) -> bool:
    """自動バックアップの実施タイミングかどうかを判定する純粋関数。

    - 間隔が 0 以下なら自動投稿はオフ (常に False)
    - 未実施 (None / 0) なら実施する
    - 前回から間隔以上が経過していたら実施する
    """
    if interval_hours <= 0:
        return False
    if not last_backup_at:
        return True
    return now - last_backup_at >= interval_hours * 3600


def backup_filename(now: float) -> str:
    """スナップショットのファイル名 (bot-backup-YYYYMMDD-HHMM.db)。"""
    dt = datetime.datetime.fromtimestamp(now)
    return dt.strftime("bot-backup-%Y%m%d-%H%M.db")


def format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _format_dt(timestamp: float, style: str) -> str:
    dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    return discord.utils.format_dt(dt, style)


def _create_snapshot() -> str:
    """一時ファイルにスナップショットを作り、そのパスを返す。

    使い終わったら呼び出し側で削除すること。
    """
    fd, path = tempfile.mkstemp(prefix="bot-backup-", suffix=".db")
    os.close(fd)
    db.backup_to(path)
    return path


class BackupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.backup_loop.start()

    def cog_unload(self):
        self.backup_loop.cancel()

    # ---------- 自動バックアップ (ループ) ----------

    @tasks.loop(minutes=30)
    async def backup_loop(self):
        now = time.time()
        for guild in list(self.bot.guilds):
            try:
                await self._auto_backup(guild, now)
            except Exception as error:  # 1件の失敗でループ自体を止めない
                print(
                    f"⚠️ ギルド {guild.id} の DB バックアップに失敗: {error}"
                )

    @backup_loop.before_loop
    async def before_backup_loop(self):
        await self.bot.wait_until_ready()

    async def _auto_backup(self, guild: discord.Guild, now: float):
        gid = guild.id
        channel_id = settings.get_backup_channel_id(gid)
        if not channel_id:
            return
        interval = settings.get_backup_interval_hours(gid)
        last_at = settings.get_backup_last_at(gid)
        if not is_due(last_at, now, interval):
            return
        channel = await self._fetch_channel(channel_id)
        if channel is None:
            print(
                f"⚠️ ギルド {gid} のバックアップ先チャンネル "
                f"{channel_id} が見つかりません"
            )
            return
        await self._send_snapshot(channel, now)
        # 上限超過で警告だけ投稿した場合も実施済みにして、
        # 30分ごとに警告を繰り返さないようにする
        settings.set_value(gid, settings.BACKUP_LAST_AT, now)

    async def _fetch_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _send_snapshot(self, channel, now: float):
        """スナップショットを作って channel に投稿する。

        (ファイルサイズ, アップロードできたか) を返す。上限超過時は
        ファイルの代わりに警告文を投稿して (サイズ, False) を返す。
        送信自体の例外は呼び出し側に伝播させる。一時ファイルは
        成否にかかわらず必ず削除する。
        """
        path = _create_snapshot()
        try:
            size = os.path.getsize(path)
            if size > MAX_UPLOAD_BYTES:
                await channel.send(
                    "⚠️ DBバックアップのファイルが大きすぎるため、"
                    f"アップロードできませんでした（{format_file_size(size)}）。"
                    "お手数ですが data/bot.db を直接保管してください。"
                )
                return size, False
            await channel.send(
                content=(
                    "💾 **DBバックアップ** "
                    f"{_format_dt(now, 'F')}（{format_file_size(size)}）"
                ),
                file=discord.File(path, filename=backup_filename(now)),
            )
            return size, True
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ---------- /backup グループ (管理者) ----------

    backup_group = app_commands.Group(
        name="backup",
        description="DBバックアップ管理（管理者専用）",
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

    @backup_group.command(
        name="now", description="DBバックアップを今すぐ投稿します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_now(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        await interaction.response.defer(ephemeral=True)

        configured_id = settings.get_backup_channel_id(gid)
        target = None
        to_configured = False
        if configured_id:
            target = await self._fetch_channel(configured_id)
            to_configured = target is not None
        if target is None:
            target = interaction.channel
        if target is None:
            await interaction.followup.send(
                "⚠️ 投稿先チャンネルが取得できませんでした。", ephemeral=True
            )
            return

        now = time.time()
        try:
            size, uploaded = await self._send_snapshot(target, now)
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"⚠️ ギルド {gid} の手動バックアップ投稿に失敗: {error}")
            await interaction.followup.send(
                "⚠️ バックアップの投稿に失敗しました。"
                "チャンネルの権限を確認してください。",
                ephemeral=True,
            )
            return
        if to_configured:
            settings.set_value(gid, settings.BACKUP_LAST_AT, now)
        if uploaded:
            await interaction.followup.send(
                f"✅ DBバックアップを {target.mention} に投稿しました"
                f"（{format_file_size(size)}）。",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ ファイルが大きすぎてアップロードできませんでした"
                f"（{format_file_size(size)}）。"
                "data/bot.db を直接保管してください。",
                ephemeral=True,
            )

    @backup_group.command(
        name="channel", description="バックアップの投稿先チャンネルを設定します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="バックアップを投稿するチャンネル")
    async def backup_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.BACKUP_CHANNEL_ID, channel.id
        )
        await interaction.response.send_message(
            f"✅ バックアップの投稿先を {channel.mention} に設定しました。",
            ephemeral=True,
        )

    @backup_group.command(
        name="interval",
        description="自動バックアップの間隔（時間）を設定します（0で自動オフ）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(hours="間隔（時間、0〜168。0は自動バックアップ無効）")
    async def backup_interval(
        self, interaction: discord.Interaction, hours: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        if not (0 <= hours <= settings.BACKUP_INTERVAL_HARD_MAX):
            await reply_error(
                interaction,
                "間隔は 0 〜 "
                f"{settings.BACKUP_INTERVAL_HARD_MAX} 時間で指定してください。",
            )
            return
        settings.set_value(
            interaction.guild.id, settings.BACKUP_INTERVAL_HOURS, hours
        )
        if hours == 0:
            text = "✅ 自動バックアップを **無効** にしました（手動の /backup now は使えます）。"
        else:
            text = f"✅ 自動バックアップの間隔を **{hours} 時間** に設定しました。"
        await interaction.response.send_message(text, ephemeral=True)

    @backup_group.command(
        name="status", description="バックアップ設定と最終実施時刻を表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_status(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        channel_id = settings.get_backup_channel_id(gid)
        interval = settings.get_backup_interval_hours(gid)
        last_at = settings.get_backup_last_at(gid)
        lines = [
            "投稿先チャンネル: "
            + (f"<#{channel_id}>" if channel_id else "未設定"),
            "自動バックアップ間隔: "
            + (f"{interval} 時間" if interval > 0 else "無効（手動のみ）"),
            "最終バックアップ: "
            + (
                f"{_format_dt(last_at, 'F')}（{_format_dt(last_at, 'R')}）"
                if last_at
                else "未実施"
            ),
        ]
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
