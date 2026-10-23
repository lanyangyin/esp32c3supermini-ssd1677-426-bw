# e6_image_core/dithering.py — 黑白墨水屏图像抖动核心

import math
from PIL import Image

# ---------------- 基础常量 ----------------

W, H = 800, 480
FRAME_BYTES = W * H // 8  # 48000 字节

# 去除了六色，只保留黑白
PALETTES = {
    "bw": [
        {"name": "black", "color": "#000000", "deviceColor": "#000000", "index": 0},
        {"name": "white", "color": "#FFFFFF", "deviceColor": "#FFFFFF", "index": 1}
    ]
}

DITHER_MODES = [
    ("Floyd-Steinberg", "floydSteinberg"),
    ("Atkinson", "atkinson"),
    ("False Floyd-Steinberg", "falseFloydSteinberg"),
    ("Jarvis", "jarvis"),
    ("Jarvis-Judice-Ninke", "jarvisJudiceNinke"),
    ("Stucki", "stucki"),
    ("Burkes", "burkes"),
    ("Sierra-3", "sierra3"),
    ("Sierra-2", "sierra2"),
    ("Sierra-2-4A", "sierra2-4a"),
    ("Fan", "fan"),
    ("Shiau-Fan", "shiauFan"),
    ("Shiau-Fan 2", "shiauFan2"),
    ("有序", "ordered"),
    ("仅量化", "quantizationOnly")
]

KERNELS = {
    'floydSteinberg': [[1, 0, 7 / 16], [-1, 1, 3 / 16], [0, 1, 5 / 16], [1, 1, 1 / 16]],
    'atkinson': [[1, 0, 1 / 8], [2, 0, 1 / 8], [-1, 1, 1 / 8], [0, 1, 1 / 8], [1, 1, 1 / 8], [0, 2, 1 / 8]],
    'falseFloydSteinberg': [[1, 0, 3 / 8], [0, 1, 3 / 8], [1, 1, 2 / 8]],
    'burkes': [[1, 0, 8 / 32], [2, 0, 4 / 32], [-2, 1, 2 / 32], [-1, 1, 4 / 32], [0, 1, 8 / 32], [1, 1, 4 / 32],
               [2, 1, 2 / 32], [-2, 2, 1 / 32], [-1, 2, 2 / 32], [0, 2, 4 / 32], [1, 2, 2 / 32], [2, 2, 1 / 32]],
    'stucki': [[1, 0, 8 / 42], [2, 0, 4 / 42], [-2, 1, 2 / 42], [-1, 1, 4 / 42], [0, 1, 8 / 42], [1, 1, 4 / 42],
               [2, 1, 2 / 42], [-2, 2, 1 / 42], [-1, 2, 2 / 42], [0, 2, 4 / 42], [1, 2, 2 / 42], [2, 2, 1 / 42]],
    'jarvis': [[1, 0, 7 / 48], [2, 0, 5 / 48], [-2, 1, 3 / 48], [-1, 1, 5 / 48], [0, 1, 7 / 48], [1, 1, 5 / 48],
               [2, 1, 3 / 48], [-2, 2, 1 / 48], [-1, 2, 3 / 48], [0, 2, 5 / 48], [1, 2, 3 / 48], [2, 2, 1 / 48]],
    'jarvisJudiceNinke': [[1, 0, 7 / 48], [2, 0, 5 / 48], [-2, 1, 3 / 48], [-1, 1, 5 / 48], [0, 1, 7 / 48],
                          [1, 1, 5 / 48], [2, 1, 3 / 48], [-2, 2, 1 / 48], [-1, 2, 3 / 48], [0, 2, 5 / 48],
                          [1, 2, 3 / 48], [2, 2, 1 / 48]],
    'sierra3': [[1, 0, 5 / 32], [2, 0, 3 / 32], [-2, 1, 2 / 32], [-1, 1, 4 / 32], [0, 1, 5 / 32], [1, 1, 4 / 32],
                [2, 1, 2 / 32], [-1, 2, 2 / 32], [0, 2, 3 / 32], [1, 2, 2 / 32]],
    'sierra2': [[1, 0, 4 / 16], [2, 0, 3 / 16], [-2, 1, 1 / 16], [-1, 1, 2 / 16], [0, 1, 3 / 16], [1, 1, 2 / 16],
                [2, 1, 1 / 16]],
    'sierra2-4a': [[1, 0, 2 / 4], [-1, 1, 1 / 4], [0, 1, 1 / 4]],
    'fan': [[1, 0, 7 / 16], [-2, 1, 1 / 16], [-1, 1, 3 / 16], [0, 1, 5 / 16]],
    'shiauFan': [[1, 0, 4 / 8], [-2, 1, 1 / 8], [-1, 1, 1 / 8], [0, 1, 2 / 8]],
    'shiauFan2': [[1, 0, 7 / 14], [-3, 1, 1 / 14], [-2, 1, 1 / 14], [-1, 1, 2 / 14], [0, 1, 3 / 14]]
}


# ---------------- 辅助函数 ----------------

def hex_to_rgb(hex_str):
    """'#RRGGBB' 转 (r, g, b)"""
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))


def palette_to_rgb(palette):
    """提取调色板的 RGB 列表"""
    return [hex_to_rgb(p["color"]) for p in palette]


def palette_to_device_rgb(palette):
    """提取设备色的 RGB 列表"""
    return [hex_to_rgb(p["deviceColor"]) for p in palette]


def palette_to_indices(palette):
    """提取索引值，这里为了兼容 1bpp，直接返回 0 和 1"""
    # 注意：原版用来查表映射 4bpp，现在只用 0(黑) 和 1(白)
    # 如果调色板顺序固定，其实无需映射
    return [p.get("index", i) for i, p in enumerate(palette)]


def make_bayer(size):
    """动态生成 Bayer 矩阵"""
    if size <= 1: return [[0]]
    if size == 2: return [[0, 2], [3, 1]]
    half = size // 2
    sub = make_bayer(half)
    m = [[0] * size for _ in range(size)]
    for y in range(half):
        for x in range(half):
            v = sub[y][x] * 4
            m[y][x] = v
            m[y][x + half] = v + 2
            m[y + half][x] = v + 3
            m[y + half][x + half] = v + 1
    return m


# ---------------- 核心抖动算法 ----------------

def dither(img, mode="floydSteinberg", serpentine=True):
    """
    黑白抖动算法。
    输入：PIL Image
    输出：二维数组（0=黑，1=白），尺寸为 480x800（根据传入的图像尺寸）
    """
    img_l = img.convert("L")
    w, h = img_l.size
    pixels = list(img_l.getdata())
    block = [[1] * w for _ in range(h)]  # 默认全白

    # 1. 仅量化（阈值二值化）
    if mode == "quantizationOnly":
        for y in range(h):
            for x in range(w):
                block[y][x] = 0 if pixels[y * w + x] < 128 else 1
        return block

    # 2. 有序抖动
    if mode == "ordered":
        M = make_bayer(8)
        div = 64
        for y in range(h):
            for x in range(w):
                val = pixels[y * w + x] + (M[y % 8][x % 8] / div - 0.5) * 255
                block[y][x] = 0 if max(0, min(255, val)) < 128 else 1
        return block

    # 3. 误差扩散
    kernel = KERNELS.get(mode, KERNELS['floydSteinberg'])

    # 转为 float 类型以累积误差
    buf = [float(p) for p in pixels]

    for y in range(h):
        reverse = serpentine and (y % 2 == 1)
        for xi in range(w):
            x = (w - 1 - xi) if reverse else xi
            i = y * w + x
            old_val = buf[i]
            new_val = 0 if old_val < 128 else 255
            block[y][x] = 0 if new_val == 0 else 1
            err = old_val - new_val

            for dx, dy, wt in kernel:
                nx = x - dx if reverse else x + dx
                ny = y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    buf[ny * w + nx] += err * wt

    return block


# ---------------- 1bpp 打包与解包 ----------------

def pack(block):
    """
    将二维黑白数组（0=黑，1=白）打包为 1bpp 字节流（48000 字节）。
    注意：驱动（SSD1677）端通常 1 表示黑，0 表示白。
    """
    h = len(block)
    w = len(block[0]) if h > 0 else 0
    row_bytes = (w + 7) // 8
    buf = bytearray(row_bytes * h)

    for y in range(h):
        row_base = y * row_bytes
        for x in range(w):
            if block[y][x] == 0:  # 0 表示黑
                buf[row_base + (x >> 3)] |= (0x80 >> (x & 7))
    return buf


def block_to_image(block, rgb_list=None):
    """
    将二维黑白数组转回 PIL Image 用于预览。
    """
    h = len(block)
    w = len(block[0]) if h > 0 else 0
    img = Image.new("1", (w, h))  # 1-bit 模式
    pixels = []
    for y in range(h):
        for x in range(w):
            # PIL 的 1-bit 模式中 0 为黑，255 为白
            pixels.append(0 if block[y][x] == 0 else 255)

    img.putdata(pixels)
    return img.convert("RGB")


def quantize(img, device_colors=None):
    """
    简单量化：直接读取图像亮度转为 0/1 数组。
    用于 PC 端工具从已有的黑白图像生成 block。
    """
    img_l = img.convert("L")
    w, h = img_l.size
    pixels = list(img_l.getdata())
    block = [[1] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            block[y][x] = 0 if pixels[y * w + x] < 128 else 1
    return block