"""一時 VC (ロビー参加で自動作成される個人用ボイスチャンネル)。

管理者が「ロビー」として指定した VC にメンバーが入ると、その人専用の
VC を自動で作成して移動させる。Bot を除くメンバーが全員抜けたら、
チャンネルは自動で削除される。

永続化の方式:
  作成したチャンネルは DB (temp_vc_channels) に記録する。Bot が落ちて
  いる間に全員が抜けたチャンネルは、再起動直後の掃除 (記録と実チャン
  ネルの照合) で削除される。チャンネルが既に消えている記録は破棄し、
  人が残っているチャンネルはそのまま残す。

空判定について:
  「空」は Bot を除くメンバーが0人の状態とする。音楽 Bot だけが残った
  チャンネルも、最後の人間が抜けた時点で削除対象になる (should_delete)。

チャンネル名について:
  名前は「{表示名} の VC」。表示名の整形には app.ui の
  sanitize_channel_part (チケットのチャンネル名で使われているもの) を
  流用し、小文字化・記号のハイフン化・長さ制限を通してから接尾辞を
  付ける。全体は Discord の上限 (100文字) 以内に収める。

公開範囲について:
  作成した VC はサーバーの全員が見て・入れるのが前提のため、カテゴリの
  権限を引き継がず、@everyone (default_role) に「チャンネルを見る」と
  「接続」を明示的に許可する上書きを付けて作る (public_overwrites)。
  ロビーが非公開カテゴリにあっても、一時 VC だけは全員に見える。
  これは権限の許可のみで、@everyone メンション等の通知は行わない。
"""
import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app import settings
from app.db import db
from app.ui import reply_error, sanitize_channel_part

# Discord のチャンネル名の上限
MAX_CHANNEL_NAME_LENGTH = 100
# 名前の接尾辞と、表示名部分に割り当てる最大長
NAME_SUFFIX = " の VC"
NAME_PART_MAX_LENGTH = MAX_CHANNEL_NAME_LENGTH - len(NAME_SUFFIX)
# 作成はできたが移動に失敗したチャンネルの空チェックを遅らせる秒数。
# すぐ消すのではなく、少し置いてから「まだ空なら削除」を実行する
ORPHAN_CHECK_DELAY_SECONDS = 30


def temp_channel_name(display_name) -> str:
    """一時 VC のチャンネル名 (「{表示名} の VC」) を作る純粋関数。

    表示名は sanitize_channel_part で整形する (空・空白だけなら
    フォールバックの "member")。全体は100文字以内に収まる。
    """
    part = sanitize_channel_part(
        display_name or "",
        fallback="member",
        max_length=NAME_PART_MAX_LENGTH,
    )
    return f"{part}{NAME_SUFFIX}"[:MAX_CHANNEL_NAME_LENGTH]


def should_delete(non_bot_member_count: int) -> bool:
    """一時 VC を削除すべきかどうかを判定する純粋関数。

    Bot を除くメンバーが0人なら削除する。音楽 Bot だけが残った状態も
    「空」として扱うため、Bot は数に含めない前提で人数を渡すこと。
    """
    return non_bot_member_count <= 0


def count_non_bot_members(channel) -> int:
    """チャンネル内の Bot でないメンバー数。"""
    return sum(1 for member in channel.members if not member.bot)


def join_action(existing_row) -> str:
    """ロビー参加時の動きを決める純粋関数。

    既に自分の一時 VC があれば作り直さず 'reuse' (そこへ移動する)、
    なければ 'create'。
    """
    return "reuse" if existing_row else "create"


def public_overwrites(default_role) -> dict:
    """一時 VC を全員が見られる・入れるようにする権限上書きを返す。

    カテゴリの権限を引き継ぐと、ロビーが非公開カテゴリにある場合に
    一時 VC も見えなくなってしまうため、@everyone (default_role) に
    「チャンネルを見る」「接続」を明示的に許可する。許可するのは
    この2つだけで、メンション等の通知は一切行わない。
    """
    return {
        default_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
        )
    }


class TempVcCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 作成処理中の (guild_id, user_id)。連続した参加イベントで
        # 二重にチャンネルを作らないためのガード
        self._creating: set = set()
        self.startup_cleanup_loop.start()

    def cog_unload(self):
        self.startup_cleanup_loop.cancel()

    # ---------- 起動時の掃除 ----------

    @tasks.loop(seconds=60, count=1)
    async def startup_cleanup_loop(self):
        """起動直後に DB の記録と実チャンネルを照合する (1回だけ)。

        チャンネルが消えている記録は破棄し、空になっているチャンネルは
        削除する。人が残っているチャンネルには何もしない。
        """
        try:
            for row in db.list_temp_vc_channels():
                channel = await self._fetch_channel(row["channel_id"])
                if channel is None:
                    db.remove_temp_vc_channel(row["channel_id"])
                    continue
                await self._delete_channel_if_empty(channel)
        except Exception as error:  # 掃除の失敗で Bot を止めない
            print(f"⚠️ 一時VCの起動時掃除に失敗: {error}")

    @startup_cleanup_loop.before_loop
    async def before_startup_cleanup_loop(self):
        await self.bot.wait_until_ready()

    # ---------- チャンネルの取得・削除 ----------

    async def _fetch_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _delete_channel_if_empty(self, channel) -> bool:
        """記録のある一時 VC が空なら削除して記録も消す。削除したら True。"""
        if db.get_temp_vc_channel(channel.id) is None:
            return False
        if not should_delete(count_non_bot_members(channel)):
            return False
        try:
            await channel.delete(reason="一時VC: 全員が退出したため自動削除")
        except (discord.Forbidden, discord.HTTPException) as error:
            # 消せなかった場合は記録を残し、次回の掃除で再試行する
            print(f"⚠️ 一時VC {channel.id} の削除に失敗: {error}")
            return False
        db.remove_temp_vc_channel(channel.id)
        return True

    async def _delayed_empty_check(self, channel_id: int):
        """少し置いてから空チェックする (移動失敗で残ったチャンネル用)。"""
        await asyncio.sleep(ORPHAN_CHECK_DELAY_SECONDS)
        try:
            channel = await self._fetch_channel(channel_id)
            if channel is None:
                db.remove_temp_vc_channel(channel_id)
                return
            await self._delete_channel_if_empty(channel)
        except Exception as error:
            print(f"⚠️ 一時VC {channel_id} の後始末に失敗: {error}")

    # ---------- 参加・退出の処理 ----------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        # 退出・移動元の一時 VC が空になったら削除する
        if before.channel is not None and (
            after.channel is None or after.channel.id != before.channel.id
        ):
            await self._delete_channel_if_empty(before.channel)
        # ロビーへの参加 (同じチャンネル内のミュート等の変化は除く)
        lobby_id = settings.get_temp_vc_lobby_id(member.guild.id)
        if not lobby_id:
            return
        if after.channel is None or after.channel.id != lobby_id:
            return
        if before.channel is not None and before.channel.id == lobby_id:
            return
        await self._handle_lobby_join(member, after.channel)

    async def _handle_lobby_join(
        self, member: discord.Member, lobby_channel: discord.VoiceChannel
    ):
        guild = member.guild
        key = (guild.id, member.id)
        if key in self._creating:
            return
        self._creating.add(key)
        try:
            existing = db.get_temp_vc_channel_by_owner(guild.id, member.id)
            if join_action(existing) == "reuse":
                channel = await self._fetch_channel(existing["channel_id"])
                if channel is not None:
                    try:
                        await member.move_to(
                            channel, reason="一時VC: 自分のチャンネルへ移動"
                        )
                    except (discord.Forbidden, discord.HTTPException) as error:
                        print(
                            f"⚠️ 一時VCへの移動に失敗 "
                            f"(ギルド {guild.id}, ユーザー {member.id}): {error}"
                        )
                    return
                # 記録だけ残っていた (チャンネルは消失) → 破棄して作り直す
                db.remove_temp_vc_channel(existing["channel_id"])

            try:
                channel = await guild.create_voice_channel(
                    name=temp_channel_name(member.display_name),
                    category=lobby_channel.category,
                    overwrites=public_overwrites(guild.default_role),
                    reason=f"一時VC: {member} 用に自動作成",
                )
            except (discord.Forbidden, discord.HTTPException) as error:
                print(
                    f"⚠️ 一時VCの作成に失敗 "
                    f"(ギルド {guild.id}, ユーザー {member.id}): {error}"
                )
                return
            db.add_temp_vc_channel(channel.id, guild.id, member.id, time.time())
            try:
                await member.move_to(
                    channel, reason="一時VC: 作成したチャンネルへ移動"
                )
            except (discord.Forbidden, discord.HTTPException) as error:
                print(
                    f"⚠️ 一時VCへの移動に失敗 "
                    f"(ギルド {guild.id}, ユーザー {member.id}): {error}"
                )
                # 空のまま残るので、少し置いてから空チェックを予約する
                self.bot.loop.create_task(self._delayed_empty_check(channel.id))
        finally:
            self._creating.discard(key)

    # ---------- /tempvc グループ (管理者) ----------

    tempvc_group = app_commands.Group(
        name="tempvc",
        description="一時VC管理（管理者専用）",
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

    @tempvc_group.command(
        name="lobby",
        description="一時VCのロビーにするVCを設定します（省略すると解除）",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="ロビーにするボイスチャンネル（省略すると解除）")
    async def tempvc_lobby(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel = None,
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        if channel is None:
            settings.set_value(gid, settings.TEMP_VC_LOBBY_ID, 0)
            await interaction.response.send_message(
                "✅ 一時VCのロビーを解除しました（一時VCは無効になりました）。",
                ephemeral=True,
            )
            return
        settings.set_value(gid, settings.TEMP_VC_LOBBY_ID, channel.id)
        await interaction.response.send_message(
            f"✅ 一時VCのロビーを {channel.mention} に設定しました。\n"
            "このVCに入ったメンバーの専用VCが自動で作られます。",
            ephemeral=True,
        )

    @tempvc_group.command(
        name="status", description="一時VCの現在の設定と稼働中チャンネルを表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def tempvc_status(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        lobby_id = settings.get_temp_vc_lobby_id(gid)
        rows = db.list_temp_vc_channels(gid)
        lines = [
            "ロビー: "
            + (f"<#{lobby_id}>" if lobby_id else "未設定（一時VCは無効）"),
            f"稼働中の一時VC: {len(rows)} 件",
        ]
        for row in rows[:20]:
            lines.append(
                f"- <#{row['channel_id']}>（所有者: <@{row['owner_id']}>）"
            )
        if len(rows) > 20:
            lines.append(f"- …ほか {len(rows) - 20} 件")
        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions(users=False),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVcCog(bot))
