# draw_test.py — 独立渲染测试（REPL: import draw_test）
#
# 用法：
#   >>> import draw_test
#   >>> draw_test.show()                          # 用默认字模 + 默认文本
#   >>> draw_test.show("中文测试")                # 自定义文本
#   >>> draw_test.show("你好", size=48)           # 指定字号
#   >>> draw_test.show("你好", family="SmileySans-Oblique", size=32)
#
# 自动探测可用字模：优先 SD（v4 多 chunk），否则 Flash。

import gc
import os
from jd7601 import JD7601
from text_renderer import render_and_display_stream

# ---------- 探测 ----------

SD_FONT_DIR = '/sd/e6/fonts'
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
            # 形如 family_size_0 / family_size / font_32
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

def show(text="你好，世界！\nESP32 独立渲染 OK",
         family=None, size=None, direction=0,
         align='center', valign='center'):
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

    epd = JD7601()
    gc.collect()
    print('[draw_test] 开始渲染 (size=%d)…' % size)
    render_and_display_stream(
        epd, text,
        font_paths=paths, size=size,
        direction=direction,
        align=align, valign=valign,
        line_height=1.3,
        default_style={'fg': 'black', 'bg': 'white'},
        band_rows=8,
        margin_top=40, margin_bottom=40,
        margin_left=40, margin_right=40,
    )
    print('[draw_test] 完成')
    return True


if __name__ == '__main__':
    gc.collect()
    show()