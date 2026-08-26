"""生成应用图标：assets/icon.ico（EXE 用）+ web/public/icon.png（网页 favicon）。

视觉与前端主视觉一致：玉青→鎏金渐变圆角方章 + 墨色"演"字。
依赖 Pillow（dev extra）：uv run python build/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
SIZE = 256

# 渐变色（与 web/src/styles.css 的 --jade/--gold 一致）
JADE = (143, 206, 136)
JADE_LIGHT = (168, 217, 160)
GOLD = (216, 184, 120)
INK = (16, 20, 8)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttf",   # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",   # 黑体
]


def _load_font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise RuntimeError("找不到可用中文字体（msyh/simhei）")


def _gradient(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = 56
    # 对角渐变：左上玉青 → 右下鎏金（中点亮一档）
    for y in range(SIZE):
        for x in range(SIZE):
            t = (x + y) / (2 * SIZE)
            color = _gradient(_gradient(JADE, JADE_LIGHT, 1 - abs(t - 0.35) * 2), GOLD, t)
            img.putpixel((x, y), (*color, 255))

    # 圆角遮罩
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=radius, fill=255)
    img.putalpha(mask)

    # "演" 字（略上移，视觉居中）
    font = _load_font(int(SIZE * 0.62))
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), "演", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((SIZE - w) / 2 - bbox[0], (SIZE - h) / 2 - bbox[1] - 6), "演",
           font=font, fill=INK)

    # 内描边（金线压边，呼应面板样式）
    d.rounded_rectangle([6, 6, SIZE - 7, SIZE - 7], radius=radius - 6,
                        outline=(64, 52, 24, 160), width=3)
    return img


def main() -> None:
    icon = build_icon()
    assets = REPO / "assets"
    assets.mkdir(exist_ok=True)
    icon.save(assets / "icon.ico",
              sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    public = REPO / "web" / "public"
    public.mkdir(exist_ok=True)
    icon.save(public / "icon.png")
    print(f"生成：{assets / 'icon.ico'} 与 {public / 'icon.png'}")


if __name__ == "__main__":
    main()
