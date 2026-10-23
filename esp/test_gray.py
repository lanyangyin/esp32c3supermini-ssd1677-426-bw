# test_gray.py — GDEY0426T82 灰度测试
#
# 用法（REPL）：
#   import test_gray
#   test_gray.all()           # 依次跑完全部测试（约 70 秒）
#   test_gray.solid()         # 纯黑/白交替
#   test_gray.checker()       # 4 种棋盘
#   test_gray.levels()        # 17 级灰度条（带数字标签）
#   test_gray.gradient()      # 连续渐变
#   test_gray.partial_ghost() # 局刷残影

import time
import epd

W, H = 800, 480
ROW_BYTES = (W + 7) // 8

# Bayer 4x4 阈值矩阵，值域 0..15
BAYER4 = (
    ( 0,  8,  2, 10),
    (12,  4, 14,  6),
    ( 3, 11,  1,  9),
    (15,  7, 13,  5),
)


def _pause(msg, sec=2):
    print("  →", msg, "（停留 %d 秒）" % sec)
    time.sleep(sec)


def _fill_buf_bayer(level):
    """level: 0..16，0=全白，16=全黑。用 Bayer 4x4 抖动填充整个 buffer。"""
    buf = epd.epd.buffer
    buf[:] = b"\x00" * len(buf)
    if level <= 0:
        return
    for y in range(H):
        by = y * ROW_BYTES
        row = BAYER4[y & 3]
        for x in range(W):
            if row[x & 3] < level:
                buf[by + (x >> 3)] |= 0x80 >> (x & 7)


# ---------- 测试项 ----------

def solid():
    """纯黑 / 纯白 交替，验证基础刷屏"""
    print("[TEST] 纯黑...")
    epd.fill(1)
    epd.show()
    _pause("观察：屏幕应该全黑，无残影", 3)

    print("[TEST] 纯白...")
    epd.fill(0)
    epd.show()
    _pause("观察：屏幕应该全白，无残影", 3)


def checker():
    """1x1 / 2x2 / 4x4 / 8x8 棋盘，验证抖动分辨率"""
    for size in (1, 2, 4, 8):
        print("[TEST] %dx%d 棋盘..." % (size, size))
        epd.epd.fill(0)
        buf = epd.epd.buffer
        for y in range(H):
            by = y * ROW_BYTES
            for x in range(W):
                if ((x // size) + (y // size)) & 1:
                    buf[by + (x >> 3)] |= 0x80 >> (x & 7)
        epd.show()
        _pause("屏幕应该看起来是 50% 灰，近看是棋盘", 2)


def levels():
    """17 级灰度条，每段带数字标签"""
    print("[TEST] 17 级灰度条...")
    epd.epd.fill(0)
    buf = epd.epd.buffer

    epd.text("17-Level Gray (Bayer 4x4)", 20, 10, 1)

    n = 17
    bar_top = 70
    bar_h = 140
    margin = 20
    seg_w = (W - margin * 2) // n

    # 每段 i 的黑度 = i/16（0=全白，16=全黑）
    for i in range(n):
        x0 = margin + i * seg_w
        for y in range(bar_top, bar_top + bar_h):
            by = y * ROW_BYTES
            row = BAYER4[y & 3]
            for x in range(x0, x0 + seg_w):
                if row[x & 3] < i:
                    buf[by + (x >> 3)] |= 0x80 >> (x & 7)
        # 数字标签
        epd.text(str(i), x0 + 4, bar_top + bar_h + 10, 1)

    # 底部再画一段连续渐变做对比
    grad_top = bar_top + bar_h + 40
    grad_h = 60
    for y in range(grad_top, grad_top + grad_h):
        by = y * ROW_BYTES
        row = BAYER4[y & 3]
        for x in range(margin, W - margin):
            target = (x - margin) * 255 // (W - margin * 2)
            if row[x & 3] * 16 < target:
                buf[by + (x >> 3)] |= 0x80 >> (x & 7)
    epd.text("continuous", margin, grad_top + grad_h + 4, 1)

    epd.show()
    _pause("观察：17 段应由白到黑均匀过渡；底部渐变应平滑", 5)


def gradient():
    """4 种抖动大小的渐变对比，看哪种观感最好"""
    print("[TEST] 渐变对比...")
    epd.epd.fill(0)
    buf = epd.epd.buffer

    epd.text("Dither Size Comparison", 20, 10, 1)

    # 4 种抖动：无抖动 / 2x2 / 4x4 / 8x8
    bayer2 = ((0, 2), (3, 1))
    bayer4 = BAYER4
    bayer8 = (
        ( 0, 32,  8, 40,  2, 34, 10, 42),
        (48, 16, 56, 24, 50, 18, 58, 26),
        (12, 44,  4, 36, 14, 46,  6, 38),
        (60, 28, 52, 20, 62, 30, 54, 22),
        ( 3, 35, 11, 43,  1, 33,  9, 41),
        (51, 19, 59, 27, 49, 17, 57, 25),
        (15, 47,  7, 39, 13, 45,  5, 37),
        (63, 31, 55, 23, 61, 29, 53, 21),
    )

    configs = [
        ("none (quantize)", None, None),
        ("Bayer 2x2", bayer2, 4),
        ("Bayer 4x4", bayer4, 16),
        ("Bayer 8x8", bayer8, 64),
    ]

    y0 = 60
    band_h = 85
    margin = 20

    for idx, (label, matrix, div) in enumerate(configs):
        top = y0 + idx * band_h
        for y in range(top, top + band_h):
            by = y * ROW_BYTES
            for x in range(margin, W - margin):
                target = (x - margin) * 255 // (W - margin * 2)
                if matrix is None:
                    # 无抖动：直接阈值
                    if target > 127:
                        buf[by + (x >> 3)] |= 0x80 >> (x & 7)
                else:
                    m = matrix[y % len(matrix)][x % len(matrix)]
                    if m * (256 // div) < target:
                        buf[by + (x >> 3)] |= 0x80 >> (x & 7)
        epd.text(label, margin, top - 12, 1)

    epd.show()
    _pause("观察：无抖动的会看到明显黑/白硬边；Bayer 8x8 应该最细腻", 6)


def partial_ghost():
    """局刷残影：同一块区域反复 5 次黑/白，看是否残留轮廓"""
    print("[TEST] 局刷残影...")
    epd.fill(0)
    epd.show()
    _pause("清屏到全白", 2)

    x0, y0 = 100, 100
    x1, y1 = 300, 300

    for i in range(5):
        # 画黑块
        for y in range(y0, y1):
            by = y * ROW_BYTES
            for x in range(x0, x1):
                epd.epd.buffer[by + (x >> 3)] |= 0x80 >> (x & 7)
        epd.show_partial()

        # 擦掉
        for y in range(y0, y1):
            by = y * ROW_BYTES
            for x in range(x0, x1):
                epd.epd.buffer[by + (x >> 3)] &= ~(0x80 >> (x & 7))
        epd.show_partial()

        print("  第 %d 次局刷完成" % (i + 1))

    _pause("观察：原本黑块位置是否有残留？", 3)


# ---------- 一次跑完 ----------

def all():
    print("=" * 40)
    print(" GDEY0426T82 灰度测试开始")
    print(" 全流程约 70 秒")
    print("=" * 40)
    solid()
    checker()
    levels()
    gradient()
    partial_ghost()
    print("=" * 40)
    print(" 测试完成")
    print("=" * 40)