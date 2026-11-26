"""サーバー統計レポート。

メッセージ数や参加/退出は既存テーブルからは遡れないため、この Cog が
日次カウンタ (stats_daily) に積み上げていく。日付の区切りは JST の
暦日。違反・抽選・フィードバックの件数は既存テーブルの created_at
から期間集計する (チケットは閉鎖時に行が消えるため対象外)。

レポートは /stats show で過去7日/30日分をその場で表示でき、投稿先を
設定すれば毎週月曜日 09:05 (JST) に過去7日分が自動投稿される
(週間 XP ランキングの 09:00 投稿とは5分ずらしてある)。投稿済みの週は
ギルド設定 stats_last_week に記録され、再起動しても二重投稿には
ならない。

注意: カウンタはこの機能が入ってから積み上がる。導入前の過去分は
存在しない (常に 0 として扱われる)。
"""
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app import settings
from app.db import db
from app.ui import COLOR_PRIMARY, reply_error
from cogs.weekly import JST, previous_week_key

METRIC_MESSAGE = "message"
METRIC_JOIN = "join"
METRIC_LEAVE = "leave"
TOP_CHANNELS = 5
# 統計レポートを自動投稿する時刻 (月曜日 09:05 JST 以降の最初のループで投稿)
POST_TIME_JST = datetime.time(9, 5)


def _as_jst(dt: datetime.datetime) -> datetime.datetime:
    """datetime を JST に変換する。naive な値は UTC とみなす。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(JST)


def jst_date(dt: datetime.datetime) -> str:
    """dt の JST での暦日 (YYYY-MM-DD)。日次カウンタの日付バケット。"""
    return _as_jst(dt).date().isoformat()


def should_post_stats(now: datetime.datetime, last_posted_week_key: str) -> bool:
    """先週分の統計レポートを今自動投稿すべきかどうか。

    月曜日 (JST) の 09:05 以降で、かつ前週分がまだ投稿されていなければ
    True。それ以外 (曜日が違う・時刻が早い・投稿済み) は False。
    """
    jst = _as_jst(now)
    if jst.weekday() != 0:  # 月曜日だけ
        return False
    if jst.time() < POST_TIME_JST:
        return False
    return last_posted_week_key != previous_week_key(now)


def period_range(now: datetime.datetime, days: int):
    """直近 days 日間 (今日を含む、JST) の集計範囲を返す。

    戻り値は (開始日, 終了日, 開始 UNIX 時刻, 終了 UNIX 時刻)。
    日付は YYYY-MM-DD 文字列、時刻は [開始日 00:00 JST, 終了日翌日
    00:00 JST) の半開区間で、既存テーブルの created_at 集計に使う。
    """
    today = _as_jst(now).date()
    start = today - datetime.timedelta(days=days - 1)
    start_dt = datetime.datetime.combine(
        start, datetime.time.min, tzinfo=JST
    )
    end_dt = start_dt + datetime.timedelta(days=days)
    return start.isoformat(), today.isoformat(), start_dt.timestamp(), end_dt.timestamp()


def summarize(rows, *, days: int) -> dict:
    """日次カウンタの行列をレポート用の集計にまとめる純粋関数。

    rows は db.get_stats の戻り値 ({"date", "metric", "channel_id",
    "count"} の列)。戻り値:
      total_messages  メッセージ総数
      messages_per_day  1日平均 (データのない日も含めて days で割る)
      joins / leaves / net  参加・退出・純増 (joins - leaves)
      top_channels  [(channel_id, 件数)] の上位 TOP_CHANNELS 件
                    (件数の降順、同点は channel_id の昇順で決定的に。
                    channel_id=0 の行はメッセージ集計から除外する)
    """
    total_messages = 0
    joins = 0
    leaves = 0
    channel_counts = {}
    for row in rows:
        count = int(row["count"])
        metric = row["metric"]
        if metric == METRIC_MESSAGE:
            total_messages += count
            channel_id = int(row["channel_id"])
            if channel_id:
                channel_counts[channel_id] = (
                    channel_counts.get(channel_id, 0) + count
                )
        elif metric == METRIC_JOIN:
            joins += count
        elif metric == METRIC_LEAVE:
            leaves += count
    top_channels = sorted(
        channel_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[:TOP_CHANNELS]
    return {
        "total_messages": total_messages,
        "messages_per_day": (total_messages / days) if days else 0.0,
        "joins": joins,
        "leaves": leaves,
        "net": joins - leaves,
        "top_channels": top_channels,
    }


def build_stats_embed(guild, summary, days, start_date, end_date, extras):
    """統計レポートの Embed を作る。

    extras は既存テーブルからの期間件数 {"infractions", "giveaways",
    "feedback"}。メンバー数は DB ではなくギルドの現在値を使う。
    """
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    embed = discord.Embed(
        title=f"📈 サーバー統計レポート（過去{days}日間）",
        color=COLOR_PRIMARY,
    )
    embed.add_field(
        name="対象期間",
        value=f"{start.year}/{start.month}/{start.day} 〜 "
        f"{end.year}/{end.month}/{end.day}",
        inline=False,
    )
    member_count = guild.member_count if guild is not None else None
    embed.add_field(
        name="メンバー数（現在）",
        value=f"{member_count} 人" if member_count is not None else "不明",
        inline=True,
    )
    net = summary["net"]
    net_text = f"+{net}" if net > 0 else str(net)
    embed.add_field(
        name="参加 / 退出 / 純増",
        value=f"{summary['joins']} / {summary['leaves']} / {net_text}",
        inline=True,
    )
    embed.add_field(
        name="メッセージ",
        value=f"合計 {summary['total_messages']} 件"
        f"（1日平均 {summary['messages_per_day']:.1f} 件）",
        inline=False,
    )
    if summary["top_channels"]:
        top_lines = "\n".join(
            f"{i}. <#{channel_id}> — {count} 件"
            for i, (channel_id, count) in enumerate(
                summary["top_channels"], start=1
            )
        )
    else:
        top_lines = "データがありません。"
    embed.add_field(
        name=f"盛り上がったチャンネル Top{TOP_CHANNELS}",
        value=top_lines,
        inline=False,
    )
    embed.add_field(
        name="その他の活動",
        value=(
            f"違反検知: {extras['infractions']} 件\n"
            f"抽選開催: {extras['giveaways']} 件\n"
            f"フィードバック投稿: {extras['feedback']} 件"
        ),
        inline=False,
    )
    embed.set_footer(
        text="メッセージ数・参加/退出は統計機能の導入以降に集計されたデータです。"
    )
    return embed


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stats_loop.start()

    def cog_unload(self):
        self.stats_loop.cancel()

    # ---------- 収集 (リスナー) ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        db.increment_stat(
            message.guild.id,
            jst_date(message.created_at),
            METRIC_MESSAGE,
            message.channel.id,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        now = datetime.datetime.now(datetime.timezone.utc)
        db.increment_stat(member.guild.id, jst_date(now), METRIC_JOIN)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        now = datetime.datetime.now(datetime.timezone.utc)
        db.increment_stat(member.guild.id, jst_date(now), METRIC_LEAVE)

    # ---------- レポート生成 ----------

    def _build_report_embed(self, guild, now, days: int):
        start_date, end_date, start_ts, end_ts = period_range(now, days)
        summary = summarize(
            db.get_stats(guild.id, start_date, end_date), days=days
        )
        extras = {
            "infractions": db.count_infractions_in_range(
                guild.id, start_ts, end_ts
            ),
            "giveaways": db.count_giveaways_in_range(
                guild.id, start_ts, end_ts
            ),
            "feedback": db.count_feedback_in_range(
                guild.id, start_ts, end_ts
            ),
        }
        return build_stats_embed(
            guild, summary, days, start_date, end_date, extras
        )

    # ---------- 自動投稿 (ループ) ----------

    @tasks.loop(hours=1)
    async def stats_loop(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for guild in list(self.bot.guilds):
            try:
                await self._maybe_post(guild, now)
            except Exception as error:  # 1件の失敗でループ自体を止めない
                print(
                    f"⚠️ ギルド {guild.id} の統計レポート処理に失敗: {error}"
                )

    @stats_loop.before_loop
    async def before_stats_loop(self):
        await self.bot.wait_until_ready()

    async def _maybe_post(self, guild: discord.Guild, now):
        gid = guild.id
        channel_id = settings.get_stats_channel_id(gid)
        if not channel_id:
            return
        last_week = settings.get_stats_last_week(gid)
        if not should_post_stats(now, last_week):
            return
        channel = await self._fetch_channel(channel_id)
        if channel is None:
            print(
                f"⚠️ ギルド {gid} の統計レポート投稿先チャンネル "
                f"{channel_id} が見つかりません"
            )
            return
        await channel.send(embed=self._build_report_embed(guild, now, 7))
        # 投稿できたら投稿済みの週を記録して二重投稿を防ぐ
        settings.set_value(
            gid, settings.STATS_LAST_WEEK, previous_week_key(now)
        )

    async def _fetch_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    # ---------- /stats グループ ----------

    stats_group = app_commands.Group(
        name="stats",
        description="サーバー統計レポート",
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

    @stats_group.command(
        name="show", description="サーバー統計レポートを表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(period="集計期間")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="過去7日間", value=7),
            app_commands.Choice(name="過去30日間", value=30),
        ]
    )
    async def stats_show(
        self, interaction: discord.Interaction, period: app_commands.Choice[int]
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        embed = self._build_report_embed(
            interaction.guild, now, period.value
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stats_group.command(
        name="channel",
        description="統計レポートの週次自動投稿先チャンネルを設定します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="レポートを投稿するチャンネル")
    async def stats_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        settings.set_value(gid, settings.STATS_CHANNEL_ID, channel.id)
        await interaction.response.send_message(
            f"✅ 統計レポートの投稿先を {channel.mention} に設定しました。"
            "毎週月曜日 09:05 (JST) に過去7日間のレポートが投稿されます。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
