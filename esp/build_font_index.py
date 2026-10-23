# build_font_index.py — 一次性扫描字体目录，生成 /fonts_index.json
#
# REPL 用法：
#   >>> import build_font_index
#   >>> build_font_index.build()
#
# 添加新字体后重新跑一次即可。
# web_server.start_server() 启动时会检查此文件是否存在。

import os
import json
try:
    import gc
except ImportError:
    gc = None

INDEX_PATH = '/fonts_index.json'
FLASH_DIR = '/www/fonts'
SD_DIR = '/sd/e6/fonts'


def _collect(gc_now=True):
    if gc is not None and gc_now:
        gc.collect()


def _parse_family_size(name):
    """从 'family_size_N.bin' / 'family_size.idx' / 'font_32.bin' 解析 (family, size)"""
    base = name
    for ext in ('.bin', '.idx', '.map'):
        if base.endswith(ext):
            base = base[:-len(ext)]
            break

    parts = base.rsplit('_', 2)
    if len(parts) == 3:
        try:
            return parts[0], int(parts[1])
        except ValueError:
            return None
    if len(parts) == 2:
        try:
            return parts[0], int(parts[1])
        except ValueError:
            pass
    if base.startswith('font_'):
        try:
            return 'font', int(base[5:])
        except ValueError:
            pass
    return None


def _scan_idx(fs_root, storage):
    """优先扫 .idx（每个 family/size 只需读 1 个小文件）"""
    fonts = []
    try:
        names = sorted(os.listdir(fs_root))
    except OSError as e:
        print('[build] %s 不可读: %s' % (fs_root, e))
        return fonts

    seen = set()
    idx_read = 0
    idx_fail = 0
    for name in names:
        if not name.endswith('.idx'):
            continue
        parsed = _parse_family_size(name)
        if not parsed:
            continue
        family, size = parsed
        key = (family, size)
        if key in seen:
            continue
        seen.add(key)

        try:
            with open(fs_root + '/' + name) as f:
                idx = json.load(f)
            idx_read += 1
        except Exception:
            idx_fail += 1
            continue

        chunks = idx.get('chunks', [])
        if not chunks:
            continue

        fonts.append({
            'family': family,
            'size': size,
            'storage': storage,
            'chunk_count': len(chunks),
        })
        _collect()

    print('[build] %s: 读 %d 个 .idx 成功，%d 失败，得到 %d 组字体'
          % (fs_root, idx_read, idx_fail, len(fonts)))
    return fonts


def _scan_bin(fs_root, storage):
    """兜底：无 .idx 时扫 .bin"""
    fonts = []
    try:
        names = sorted(os.listdir(fs_root))
    except OSError:
        return fonts

    groups = {}
    for name in names:
        if not name.endswith('.bin'):
            continue
        parsed = _parse_family_size(name)
        if not parsed:
            continue
        family, size = parsed
        key = (family, size)
        if key not in groups:
            groups[key] = {
                'family': family,
                'size': size,
                'storage': storage,
                'chunk_count': 0,
            }
        groups[key]['chunk_count'] += 1

    fonts = list(groups.values())
    print('[build] %s: 扫 .bin 得到 %d 组字体' % (fs_root, len(fonts)))
    return fonts


def _scan_dir(fs_root, storage):
    fonts = _scan_idx(fs_root, storage)
    if not fonts:
        print('[build] %s: 无 .idx，退化扫 .bin' % fs_root)
        fonts = _scan_bin(fs_root, storage)
    return fonts


def build(verbose=True):
    _collect()
    print('[build] === 开始构建字体索引 ===')

    all_fonts = []

    all_fonts.extend(_scan_dir(FLASH_DIR, 'flash'))
    _collect()

    try:
        os.stat(SD_DIR)
        n_before = len(all_fonts)
        all_fonts.extend(_scan_dir(SD_DIR, 'sd'))
        print('[build] SD: 新增 %d 组' % (len(all_fonts) - n_before))
    except OSError:
        print('[build] SD: %s 不存在，跳过' % SD_DIR)
    _collect()

    all_fonts.sort(key=lambda x: (x['storage'] != 'sd', x['family'], x['size']))

    data = {'version': 1, 'fonts': all_fonts}

    try:
        os.remove(INDEX_PATH)
    except OSError:
        pass

    try:
        with open(INDEX_PATH, 'w') as f:
            json.dump(data, f)
        sz = os.stat(INDEX_PATH)[6]
        print('[build] === 完成：%d 组字体 → %s (%d 字节) ==='
              % (len(all_fonts), INDEX_PATH, sz))
        if verbose:
            for fnt in all_fonts[:30]:
                print('   [%-5s] %-32s %3dpx × %d chunk' % (
                    fnt['storage'].upper(),
                    fnt['family'],
                    fnt['size'],
                    fnt['chunk_count']))
            if len(all_fonts) > 30:
                print('   ... 共 %d 组' % len(all_fonts))
        _collect()
        return True
    except Exception as e:
        print('[build] 写入 %s 失败: %s' % (INDEX_PATH, e))
        return False


if __name__ == '__main__':
    build()