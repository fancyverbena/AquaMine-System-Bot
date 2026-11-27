"""ランクカード画像の生成 (Pillow)。

/rank の内容 (レベル・順位・総XP・次レベルまでの進捗) を 900x280 の
PNG カードとして描く。Discord オブジェクトには依存せず、プリミティブと
アバター画像のバイト列だけを受け取るので単体テストしやすい。
フォントが見つからない環境でも PIL のデフォルトフォントで描画を続ける
(例外で落とさない)。
"""
import glob
import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .ui import COLOR_GOLD, COLOR_PRIMARY

CARD_WIDTH = 900
CARD_HEIGHT = 280
AVATAR_SIZE = 180
AVATAR_XY = (50, 50)
TEXT_X = 270
TEXT_RIGHT = 850

# 日本語フォントの探索先 (前にあるものほど優先)
_FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _repo_font_candidates(bold: bool):
    """リポジトリ同梱フォント (assets/fonts/) の候補を返す。"""
    fonts_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir, "assets", "fonts"
    )
    patterns = ("*.ttf", "*.ttc", "*.otf")
    found = []
    for pattern in patterns:
        found.extend(sorted(glob.glob(os.path.join(fonts_dir, pattern))))
    if bold:
        bold_first = [p for p in found if "bold" in os.path.basename(p).lower()]
        return bold_first + [p for p in found if p not in bold_first]
    return found


def _font_paths(bold: bool):
    system = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR
    return _repo_font_candidates(bold) + list(system)


@lru_cache(maxsize=32)
def load_font(size: int, bold: bool = False):
    """指定サイズのフォントを返す。何があっても例外を投げない。

    探索順: リポジトリ同梱 (assets/fonts/) → システムの Noto CJK →
    DejaVu → PIL のデフォルトフォント。
    """
    for path in _font_paths(bold):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # 古い Pillow は size 引数なし
        return ImageFont.load_default()


def _rgb(color) -> tuple:
    return (color.r, color.g, color.b)


def _darken(rgb: tuple, factor: float) -> tuple:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


_PRIMARY = _rgb(COLOR_PRIMARY)
_GOLD = _rgb(COLOR_GOLD)
_BG = _darken(_PRIMARY, 0.16)          # 濃紺寄りの暗い背景
_PANEL = _darken(_PRIMARY, 0.30)       # アバター裏などの少し明るい面
_BAR_BG = (52, 54, 66)
_TEXT = (255, 255, 255)
_SUBTEXT = (178, 182, 196)


def _circle_avatar(avatar_bytes, display_name: str) -> Image.Image:
    """アバターを円形に切り抜いた RGBA 画像を返す。

    バイト列が無い・壊れている場合は、表示名の頭文字を描いた
    プレースホルダー円で代替する (生成自体は失敗させない)。
    """
    size = AVATAR_SIZE
    if avatar_bytes:
        try:
            src = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            # 中央を正方形に切り取ってからリサイズ
            side = min(src.width, src.height)
            left = (src.width - side) // 2
            top = (src.height - side) // 2
            src = src.crop((left, top, left + side, top + side))
            src = src.resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
            out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            out.paste(src, (0, 0), mask)
            return out
        except Exception:
            pass  # 落ちたらプレースホルダーにフォールバック

    placeholder = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(placeholder)
    draw.ellipse((0, 0, size, size), fill=_PRIMARY + (255,))
    initial = (display_name or "?").strip()[:1] or "?"
    font = load_font(84, bold=True)
    draw.text(
        (size / 2, size / 2), initial, font=font, fill=_TEXT + (255,),
        anchor="mm",
    )
    return placeholder


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int,
              start_size: int, bold: bool = True):
    """幅に収まるまでフォントサイズを縮める。(フォント, 表示文字列) を返す。"""
    size = start_size
    while size > 20:
        font = load_font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font, text
        size -= 2
    font = load_font(20, bold=bold)
    trimmed = text
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_width:
        trimmed = trimmed[:-1]
    return font, (trimmed + "…") if trimmed else "…"


def generate_rank_card(
    *,
    display_name: str,
    level: int,
    rank_position: int,
    total_xp: int,
    fraction: float,
    remaining_xp: int,
    next_level: int,
    next_threshold: int,
    avatar_bytes: bytes = None,
) -> bytes:
    """ランクカード PNG のバイト列を生成する。

    fraction は次レベルまでの進捗率 (0.0〜1.0)。level_progress() の
    戻り値をそのまま渡す想定。
    """
    fraction = max(0.0, min(1.0, fraction))
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BG)
    draw = ImageDraw.Draw(card, "RGBA")

    # 上部のアクセントバーとアバター背面のパネル
    draw.rectangle((0, 0, CARD_WIDTH, 8), fill=_GOLD)
    ax, ay = AVATAR_XY
    draw.rounded_rectangle(
        (ax - 10, ay - 10, ax + AVATAR_SIZE + 10, ay + AVATAR_SIZE + 10),
        radius=24, fill=_PANEL,
    )
    avatar_img = _circle_avatar(avatar_bytes, display_name)
    card.paste(avatar_img, (ax, ay), avatar_img)

    max_text_width = TEXT_RIGHT - TEXT_X
    # 表示名
    name_font, name_text = _fit_font(
        draw, display_name or "?", max_text_width, 46, bold=True
    )
    draw.text((TEXT_X, 42), name_text, font=name_font, fill=_TEXT)

    # レベルと順位
    level_font = load_font(36, bold=True)
    draw.text(
        (TEXT_X, 108), f"レベル {level}", font=level_font, fill=_GOLD
    )
    level_width = draw.textlength(f"レベル {level}", font=level_font)
    rank_font = load_font(30)
    draw.text(
        (TEXT_X + level_width + 24, 114),
        f"サーバー内 #{rank_position} 位",
        font=rank_font, fill=_SUBTEXT,
    )

    # 進捗バー
    bar_x0, bar_y0 = TEXT_X, 176
    bar_x1, bar_y1 = TEXT_RIGHT, 208
    draw.rounded_rectangle(
        (bar_x0, bar_y0, bar_x1, bar_y1), radius=16, fill=_BAR_BG
    )
    if fraction > 0:
        fill_x1 = bar_x0 + (bar_x1 - bar_x0) * fraction
        # 丸みを保つため最小幅を確保する
        fill_x1 = max(fill_x1, bar_x0 + 32)
        draw.rounded_rectangle(
            (bar_x0, bar_y0, fill_x1, bar_y1), radius=16, fill=_GOLD
        )

    # XP 詳細
    detail_font = load_font(24)
    percent = int(fraction * 100)
    draw.text(
        (TEXT_X, 224),
        f"総XP {total_xp:,} XP（{percent}%）",
        font=detail_font, fill=_TEXT,
    )
    right_text = f"次の Lv.{next_level} まで あと {remaining_xp:,} XP"
    right_width = draw.textlength(right_text, font=detail_font)
    draw.text(
        (TEXT_RIGHT - right_width, 224), right_text,
        font=detail_font, fill=_SUBTEXT,
    )
    # 次レベルの必要累計 XP (小さく添える)
    threshold_font = load_font(20)
    draw.text(
        (TEXT_X, 252),
        f"Lv.{next_level} 到達には累計 {next_threshold:,} XP",
        font=threshold_font, fill=_SUBTEXT,
    )

    buffer = io.BytesIO()
    card.save(buffer, format="PNG")
    return buffer.getvalue()
