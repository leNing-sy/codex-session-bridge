#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 session-convert.exe 的图标 (纯标准库, 无 Pillow).

图案: 深蓝圆角方块 + 两个白色对向箭头 (表示会话双向转换)。
输出 PNG-in-ICO (Vista+ 支持), 256x256。

用法: python assets/make_icon.py  -> 生成 assets/icon.ico
"""

import os
import struct
import zlib

SIZE = 256
BG = (36, 82, 138, 255)      # 深蓝
FG = (255, 255, 255, 255)    # 白
TRANSPARENT = (0, 0, 0, 0)
RADIUS = 44                  # 圆角半径


def inside_rounded_rect(x, y):
    margin = 12
    lo, hi = margin, SIZE - 1 - margin
    if x < lo or x > hi or y < lo or y > hi:
        return False
    r = RADIUS
    for cx, cy in ((lo + r, lo + r), (hi - r, lo + r),
                   (lo + r, hi - r), (hi - r, hi - r)):
        if (x < lo + r or x > hi - r) and (y < lo + r or y > hi - r):
            if abs(x - cx) <= r or abs(y - cy) <= r:
                pass
    # 四角判定: 落在角落方块内但在圆外 -> 不属于圆角矩形
    if x < lo + r and y < lo + r:
        return (x - (lo + r)) ** 2 + (y - (lo + r)) ** 2 <= r * r
    if x > hi - r and y < lo + r:
        return (x - (hi - r)) ** 2 + (y - (lo + r)) ** 2 <= r * r
    if x < lo + r and y > hi - r:
        return (x - (lo + r)) ** 2 + (y - (hi - r)) ** 2 <= r * r
    if x > hi - r and y > hi - r:
        return (x - (hi - r)) ** 2 + (y - (hi - r)) ** 2 <= r * r
    return True


def inside_arrow_right(x, y):
    """上方箭头, 指向右: 杆 + 三角头"""
    if 88 <= y <= 112 and 52 <= x <= 158:
        return True
    if 158 <= x <= 204:
        half = (204 - x) * 36 // 46  # 头部渐窄
        return 100 - half - 12 <= y <= 100 + half + 12 and abs(y - 100) <= (204 - x)
    return False


def inside_arrow_left(x, y):
    """下方箭头, 指向左"""
    if 144 <= y <= 168 and 98 <= x <= 204:
        return True
    if 52 <= x <= 98:
        return abs(y - 156) <= (x - 52)
    return False


def build_pixels():
    rows = []
    for y in range(SIZE):
        row = bytearray()
        for x in range(SIZE):
            if inside_arrow_right(x, y) or inside_arrow_left(x, y):
                px = FG if inside_rounded_rect(x, y) else TRANSPARENT
            elif inside_rounded_rect(x, y):
                px = BG
            else:
                px = TRANSPARENT
            row.extend(px)
        rows.append(bytes(row))
    return rows


def make_png(rows):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # RGBA8
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def make_ico(png):
    # ICONDIR + 1 个 ICONDIRENTRY, 宽高 256 记 0
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    png = make_png(build_pixels())
    with open(out, "wb") as f:
        f.write(make_ico(png))
    print("生成:", out, "(%d bytes)" % os.path.getsize(out))


if __name__ == "__main__":
    main()
