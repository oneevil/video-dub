#!/usr/bin/env python3
"""Генерирует иконки приложения из логотипа проекта.

Источник — favicon из app/app.html: скруглённый квадрат с диагональным
градиентом и белый треугольник «play». Рисуем геометрию заново, а не
конвертируем SVG, чтобы не тянуть cairo/librsvg ради четырёх примитивов.

    uv run --with pillow python packaging/make_icons.py

На выходе:
    packaging/macos/icon.png    1024×1024, с отступом по сетке Apple
    packaging/windows/icon.ico  16…256, без отступа
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Всё в долях от стороны — ровно как в исходном viewBox="0 0 32 32"
RADIUS = 7 / 32
TRIANGLE = ((10 / 32, 8 / 32), (10 / 32, 24 / 32), (24 / 32, 16 / 32))
COLOR_FROM = (0x10, 0xB9, 0x81)   # --accent
COLOR_TO = (0x38, 0xBD, 0xF8)     # --blue

SS = 4   # сглаживаем супersampling'ом: рисуем крупнее и уменьшаем


def _gradient(size: int) -> Image.Image:
    """Диагональный градиент из левого верхнего угла в правый нижний."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(
                round(a + (b - a) * t) for a, b in zip(COLOR_FROM, COLOR_TO)
            )
    return img


def render(size: int, inset: float = 0.0) -> Image.Image:
    """Иконка стороной size. inset — доля поля вокруг квадрата (для macOS)."""
    big = size * SS
    pad = round(big * inset)
    box = big - 2 * pad

    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # Скруглённый квадрат: градиент + маска
    mask = Image.new("L", (box, box), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, box - 1, box - 1), radius=round(box * RADIUS), fill=255
    )
    tile = _gradient(box).convert("RGBA")
    tile.putalpha(mask)
    canvas.paste(tile, (pad, pad), tile)

    # Треугольник поверх
    ImageDraw.Draw(canvas).polygon(
        [(pad + x * box, pad + y * box) for x, y in TRIANGLE],
        fill=(255, 255, 255, 255),
    )

    return canvas.resize((size, size), Image.LANCZOS)


def main():
    # macOS: по сетке Apple содержимое занимает ~82% полотна, иначе иконка
    # выглядит крупнее соседних в Dock
    mac = os.path.join(ROOT, "packaging", "macos", "icon.png")
    os.makedirs(os.path.dirname(mac), exist_ok=True)
    render(1024, inset=0.09).save(mac)
    print(f"+ {mac}")

    # Windows: без отступа — в списках и на панели задач иконка мелкая
    win = os.path.join(ROOT, "packaging", "windows", "icon.ico")
    os.makedirs(os.path.dirname(win), exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    render(256).save(win, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"+ {win} ({', '.join(str(s) for s in sizes)})")


if __name__ == "__main__":
    main()
