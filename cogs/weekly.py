"""週間 XP ランキングの自動投稿。

user_xp には累計 XP しかないため、「その週に増えた XP」は週の開始時点の
累計を基準スナップショット (xp_weekly_baseline) として覚えておき、
現在の累計との差分で求める。

週は月曜日 00:00 (JST) から次の月曜日 00:00 (JST) まで。毎週月曜日
09:00 (JST) 以降、1時間間隔のループが先週分のランキング (増加量の
上位10件) を設定チャンネルへ投稿し、基準を現在の累計に差し替える。
投稿済みの週はギルド設定 weekly_ranking_last_week に記録され、
再起動しても二重投稿にはならない。

初回 (基準がまだ無いギルド) は投稿せず、現在の累計を基準として
記録するだけ。投稿が始まるのは翌週の月曜日から。
"""
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app import settings
from app.db import db
from app.ui import COLOR_GOLD, reply_error
from cogs.leveling import calc_level

JST = datetime.timezone(datetime.timedelta(hours=9))
TOP_N = 10
# 週間ランキングを投稿する時刻 (月曜日 09:00 JST 以降の最初のループで投稿)
POST_TIME_JST = datetime.time(9, 0)


def _as_jst(dt: datetime.datetime) -> datetime.datetime:
    """datetime を JST に変換する。naive な値は UTC とみなす。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(JST)


def week_key(dt: datetime.datetime) -> str:
    """dt が属する週のキー (週開始 = 月曜日の日付、JST、YYYY-MM-DD)。"""
    jst = _as_jst(dt)
    monday = jst.date() - datetime.timedelta(days=jst.weekday())
    return monday.isoformat()


def previous_week_key(dt: datetime.datetime) -> str:
    """dt が属する週の前週のキー。"""
    jst = _as_jst(dt)
    monday = jst.date() - datetime.timedelta(days=jst.weekday() + 7)
    return monday.isoformat()


def should_post(now: datetime.datetime, last_posted_week_key: str) -> bool:
    """先週分のランキングを今投稿すべきかどうか。

    月曜日 (JST) の 09:00 以降で、かつ前週分がまだ投稿されていなければ
    True。それ以外 (曜日が違う・時刻が早い・投稿済み) は False。
    """
    jst = _as_jst(now)
    if jst.weekday() != 0:  # 月曜日だけ
        return False
    if jst.time() < POST_TIME_JST:
        return False
    return last_posted_week_key != previous_week_key(now)


def compute_gains(current_rows, baselines) -> list:
    """週の増加量ランキングを計算する純粋関数。

    current_rows は (user_id, 累計 XP) の列、baselines は
    {user_id: 週開始時点の累計 XP}。基準に無いユーザーは 0 から
    増えたものとして扱う (週の途中で初めて XP を得た人)。
    増加量が 0 以下の人は含めない。並び順は増加量の降順、同点なら
    累計 XP の降順、それも同じなら user_id の昇順 (決定的にする)。
    戻り値は {"user_id", "gain", "total_xp"} のリスト。
    """
    entries = []
    for user_id, total_xp in current_rows:
        gain = total_xp - (baselines or {}).get(user_id, 0)
        if gain > 0:
            entries.append(
                {"user_id": user_id, "gain": gain, "total_xp": total_xp}
            )
    entries.sort(
        key=lambda e: (-e["gain"], -e["total_xp"], e["user_id"])
    )
    return entries


def week_range_label(key: str) -> str:
    """週キー (月曜日の日付) から「M/D〜M/D」(月曜〜日曜) の表示を作る。"""
    monday = datetime.date.fromisoformat(key)
    sunday = monday + datetime.timedelta(days=6)
    return f"{monday.month}/{monday.day}〜{sunday.month}/{sunday.day}"


def build_ranking_lines(guild, entries) -> list:
    """ランキングの各行テキストを作る。メンバーが取れない場合は ID 表示。"""
    lines = []
    for i, entry in enumerate(entries, start=1):
        member = guild.get_member(entry["user_id"]) if guild is not None else None
        name = (
            member.display_name
            if member is not None
            else f"ユーザー (ID: {entry['user_id']})"
        )
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        level = calc_level(entry["total_xp"])
        lines.append(
            f"{medal} **{name}** - +{entry['gain']} XP（現在 Lv.{level}）"
        )
    return lines


def build_ranking_embed(guild, entries, key: str, *, footer: str = None):
    """週間ランキングの Embed を作る (entries は表示分に絞ったもの)。"""
    embed = discord.Embed(
        title=f"📊 週間XPランキング（{week_range_label(key)}）",
        color=COLOR_GOLD,
    )
    lines = build_ranking_lines(guild, entries)
    embed.description = (
        "\n".join(lines) if lines else "まだデータがありません。"
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


class WeeklyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.weekly_loop.start()

    def cog_unload(self):
        self.weekly_loop.cancel()

    # ---------- 自動投稿 (ループ) ----------

    @tasks.loop(hours=1)
    async def weekly_loop(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for guild in list(self.bot.guilds):
            try:
                await self._process_guild(guild, now)
            except Exception as error:  # 1件の失敗でループ自体を止めない
                print(
                    f"⚠️ ギルド {guild.id} の週間ランキング処理に失敗: {error}"
                )

    @weekly_loop.before_loop
    async def before_weekly_loop(self):
        await self.bot.wait_until_ready()

    async def _process_guild(self, guild: discord.Guild, now):
        gid = guild.id
        channel_id = settings.get_weekly_ranking_channel_id(gid)
        if not channel_id:
            return
        if db.count_xp_baselines(gid) == 0:
            # 初回は現在の累計を基準として記録するだけ (投稿は翌週から)
            db.replace_xp_baselines(gid, db.get_all_user_xp(gid))
            return
        last_week = settings.get_weekly_ranking_last_week(gid)
        if not should_post(now, last_week):
            return
        channel = await self._fetch_channel(channel_id)
        if channel is None:
            print(
                f"⚠️ ギルド {gid} の週間ランキング投稿先チャンネル "
                f"{channel_id} が見つかりません"
            )
            return
        current_rows = db.get_all_user_xp(gid)
        entries = compute_gains(current_rows, db.get_xp_baselines(gid))[:TOP_N]
        prev_key = previous_week_key(now)
        if entries:
            await channel.send(
                embed=build_ranking_embed(guild, entries, prev_key)
            )
        else:
            await channel.send("📊 先週はXPの変動がありませんでした。")
        # 投稿できたら基準を現在の累計に差し替え、投稿済みの週を記録する
        db.replace_xp_baselines(gid, current_rows)
        settings.set_value(gid, settings.WEEKLY_RANKING_LAST_WEEK, prev_key)

    async def _fetch_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    def _current_entries(self, guild_id: int) -> list:
        """今週の途中経過 (基準からの増加量、上位 TOP_N 件)。"""
        return compute_gains(
            db.get_all_user_xp(guild_id), db.get_xp_baselines(guild_id)
        )[:TOP_N]

    # ---------- /weekly グループ ----------

    weekly_group = app_commands.Group(
        name="weekly",
        description="週間XPランキング",
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

    @weekly_group.command(
        name="ranking", description="今週のXP増加量ランキング（途中経過）を表示します"
    )
    async def weekly_ranking(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await reply_error(interaction, "このコマンドはサーバー内でのみ使えます。")
            return
        if db.count_xp_baselines(guild.id) == 0:
            await interaction.response.send_message(
                "📊 週間ランキングは集計準備中です。"
                "基準データが記録されると自動で集計が始まります。",
                ephemeral=True,
            )
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        entries = self._current_entries(guild.id)
        embed = build_ranking_embed(
            guild,
            entries,
            week_key(now),
            footer="今週の途中経過です。確定ランキングは月曜日 09:00 (JST) に自動投稿されます。",
        )
        if not entries:
            embed.description = "今週はまだXPの変動がありません。"
        await interaction.response.send_message(embed=embed)

    @weekly_group.command(
        name="channel",
        description="週間ランキングの自動投稿先チャンネルを設定します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="ランキングを投稿するチャンネル")
    async def weekly_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        settings.set_value(gid, settings.WEEKLY_RANKING_CHANNEL_ID, channel.id)
        note = ""
        if db.count_xp_baselines(gid) == 0:
            # 基準が無ければ今すぐ記録し、今週の増加分から集計する
            db.replace_xp_baselines(gid, db.get_all_user_xp(gid))
            note = (
                "\n現在の累計 XP を基準として記録しました。"
                "今週の増加分から集計されます。"
            )
        await interaction.response.send_message(
            f"✅ 週間ランキングの投稿先を {channel.mention} に設定しました。"
            "毎週月曜日 09:00 (JST) に先週分のランキングが投稿されます。"
            + note,
            ephemeral=True,
        )

    @weekly_group.command(
        name="test",
        description="今週の途中経過をプレビュー投稿します（集計には影響しません）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def weekly_test(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        gid = guild.id
        if db.count_xp_baselines(gid) == 0:
            await interaction.response.send_message(
                "📊 週間ランキングは集計準備中です。"
                "基準データが記録されると自動で集計が始まります。",
                ephemeral=True,
            )
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        entries = self._current_entries(gid)
        embed = build_ranking_embed(
            guild,
            entries,
            week_key(now),
            footer="プレビュー投稿です（今週の途中経過）。基準のリセットは行われません。",
        )
        if not entries:
            embed.description = "今週はまだXPの変動がありません。"
        channel_id = settings.get_weekly_ranking_channel_id(gid)
        target = await self._fetch_channel(channel_id) if channel_id else None
        if target is None:
            # 投稿先が未設定 (または取得できない) 場合はその場で見せる
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await target.send(embed=embed)
        await interaction.response.send_message(
            f"✅ プレビューを {target.mention} に投稿しました。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WeeklyCog(bot))
