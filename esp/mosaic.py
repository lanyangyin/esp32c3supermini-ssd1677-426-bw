# mosaic.py — 适配 SSD1677 网格拼图 (1bpp, 800x480)
#
# src 格式：
#   '/path/to/N.bin'     文件源 (必须是 48000 字节的 1bpp 格式)
#   'solid:0'            黑色
#   'solid:1'            白色
#   None 或 ''           白底

W, H = 800, 480
ROW_BYTES = W // 8  # 100 字节/行
FRAME_BYTES = ROW_BYTES * H  # 48000 字节
MAX_CELLS = 64
WHITE_IDX = 1


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
    只要遇到 solid 或 pseudo，统一视为纯色处理。
    """
    if not src or not isinstance(src, str):
        return None
    if src.startswith('solid:'):
        try:
            idx = int(src[6:])
            return ('solid', (idx,))
        except Exception:
            pass
        return None
    if src.startswith('pseudo:'):
        # 伪色全部视为白色 (或者你可以选择视为黑)
        return ('solid', (1,))
    return None


def _iter_rows(grid):
    grid = _norm_grid(grid)
    rows = len(grid)
    cols = len(grid[0])
    col_x = _boundaries(W, cols)
    row_y = _boundaries(H, rows)

    # 预解析每格
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

        # 每一行属于哪个 ry
        row_of = [0] * H
        for ry in range(rows):
            for y in range(row_y[ry], row_y[ry + 1]):
                if 0 <= y < H:
                    row_of[y] = ry

        # 行级缓存，避免重复 seek
        file_row_cache = {}

        for out_y in range(H):
            ry = row_of[out_y]
            out_row = bytearray(ROW_BYTES)  # 默认全 0 (白底)

            # 清理上一行的缓存
            file_row_cache.clear()

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
                    if idx == 0:  # 如果是黑色
                        # 把 x0 到 x1 的位全置 1
                        start_byte = x0 >> 3
                        end_byte = (x1 - 1) >> 3
                        if start_byte == end_byte:
                            mask = (0xFF >> (x0 & 7)) & (0xFF << (7 - ((x1 - 1) & 7)))
                            out_row[start_byte] |= mask
                        else:
                            out_row[start_byte] |= (0xFF >> (x0 & 7))
                            for b in range(start_byte + 1, end_byte):
                                out_row[b] = 0xFF
                            out_row[end_byte] |= (0xFF << (7 - ((x1 - 1) & 7)))
                elif kind == 'file':
                    f = handles[params]
                    if params not in file_row_cache:
                        try:
                            f.seek(out_y * ROW_BYTES)
                            file_row_cache[params] = f.read(ROW_BYTES)
                        except Exception:
                            file_row_cache[params] = b'\x00' * ROW_BYTES

                    src_row = file_row_cache[params]

                    # 按位拷贝
                    for x in range(x0, x1):
                        src_byte_idx = x >> 3
                        if src_byte_idx >= len(src_row):
                            break
                        # 如果源位是 1，则在新行置 1
                        if (src_row[src_byte_idx] >> (7 - (x & 7))) & 1:
                            out_row[x >> 3] |= (0x80 >> (x & 7))

            yield bytes(out_row)
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
    """返回完整 bytes（48000）。"""
    out = bytearray(FRAME_BYTES)
    pos = 0
    for row in _iter_rows(grid):
        out[pos:pos + len(row)] = row
        pos += len(row)
    return bytes(out)


def compose_and_display(grid, epd=None):
    """流式显示到屏幕。"""
    if epd is None:
        import epd
    epd.fill(1)  # 清屏（白）
    pos = 0
    for row in _iter_rows(grid):
        epd.buffer[pos:pos + len(row)] = row
        pos += len(row)
    epd.show()