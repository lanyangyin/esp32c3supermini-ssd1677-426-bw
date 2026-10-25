# draw_test.py — 独立渲染测试（REPL: import draw_test）
#
# 用法：
#   >>> import draw_test
#   >>> draw_test.show()                          # 用默认字模 + 默认文本
#   >>> draw_test.show("中文测试")                # 自定义文本
#   >>> draw_test.show("你好", size=48)           # 指定字号
#   >>> draw_test.show("你好", family="SmileySans-Oblique", size=32, fg=0, bg=15)
#
# 自动探测可用字模：优先 SD，否则 Flash。

import gc
import os
import epd
from text_renderer import render_full

# ---------- 探测 ----------

SD_FONT_DIR = '/sd/fonts'
FLASH_FONT_DIR = '/www/fonts'


def _scan_dir(dirpath):
    try:
        return sorted(os.listdir(dirpath))
    except OSError:
        return []


def list_families():
    """返回 [(storage, family, [sizes...]), ...]"""
    out = []
    for dirpath, storage in ((SD_FONT_DIR, 'sd'), (FLASH_FONT_DIR, 'flash')):
        names = _scan_dir(dirpath)
        if not names:
            continue
        groups = {}
        for n in names:
            if not n.endswith('.bin'):
                continue
            base = n[:-4]
            parts = base.rsplit('_', 2)
            if len(parts) == 3:
                try:
                    family = parts[0]
                    size = int(parts[1])
                except ValueError:
                    continue
            elif len(parts) == 2:
                try:
                    family = parts[0]
                    size = int(parts[1])
                except ValueError:
                    # font_32.bin -> family='font', size=32
                    if base.startswith('font_'):
                        family = 'font'
                        try:
                            size = int(base[5:])
                        except ValueError:
                            continue
                    else:
                        continue
            else:
                # font_32.bin 这种老格式
                if base.startswith('font_'):
                    family = 'font'
                    try:
                        size = int(base[5:])
                    except ValueError:
                        continue
                else:
                    continue
            groups.setdefault(family, set()).add(size)
        for family, sizes in groups.items():
            out.append((storage, family, sorted(sizes)))
    return out


def find_paths(family=None, size=None):
    """找到指定 family/size 的所有 chunk 路径。返回 (paths, storage) 或 (None, None)。"""
    families = list_families()
    if not families:
        return None, None

    # 优先 SD
    families.sort(key=lambda x: (x[0] != 'sd', x[1]))

    for storage, fam, sizes in families:
        if family and fam != family:
            continue
        if size is not None and size not in sizes:
            continue
        target_size = size if size is not None else (32 if 32 in sizes else sizes[-1])
        if target_size not in sizes:
            continue
        dirpath = SD_FONT_DIR if storage == 'sd' else FLASH_FONT_DIR
        names = _scan_dir(dirpath)
        # 收集该 family/size 的所有 chunk
        chunk_files = []
        for n in names:
            if not n.endswith('.bin'):
                continue
            base = n[:-4]
            parts = base.rsplit('_', 2)
            if len(parts) == 3:
                try:
                    if parts[0] == fam and int(parts[1]) == target_size:
                        chunk_files.append((int(parts[2]), n))
                except ValueError:
                    pass
            elif len(parts) == 2:
                try:
                    if parts[0] == fam and int(parts[1]) == target_size:
                        chunk_files.append((0, n))
                except ValueError:
                    pass
            else:
                if fam == 'font' and base == 'font_%d' % target_size:
                    chunk_files.append((0, n))
        if not chunk_files:
            continue
        chunk_files.sort()
        paths = [dirpath + '/' + n for _, n in chunk_files]
        return paths, storage
    return None, None


# ---------- 渲染 ----------

def show(text="Hello 16-Level Gray!\nESP32 SSD1677 OK",
         family=None, size=None, direction=0,
         align='center', valign='center',
         fg=0, bg=15):
    paths, storage = find_paths(family=family, size=size)
    if not paths:
        print('[draw_test] 未找到可用字模。请先运行 draw_test.list_families() 查看。')
        return False

    print('[draw_test] 使用 %s 字模，%d 个 chunk：' % (storage, len(paths)))
    for p in paths:
        try:
            print('   %s  %d B' % (p, os.stat(p)[6]))
        except OSError:
            print('   %s  (缺失)' % p)
            return False

    # 从路径推断 size
    if size is None:
        # 尝试从文件名解析
        base = paths[0].rsplit('/', 1)[-1][:-4]
        parts = base.rsplit('_', 2)
        if len(parts) >= 2:
            try:
                size = int(parts[1])
            except ValueError:
                size = 32
        else:
            size = 32

    gc.collect()
    print('[draw_test] 开始渲染 (size=%d, fg=%d, bg=%d)…' % (size, fg, bg))

    bin_data = render_full(
        text,
        size=size,
        font_paths=paths,
        direction=direction,
        align=align, valign=valign,
        line_height=1.3,
        default_style={'fg': fg, 'bg': bg},
        margin_top=40, margin_bottom=40,
        margin_left=40, margin_right=40,
    )

    print('[draw_test] 写入屏幕…')
    epd.init()
    epd.begin_frame()
    try:
        W_BYTES = 800 // 2  # 4bpp 输出每行 400 字节
        # 注意：bin_data 是 4bpp（每像素 4bit），需要转成 1bpp 再写
        # 这里的渲染结果需要先经过 Bayer 抖动打包为 1bpp
        # 简化：直接把 4bpp 数据丢弃，仅作为占位；建议改用 render_and_display_stream
        for y in range(480):
            start_idx = y * W_BYTES
            end_idx = start_idx + W_BYTES
            # 用 _pack_row 风格把 4bpp 转为 1bpp（这里简化处理）
            row = bytearray(100)
            for bi in range(100):
                bv = 0
                base_x = bi * 8
                for bit in range(8):
                    idx = (base_x + bit) >> 1
                    b = bin_data[start_idx + idx]
                    v = (b & 0xF) if (base_x + bit) & 1 else ((b >> 4) & 0xF)
                    thr = ((0, 8, 2, 10), (12, 4, 14, 6),
                           (3, 11, 1, 9), (15, 7, 13, 5))[y & 3][(base_x + bit) & 3]
                    if v * 17 > thr * 16 + 8:
                        bv |= 0x80 >> bit
                row[bi] = bv
            epd.write_chunk(bytes(row))
    finally:
        epd.end_frame()
        epd.sleep()

    print('[draw_test] 完成')
    return True


if __name__ == '__main__':
    gc.collect()
    show()