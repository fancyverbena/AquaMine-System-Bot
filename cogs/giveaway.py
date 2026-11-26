"""抽選 (プレゼント企画)。

管理者が /giveaway start で賞品・当選者数・期間を指定して開始すると、
「🎉 参加する」ボタン付きのパネルが投稿される。メンバーはボタンで
参加/取り消しをトグルでき、締切になると自動で抽選して当選者を発表する。

永続化の方式:
  抽選と参加者はすべて DB (giveaways / giveaway_entries) に保存する。
  終了処理は discord.ext.tasks のループ (30秒間隔) が期限切れの active な
  抽選を回収する形で、Bot が落ちていた間に締切を迎えた抽選も再起動直後の
  最初のループで終了する。
  ボタンはロールパネルと同じ DynamicItem 方式で、custom_id は
  `v2:giveaway:<giveaway_id>`。クリック時は DB を引き直し、終了済みの
  抽選のボタンは「終了しています」と返すだけになる。
"""
import datetime
import random
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.db import db
from app.ui import COLOR_GOLD, COLOR_PRIMARY, COLOR_SUCCESS, base_embed, reply_error

CUSTOM_ID_TEMPLATE = r"v2:giveaway:(?P<giveaway_id>[0-9]+)"

MAX_PRIZE_LENGTH = 200
MAX_WINNER_COUNT = 20
MIN_DURATION_SECONDS = 10
MAX_DURATION_SECONDS = 30 * 24 * 3600

_DURATION_RE = re.compile(r"^\s*([0-9]+)\s*([smhd])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text):
    """`30s` `10m` `2h` `1d` のような期間文字列を秒数にする。

    数値+単位 (s/m/h/d) の1組だけを受け付け、大文字小文字や前後の
    空白は許容する。有効範囲は 10秒 〜 30日。それ以外は None。
    """
    if not isinstance(text, str):
        return None
    match = _DURATION_RE.fullmatch(text)
    if match is None:
        return None
    seconds = int(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]
    if not (MIN_DURATION_SECONDS <= seconds <= MAX_DURATION_SECONDS):
        return None
    return seconds


def draw_winners(entry_user_ids, winner_count, rng=None):
    """参加者から当選者を重複なしでランダムに選ぶ。

    当選者数は min(winner_count, 参加者数) で、参加者がいなければ []。
    再抽選 (reroll) のときは、呼び出し側が過去の当選者を除いた参加者
    リストを渡すこと。rng に random.Random を渡すとテストで再現できる。
    """
    entries = list(entry_user_ids or [])
    if not entries or winner_count <= 0:
        return []
    chooser = rng if rng is not None else random
    return chooser.sample(entries, min(winner_count, len(entries)))


def _format_dt(timestamp: float, style: str) -> str:
    dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    return discord.utils.format_dt(dt, style)


def build_giveaway_embed(row, entry_count: int) -> discord.Embed:
    """開催中パネルの Embed。"""
    embed = base_embed("🎉 抽選開催中！", color=COLOR_GOLD)
    embed.add_field(name="賞品", value=row["prize"], inline=False)
    embed.add_field(name="当選者数", value=f"{row['winner_count']} 人", inline=True)
    embed.add_field(
        name="参加人数", value=f"{entry_count} 人", inline=True
    )
    embed.add_field(
        name="終了日時",
        value=(
            f"{_format_dt(row['ends_at'], 'R')}"
            f"（{_format_dt(row['ends_at'], 'f')}）"
        ),
        inline=False,
    )
    embed.add_field(name="主催者", value=f"<@{row['host_id']}>", inline=True)
    embed.set_footer(text=f"抽選ID: {row['id']} ／ ボタンで参加・取り消しができます")
    return embed


def build_giveaway_ended_embed(row, entry_count: int) -> discord.Embed:
    """終了後パネルの Embed。"""
    winners = row["winners"]
    embed = base_embed("🎉 抽選終了", color=COLOR_SUCCESS)
    embed.add_field(name="賞品", value=row["prize"], inline=False)
    if winners:
        winner_text = " ".join(f"<@{uid}>" for uid in winners)
    else:
        winner_text = "参加者がいなかったため当選者なし"
    embed.add_field(name="当選者", value=winner_text, inline=False)
    embed.add_field(
        name="参加人数", value=f"{entry_count} 人", inline=True
    )
    embed.add_field(name="主催者", value=f"<@{row['host_id']}>", inline=True)
    embed.set_footer(text=f"抽選ID: {row['id']}")
    return embed


def build_giveaway_cancelled_embed(row) -> discord.Embed:
    """キャンセル後パネルの Embed。"""
    embed = base_embed("🚫 抽選はキャンセルされました", color=COLOR_PRIMARY)
    embed.add_field(name="賞品", value=row["prize"], inline=False)
    embed.set_footer(text=f"抽選ID: {row['id']}")
    return embed


async def handle_giveaway_entry(interaction: discord.Interaction, giveaway_id: int):
    """参加ボタンのクリック処理 (DynamicItem の callback から呼ばれる)。"""
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
        return
    if member.bot:
        return

    row = db.get_giveaway(giveaway_id)
    now = time.time()
    if (
        row is None
        or row["guild_id"] != guild.id
        or row["status"] != "active"
        or now >= row["ends_at"]
    ):
        await reply_error(interaction, "この抽選はすでに終了しています。")
        return

    entered = db.toggle_entry(giveaway_id, member.id, now)
    entry_count = db.count_entries(giveaway_id)

    # パネルの参加人数表示を更新する (失敗しても参加自体は確定している)
    message = interaction.message
    if message is not None:
        try:
            await message.edit(embed=build_giveaway_embed(row, entry_count))
        except discord.HTTPException:
            pass

    if entered:
        await interaction.response.send_message(
            f"✅ 抽選に参加しました（現在 {entry_count} 人）。"
            "もう一度押すと参加を取り消せます。",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"✅ 参加を取り消しました（現在 {entry_count} 人）。",
            ephemeral=True,
        )


class GiveawayButton(
    discord.ui.DynamicItem[discord.ui.Button], template=CUSTOM_ID_TEMPLATE
):
    """抽選 ID を custom_id に埋め込む動的ボタン。

    パネル投稿時はこのクラス自体を View に入れて送る。再起動後の
    クリックは bot.add_dynamic_items の登録を経由して from_custom_id で
    再生成され、callback が呼ばれる。
    """

    def __init__(self, giveaway_id: int):
        super().__init__(
            discord.ui.Button(
                label="🎉 参加する",
                style=discord.ButtonStyle.primary,
                custom_id=f"v2:giveaway:{giveaway_id}",
            )
        )
        self.giveaway_id = giveaway_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match,
        /,
    ):
        return cls(int(match["giveaway_id"]))

    async def callback(self, interaction: discord.Interaction):
        await handle_giveaway_entry(interaction, self.giveaway_id)


def build_giveaway_view(giveaway_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(GiveawayButton(giveaway_id))
    return view


class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.end_loop.start()

    def cog_unload(self):
        self.end_loop.cancel()

    # ---------- 終了処理 (ループ / end / 再起動後の回収で共用) ----------

    @tasks.loop(seconds=30)
    async def end_loop(self):
        now = time.time()
        for row in db.list_overdue_giveaways(now):
            try:
                await self.finalize_giveaway(row["id"])
            except Exception as error:  # 1件の失敗でループ自体を止めない
                print(f"⚠️ 抽選 #{row['id']} の終了処理に失敗: {error}")

    @end_loop.before_loop
    async def before_end_loop(self):
        await self.bot.wait_until_ready()

    async def _fetch_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _edit_panel(self, row, embed):
        """パネルメッセージをボタンなしの状態に差し替える (失敗は無視)。"""
        if not row.get("message_id"):
            return
        channel = await self._fetch_channel(row["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(row["message_id"])
            await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def finalize_giveaway(self, giveaway_id: int):
        """抽選を終了する: 抽選 → DB 確定 → パネル差し替え → 結果発表。

        ループ・/giveaway end・再起動後の回収すべてがこのメソッドを使う。
        対象が無い/終了済みの場合は None、終了できたら
        {"row": 終了後の行, "entry_count": 参加者数} を返す。
        """
        row = db.get_giveaway(giveaway_id)
        if row is None or row["status"] != "active":
            return None

        entries = db.list_entry_user_ids(giveaway_id)
        winners = draw_winners(entries, row["winner_count"])
        db.finish_giveaway(giveaway_id, "ended", winners)
        row = db.get_giveaway(giveaway_id)

        await self._edit_panel(
            row, build_giveaway_ended_embed(row, len(entries))
        )

        channel = await self._fetch_channel(row["channel_id"])
        if channel is not None:
            if winners:
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                text = (
                    f"🎉 **抽選結果** 🎉\n"
                    f"賞品「{row['prize']}」の当選者は {mentions} です！"
                    "おめでとうございます！🎊"
                )
            else:
                text = (
                    f"🎉 **抽選結果**\n"
                    f"賞品「{row['prize']}」の抽選は、"
                    "参加者がいなかったため当選者なしでした。"
                )
            try:
                await channel.send(text)
            except (discord.Forbidden, discord.HTTPException):
                pass
        return {"row": row, "entry_count": len(entries)}

    # ---------- /giveaway グループ (管理者) ----------

    giveaway_group = app_commands.Group(
        name="giveaway",
        description="抽選管理（管理者専用）",
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

    def _get_own_giveaway(self, interaction: discord.Interaction, giveaway_id: int):
        """このギルドの抽選でなければ None (他ギルドの ID を操作させない)。"""
        row = db.get_giveaway(giveaway_id)
        if row is None or row["guild_id"] != interaction.guild.id:
            return None
        return row

    @giveaway_group.command(
        name="start", description="抽選を開始します（このチャンネルにパネルを投稿）"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        prize="賞品（200文字まで）",
        winners="当選者数（1〜20）",
        duration="期間（例: 30s / 10m / 2h / 1d、10秒〜30日）",
    )
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        prize: str,
        winners: int,
        duration: str,
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        prize = (prize or "").strip()
        if not prize:
            await reply_error(interaction, "賞品を入力してください。")
            return
        if len(prize) > MAX_PRIZE_LENGTH:
            await reply_error(
                interaction, f"賞品は {MAX_PRIZE_LENGTH} 文字以内で入力してください。"
            )
            return
        if not (1 <= winners <= MAX_WINNER_COUNT):
            await reply_error(
                interaction, f"当選者数は 1 〜 {MAX_WINNER_COUNT} で指定してください。"
            )
            return
        seconds = parse_duration(duration)
        if seconds is None:
            await reply_error(
                interaction,
                "期間の形式が正しくありません。`30s` `10m` `2h` `1d` のように"
                "指定してください（10秒〜30日）。",
            )
            return
        channel = interaction.channel
        if channel is None:
            await reply_error(interaction, "チャンネルが取得できませんでした。")
            return

        now = time.time()
        giveaway_id = db.create_giveaway(
            interaction.guild.id,
            channel.id,
            interaction.user.id,
            prize,
            winners,
            now + seconds,
            now,
        )
        row = db.get_giveaway(giveaway_id)
        try:
            message = await channel.send(
                embed=build_giveaway_embed(row, 0),
                view=build_giveaway_view(giveaway_id),
            )
        except (discord.Forbidden, discord.HTTPException):
            # パネルを投稿できなかった抽選は取り消して残さない
            db.finish_giveaway(giveaway_id, "cancelled", [])
            await reply_error(
                interaction, "このチャンネルにパネルを投稿する権限が Bot にありません。"
            )
            return
        db.set_giveaway_message(giveaway_id, message.id)
        await interaction.response.send_message(
            f"✅ 抽選を開始しました（抽選ID: {giveaway_id}）。\n"
            f"終了日時: {_format_dt(row['ends_at'], 'F')}"
            f"（{_format_dt(row['ends_at'], 'R')}）",
            ephemeral=True,
        )

    @giveaway_group.command(
        name="end", description="開催中の抽選を今すぐ終了して抽選します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(giveaway_id="抽選ID")
    @app_commands.rename(giveaway_id="id")
    async def giveaway_end(
        self, interaction: discord.Interaction, giveaway_id: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        row = self._get_own_giveaway(interaction, giveaway_id)
        if row is None:
            await reply_error(interaction, "その抽選IDは見つかりません。")
            return
        if row["status"] != "active":
            await reply_error(interaction, "その抽選はすでに終了しています。")
            return
        await interaction.response.defer(ephemeral=True)
        result = await self.finalize_giveaway(giveaway_id)
        if result is None:
            await interaction.followup.send(
                "⚠️ その抽選はすでに終了しています。", ephemeral=True
            )
            return
        winners = result["row"]["winners"]
        if winners:
            mentions = " ".join(f"<@{uid}>" for uid in winners)
            text = f"✅ 抽選 #{giveaway_id} を終了しました。当選者: {mentions}"
        else:
            text = f"✅ 抽選 #{giveaway_id} を終了しました（当選者なし）。"
        await interaction.followup.send(text, ephemeral=True)

    @giveaway_group.command(
        name="reroll",
        description="終了した抽選を再抽選します（当選済みの人は除外）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(giveaway_id="抽選ID")
    @app_commands.rename(giveaway_id="id")
    async def giveaway_reroll(
        self, interaction: discord.Interaction, giveaway_id: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        row = self._get_own_giveaway(interaction, giveaway_id)
        if row is None:
            await reply_error(interaction, "その抽選IDは見つかりません。")
            return
        if row["status"] != "ended":
            await reply_error(
                interaction, "再抽選できるのは終了した抽選だけです。"
            )
            return
        previous_winners = list(row["winners"])
        eligible = [
            uid
            for uid in db.list_entry_user_ids(giveaway_id)
            if uid not in previous_winners
        ]
        if not eligible:
            await reply_error(
                interaction,
                "再抽選できる参加者が残っていません（全員が当選済みです）。",
            )
            return
        await interaction.response.defer(ephemeral=True)
        new_winners = draw_winners(eligible, row["winner_count"])
        # 過去の当選者と合わせて保存し、次の再抽選でも全員を除外する
        combined = previous_winners + new_winners
        db.finish_giveaway(giveaway_id, "ended", combined)
        row = db.get_giveaway(giveaway_id)

        entry_count = db.count_entries(giveaway_id)
        await self._edit_panel(row, build_giveaway_ended_embed(row, entry_count))

        mentions = " ".join(f"<@{uid}>" for uid in new_winners)
        channel = await self._fetch_channel(row["channel_id"])
        if channel is not None:
            try:
                await channel.send(
                    f"🎉 **再抽選の結果** 🎉\n"
                    f"賞品「{row['prize']}」の新しい当選者は {mentions} です！"
                    "おめでとうございます！🎊"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.followup.send(
            f"✅ 抽選 #{giveaway_id} を再抽選しました。新しい当選者: {mentions}",
            ephemeral=True,
        )

    @giveaway_group.command(
        name="cancel", description="開催中の抽選を中止します（抽選しません）"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(giveaway_id="抽選ID")
    @app_commands.rename(giveaway_id="id")
    async def giveaway_cancel(
        self, interaction: discord.Interaction, giveaway_id: int
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        row = self._get_own_giveaway(interaction, giveaway_id)
        if row is None:
            await reply_error(interaction, "その抽選IDは見つかりません。")
            return
        if row["status"] != "active":
            await reply_error(interaction, "その抽選はすでに終了しています。")
            return
        await interaction.response.defer(ephemeral=True)
        db.finish_giveaway(giveaway_id, "cancelled", [])
        row = db.get_giveaway(giveaway_id)
        await self._edit_panel(row, build_giveaway_cancelled_embed(row))
        await interaction.followup.send(
            f"✅ 抽選 #{giveaway_id} をキャンセルしました。", ephemeral=True
        )

    @giveaway_group.command(
        name="list", description="開催中の抽選を表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def giveaway_list(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        rows = db.list_active_giveaways(interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                "開催中の抽選はありません。", ephemeral=True
            )
            return
        lines = [f"開催中の抽選（{len(rows)} 件）:"]
        for row in rows:
            entry_count = db.count_entries(row["id"])
            lines.append(
                f"- ID {row['id']}: 「{row['prize']}」"
                f" 当選 {row['winner_count']} 人 / 参加 {entry_count} 人"
                f" / 終了 {_format_dt(row['ends_at'], 'R')}"
                f"（{_format_dt(row['ends_at'], 'f')}）"
            )
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
