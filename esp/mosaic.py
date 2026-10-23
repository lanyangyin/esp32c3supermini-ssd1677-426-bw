# mosaic.py — 网格拼图（REPL 可调用，支持纯色和伪色）
#
# 用法（REPL）：
#   from mosaic import compose_and_display, compose_to_file, compose_bytes
#
#   # 2×2 拼图
#   grid = [
#       ['/bgs/0.bin',  'solid:1'],           # 背景图 + 白
#       ['pseudo:0,1,8', '/bins/0.bin'],      # 灰 50% + BIN
#   ]
#   compose_and_display(grid)
#
# src 格式：
#   '/path/to/N.bin'     文件源
#   'solid:N'            基础色，N=0..5（黑/白/黄/红/蓝/绿）
#   'pseudo:a,b,d'       伪色，a/b=0..5，d=4/8/12（25%/50%/75%）
#   None 或 ''           白底
#
# 每一格显示"源 bin 同位置"的图像；纯色/伪色则按该位置的颜色索引填充。
# 内存策略：逐行处理 + 句柄缓存，8×8=64 格时峰值 < 5KB。

W, H = 720, 480
FRAME_BYTES = 172800
INDEX_MAP = [0x0, 0x1, 0x2, 0x3, 0x5, 0x6]
IDX_REVERSE = {0x0: 0, 0x1: 1, 0x2: 2, 0x3: 3, 0x5: 4, 0x6: 5}
MAX_CELLS = 64
WHITE_IDX = 1

_BAYER4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]


def _norm_grid(grid):
    if not grid:
        raise ValueError('grid 为空')
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    if cols == 0:
        raise ValueError('grid 无列')
    if rows * cols > MAX_CELLS:
        raise ValueError('格子数 {} 超过上限 {}'.format(rows * cols, MAX_CELLS))
    return [list(r) + [None] * (cols - len(r)) for r in grid]


def _boundaries(total, n):
    return [round(i * total / n) for i in range(n + 1)]


def _parse_color_src(src):
    """返回 (kind, params) 或 None。
    kind = 'solid'  → params = (idx,)
    kind = 'pseudo' → params = (a, b, density)
    """
    if not src or not isinstance(src, str):
        return None
    if src.startswith('solid:'):
        try:
            idx = int(src[6:])
            if 0 <= idx <= 5:
                return ('solid', (idx,))
        except Exception:
            pass
        return None
    if src.startswith('pseudo:'):
        try:
            parts = src[7:].split(',')
            if len(parts) == 3:
                a = int(parts[0]); b = int(parts[1]); d = int(parts[2])
                if 0 <= a <= 5 and 0 <= b <= 5 and d > 0:
                    return ('pseudo', (a, b, d))
        except Exception:
            pass
        return None
    return None


_WHITE_ROW = bytearray(W)
for _i in range(W):
    _WHITE_ROW[_i] = WHITE_IDX


def _iter_rows(grid):
    grid = _norm_grid(grid)
    rows = len(grid)
    cols = len(grid[0])
    col_x = _boundaries(W, cols)
    row_y = _boundaries(H, rows)

    # 预解析每格：cell_specs = [(ry, cx, kind, params)]
    cell_specs = []
    handles = {}
    try:
        for ry in range(rows):
            for cx in range(cols):
                src = grid[ry][cx]
                if not src:
                    cell_specs.append((ry, cx, 'empty', None))
                    continue
                parsed = _parse_color_src(src)
                if parsed:
                    cell_specs.append((ry, cx, parsed[0], parsed[1]))
                else:
                    # 文件源
                    if src not in handles:
                        try:
                            handles[src] = open(src, 'rb')
                            cell_specs.append((ry, cx, 'file', src))
                        except OSError as e:
                            print('[mosaic] open fail:', src, e)
                            cell_specs.append((ry, cx, 'empty', None))
                    else:
                        cell_specs.append((ry, cx, 'file', src))

        # 每行属于哪个 ry
        row_of = [0] * H
        for ry in range(rows):
            for y in range(row_y[ry], row_y[ry + 1]):
                if 0 <= y < H:
                    row_of[y] = ry

        out_row = bytearray(W)

        for out_y in range(H):
            ry = row_of[out_y]
            out_row[:] = _WHITE_ROW

            for (c_ry, cx, kind, params) in cell_specs:
                if c_ry != ry:
                    continue
                x0 = col_x[cx]
                x1 = col_x[cx + 1]
                if x1 <= x0:
                    continue

                if kind == 'empty':
                    continue
                elif kind == 'solid':
                    idx = params[0]
                    for x in range(x0, x1):
                        out_row[x] = idx
                elif kind == 'pseudo':
                    a, b, d = params
                    bayer = _BAYER4[out_y & 3]
                    for x in range(x0, x1):
                        out_row[x] = a if bayer[x & 3] < d else b
                elif kind == 'file':
                    f = handles[params]
                    byte_start = x0 >> 1
                    byte_end = (x1 + 1) >> 1
                    byte_count = byte_end - byte_start
                    if byte_count <= 0:
                        continue
                    try:
                        f.seek(out_y * 360 + byte_start)
                        line = f.read(byte_count)
                    except Exception:
                        continue
                    n = len(line)
                    for x in range(x0, x1):
                        bi = (x >> 1) - byte_start
                        if bi < 0 or bi >= n:
                            continue
                        bval = line[bi]
                        nib = (bval & 0xF) if (x & 1) else ((bval >> 4) & 0xF)
                        pi = IDX_REVERSE.get(nib)
                        out_row[x] = pi if pi is not None else WHITE_IDX

            packed = bytearray(360)
            for i in range(0, W, 2):
                hi = INDEX_MAP[out_row[i]]
                lo = INDEX_MAP[out_row[i + 1]]
                packed[i >> 1] = (hi << 4) | lo
            yield bytes(packed)
    finally:
        for f in handles.values():
            try:
                f.close()
            except Exception:
                pass


def compose_to_file(grid, path):
    """流式写文件。返回写入字节数。"""
    n = 0
    with open(path, 'wb') as f:
        for row in _iter_rows(grid):
            f.write(row)
            n += len(row)
    return n


def compose_bytes(grid):
    """返回完整 bytes（172800）。"""
    out = bytearray(FRAME_BYTES)
    pos = 0
    for row in _iter_rows(grid):
        out[pos:pos + len(row)] = row
        pos += len(row)
    return bytes(out)


def compose_and_display(grid, epd=None):
    """流式显示到屏幕。"""
    if epd is None:
        from jd7601 import JD7601
        epd = JD7601()
    epd.init()
    epd.begin_frame()
    try:
        for row in _iter_rows(grid):
            epd.write_chunk(row)
    finally:
        epd.end_frame()
        epd.sleep()