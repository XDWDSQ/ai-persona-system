# -*- coding: utf-8 -*-
"""从角色头像生成 Android 应用图标（mipmap PNG，含圆角方形 + 圆形两套）。"""
import os
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "..", "xiaoni-ai-persona", "pages", "avatar_dashuai.jpg")
OUT = os.path.join(BASE, "app", "src", "main", "res")

# 各密度的启动器图标尺寸（px）
SIZES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def rounded_square(img: Image.Image, size: int, radius_ratio: float = 0.22) -> Image.Image:
    """按指定尺寸生成圆角方形图标（带 8% 内边距与背景色）。"""
    canvas = Image.new("RGBA", (size, size), (10, 10, 10, 255))
    side = int(size * 0.84)
    thumb = img.resize((side, side), Image.LANCZOS)
    radius = int(size * radius_ratio)
    mask = Image.new("L", (side, side), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, side - 1, side - 1], radius=radius, fill=255)
    canvas.paste(thumb, ((size - side) // 2, (size - side) // 2), mask)
    return canvas


def circle(img: Image.Image, size: int) -> Image.Image:
    """按指定尺寸生成圆形图标。"""
    side = int(size * 0.84)
    thumb = img.resize((side, side), Image.LANCZOS)
    mask = Image.new("L", (side, side), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([0, 0, side - 1, side - 1], fill=255)
    canvas = Image.new("RGBA", (size, size), (10, 10, 10, 255))
    canvas.paste(thumb, ((size - side) // 2, (size - side) // 2), mask)
    return canvas


def main():
    src = Image.open(SRC).convert("RGB")
    # 居中方形裁剪（头像一般为竖构图，裁成方形）
    w, h = src.size
    side = min(w, h)
    src = src.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))

    for folder, size in SIZES.items():
        os.makedirs(os.path.join(OUT, folder), exist_ok=True)
        rounded_square(src, size).save(
            os.path.join(OUT, folder, "ic_launcher.png"))
        circle(src, size).save(
            os.path.join(OUT, folder, "ic_launcher_round.png"))
        print(f"{folder}: {size}px OK")


if __name__ == "__main__":
    main()
