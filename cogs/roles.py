"""セルフ付与ロールパネル (リアクションロール)。

管理者が /roles add で登録したロールをボタンとして並べたパネルを投稿し、
メンバーがボタンを押すたびにそのロールの付け外し (トグル) ができる。

永続化の方式:
  ボタンは discord.ui.DynamicItem で実装し、custom_id は
  `v2:role:<role_id>`。ロールは実行時に設定されるため固定の custom_id では
  足りず、テンプレートで受けるこの方式が discord.py 2.7 の正道。
  bot.py の setup_hook で bot.add_dynamic_items(RoleButton) を登録するので、
  Bot 再起動後も既存パネルのボタンがそのまま動く。
  クリック時はその時点のギルド設定を引き直すため、設定から外された
  ロールの古いボタンは「もう使えない」と返して何もしない。
"""
import re

import discord
from discord import app_commands
from discord.ext import commands

from app import settings
from app.ui import COLOR_PRIMARY, base_embed, reply_error

CUSTOM_ID_TEMPLATE = r"v2:role:(?P<role_id>[0-9]+)"
MAX_PANEL_ROLES = settings.MAX_ROLE_PANEL_ROLES


def toggle_actions(member_role_ids, panel_role_ids, clicked_role_id, exclusive):
    """ボタンクリック時の付け外しを決める純粋関数。

    戻り値は (to_add, to_remove) のロール ID リスト。
    - クリックしたロールがパネル設定に無ければ何もしない。
    - 既に持っていれば外すだけ (排他モードでも他は触らない)。
    - 持っていなければ付ける。排他モードでは、他に持っている
      パネルロールをすべて外す (パネル外のロールは決して触らない)。
    """
    panel = list(panel_role_ids or [])
    if clicked_role_id not in panel:
        return [], []
    member = set(member_role_ids or [])
    if clicked_role_id in member:
        return [], [clicked_role_id]
    to_add = [clicked_role_id]
    to_remove = []
    if exclusive:
        to_remove = [
            role_id
            for role_id in panel
            if role_id != clicked_role_id and role_id in member
        ]
    return to_add, to_remove


def panel_add_error(role_ids, new_role_id, max_roles=MAX_PANEL_ROLES):
    """パネルへのロール追加可否。問題があればキー、なければ None。

    キー: 'duplicate' (登録済み) / 'full' (上限到達)。
    ロール自体の性質 (@everyone・管理ロール・階層) のチェックは
    discord のオブジェクトが要るのでコマンド側で行う。
    """
    ids = list(role_ids or [])
    if new_role_id in ids:
        return "duplicate"
    if len(ids) >= max_roles:
        return "full"
    return None


async def handle_role_toggle(interaction: discord.Interaction, role_id: int):
    """ロールボタンのクリック処理 (DynamicItem の callback から呼ばれる)。"""
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        await reply_error(interaction, "この操作はサーバー内でのみ使えます。")
        return
    if member.bot:
        return

    # クリック時点の設定を引き直す (設定から外れたロールのボタンは無効)
    panel_ids = settings.get_role_panel_role_ids(guild.id)
    if role_id not in panel_ids:
        await reply_error(
            interaction,
            "このロールは現在のパネル設定に含まれていないため、使えなくなりました。",
        )
        return

    role = guild.get_role(role_id)
    if role is None:
        await reply_error(
            interaction, "対象のロールが見つかりません（削除された可能性があります）。"
        )
        return
    if role.is_default():
        await reply_error(interaction, "@everyone ロールは付け外しできません。")
        return
    if role.managed:
        await reply_error(
            interaction, "連携・Bot 管理ロールは付け外しできません。"
        )
        return
    me = guild.me
    if me is not None and role >= me.top_role:
        await reply_error(
            interaction,
            "Bot より上位のロールのため付け外しできません。管理者に確認してください。",
        )
        return

    exclusive = settings.get_role_panel_exclusive(guild.id)
    to_add, to_remove = toggle_actions(
        [r.id for r in member.roles], panel_ids, role_id, exclusive
    )

    try:
        if to_remove:
            remove_roles = [
                r for r in (guild.get_role(rid) for rid in to_remove) if r
            ]
            if remove_roles:
                await member.remove_roles(
                    *remove_roles, reason="ロールパネル (付け替え)"
                )
        if to_add:
            await member.add_roles(role, reason="ロールパネル")
    except discord.Forbidden:
        await reply_error(
            interaction, "Botにロールを付け外しする権限がありません。管理者に確認してください。"
        )
        return
    except discord.HTTPException:
        await reply_error(interaction, "ロールの付け外し中にエラーが発生しました。")
        return

    if to_add:
        text = f"✅ {role.mention} を付けました。"
        if to_remove:
            removed_mentions = " ".join(
                f"<@&{rid}>" for rid in to_remove
            )
            text += f"\n（排他モードのため {removed_mentions} を外しました）"
        await interaction.response.send_message(text, ephemeral=True)
    else:
        await interaction.response.send_message(
            f"✅ {role.mention} を外しました。", ephemeral=True
        )


class RoleButton(
    discord.ui.DynamicItem[discord.ui.Button], template=CUSTOM_ID_TEMPLATE
):
    """ロール ID を custom_id に埋め込む動的ボタン。

    パネル投稿時はこのクラス自体を View に入れて送る。再起動後の
    クリックは bot.add_dynamic_items の登録を経由して from_custom_id で
    再生成され、callback が呼ばれる。
    """

    def __init__(self, role_id: int, label: str = "ロール"):
        super().__init__(
            discord.ui.Button(
                label=(label or "ロール")[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"v2:role:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match,
        /,
    ):
        label = getattr(item, "label", None) or "ロール"
        return cls(int(match["role_id"]), label=label)

    async def callback(self, interaction: discord.Interaction):
        await handle_role_toggle(interaction, self.role_id)


def build_role_panel(guild: discord.Guild):
    """(embed, view) を返す。有効なロールが1つも無ければ None。

    削除済みロールは Embed・ボタンどちらからも除外する。
    """
    roles = []
    for role_id in settings.get_role_panel_role_ids(guild.id):
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    if not roles:
        return None

    lines = [
        "ボタンを押すと、そのロールの付け外しができます。",
        "もう一度押すと外れます。",
        "",
    ]
    if settings.get_role_panel_exclusive(guild.id):
        lines.append("※ 排他モード: 付けられるのはこのパネルのうち1つだけです。")
        lines.append("")
    lines += [f"{role.mention}" for role in roles]

    embed = base_embed(
        "🎭 ロール選択", "\n".join(lines), color=COLOR_PRIMARY
    )
    view = discord.ui.View(timeout=None)
    for role in roles:
        view.add_item(RoleButton(role.id, label=role.name))
    return embed, view


class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- /roles グループ (管理者) ----------

    roles_group = app_commands.Group(
        name="roles",
        description="ロールパネル管理（管理者専用）",
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

    @roles_group.command(
        name="add", description="ロールパネルにロールを追加します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(role="パネルに追加するロール")
    async def roles_add(
        self, interaction: discord.Interaction, role: discord.Role
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        if role.is_default():
            await reply_error(interaction, "@everyone ロールは追加できません。")
            return
        if role.managed:
            await reply_error(
                interaction, "連携・Bot 管理ロールは追加できません。"
            )
            return
        me = guild.me
        if me is not None and role >= me.top_role:
            await reply_error(
                interaction,
                "Bot より上位のロールは追加できません（付け外しできないため）。",
            )
            return
        gid = guild.id
        role_ids = settings.get_role_panel_role_ids(gid)
        error = panel_add_error(role_ids, role.id)
        if error == "duplicate":
            await reply_error(
                interaction, f"{role.mention} はすでにパネルに登録されています。"
            )
            return
        if error == "full":
            await reply_error(
                interaction,
                f"パネルのロールは最大 {MAX_PANEL_ROLES} 件までです。"
                "先に /roles remove で外してください。",
            )
            return
        role_ids.append(role.id)
        settings.set_value(gid, settings.ROLE_PANEL_ROLE_IDS, role_ids)
        await interaction.response.send_message(
            f"✅ {role.mention} をパネルに追加しました（現在 {len(role_ids)} 件）。"
            "\nパネルを貼り直すには /roles panel を実行してください"
            "（既存のパネルにも新しいボタンは追加されません）。",
            ephemeral=True,
        )

    @roles_group.command(
        name="remove", description="ロールパネルからロールを外します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(role="パネルから外すロール")
    async def roles_remove(
        self, interaction: discord.Interaction, role: discord.Role
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        gid = interaction.guild.id
        role_ids = settings.get_role_panel_role_ids(gid)
        if role.id not in role_ids:
            await reply_error(
                interaction, f"{role.mention} はパネルに登録されていません。"
            )
            return
        role_ids.remove(role.id)
        settings.set_value(gid, settings.ROLE_PANEL_ROLE_IDS, role_ids)
        await interaction.response.send_message(
            f"✅ {role.mention} をパネルから外しました（現在 {len(role_ids)} 件）。"
            "\nすでにこのロールを持っているメンバーからは外れません。"
            "既存パネルのボタンは押しても使えなくなります。",
            ephemeral=True,
        )

    @roles_group.command(
        name="list", description="パネルに登録中のロールを表示します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roles_list(self, interaction: discord.Interaction):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        gid = guild.id
        role_ids = settings.get_role_panel_role_ids(gid)
        exclusive = settings.get_role_panel_exclusive(gid)
        lines = [
            f"ロールパネルの設定（{len(role_ids)} 件 / 最大 {MAX_PANEL_ROLES} 件）",
            f"排他モード: {'**ON**（付けられるのは1つだけ）' if exclusive else 'OFF'}",
        ]
        if role_ids:
            lines.append("")
            for index, role_id in enumerate(role_ids, start=1):
                role = guild.get_role(role_id)
                lines.append(
                    f"{index}. {role.mention if role else f'(削除済みロール: {role_id})'}"
                )
        else:
            lines.append("\nロールはまだ登録されていません。/roles add で追加してください。")
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True
        )

    @roles_group.command(
        name="exclusive",
        description="排他モード（パネルのロールを1つだけ付ける）の有効/無効を切り替えます",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(enabled="排他モードを有効にするか")
    async def roles_exclusive(
        self, interaction: discord.Interaction, enabled: bool
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        settings.set_value(
            interaction.guild.id, settings.ROLE_PANEL_EXCLUSIVE, enabled
        )
        await interaction.response.send_message(
            "✅ 排他モードを "
            + (
                "**有効** にしました。パネルのロールを付けると、他のパネルロールが外れます。"
                if enabled
                else "**無効** にしました。パネルのロールは複数付けられます。"
            ),
            ephemeral=True,
        )

    @roles_group.command(
        name="panel", description="ロールパネルを指定チャンネルに投稿します"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="投稿先チャンネル（省略時はこのチャンネル）")
    async def roles_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
    ):
        if not self._guard_admin_guild(interaction):
            await self._reject(interaction)
            return
        guild = interaction.guild
        built = build_role_panel(guild)
        if built is None:
            await reply_error(
                interaction,
                "パネルに表示できるロールがありません。"
                "先に /roles add でロールを追加してください。",
            )
            return
        embed, view = built
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await reply_error(interaction, "テキストチャンネルを指定してください。")
            return
        try:
            await target.send(embed=embed, view=view)
        except discord.Forbidden:
            await reply_error(
                interaction,
                f"{target.mention} に投稿する権限が Bot にありません。",
            )
            return
        except discord.HTTPException:
            await reply_error(interaction, "パネルの投稿中にエラーが発生しました。")
            return
        await interaction.response.send_message(
            f"✅ ロールパネルを {target.mention} に投稿しました。"
            "\n何度でも貼り直せます。古いパネルのボタンもそのまま使えます。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RolesCog(bot))
