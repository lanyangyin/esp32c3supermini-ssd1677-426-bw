# boot_screen.py — 开机模式检测（GPIO20+21）+ 4 种动作（1bpp 800x480）

import os
import gc
import time

MODE_PIN_A = 20
MODE_PIN_B = 21

from machine import Pin

MODE_PIN_PULL = Pin.PULL_UP
MODE_ACTIVE_VALUE = 0

DEBOUNCE_MS = 100
SAMPLES = 10

MODE_NOOP = 0
MODE_RANDOM = 1
MODE_NETINFO = 2
MODE_DESIGNATED = 3

FIXED_BOTTOM = ">>>>>>>>>>>><<<<<<<<<<<<"
GRAY_FILL = 'pseudo:0,1,4'
TMP_BG_SD = '/sd/bgs/__boot_bg__.bin'
TMP_BG_FLASH = '/bgs/__boot_bg__.bin'
BOOT_CFG_PATH = '/boot_cfg.json'
FRAME_BYTES = 800 * 480 // 8  # 48000 字节（1bpp）

import epd


def check_and_run(force_mode=None):
    if force_mode is None:
        mode = _read_mode()
        if mode is None:
            return False
    else:
        mode = force_mode
        print('[BOOT] 强制模式: %d%d (mode=%d)'
              % ((mode >> 1) & 1, mode & 1, mode))
    return run_mode(mode)


def run_mode(mode):
    t0 = time.ticks_ms()
    try:
        if mode == MODE_NOOP:
            print('[BOOT] 模式 00：什么都不做')
            return False
        elif mode == MODE_RANDOM:
            print('[BOOT] 模式 01：随机 bin')
            ok = _do_random_bin_screen()
        elif mode == MODE_NETINFO:
            print('[BOOT] 模式 10：网络信息')
            ok = _do_netinfo_screen()
        elif mode == MODE_DESIGNATED:
            print('[BOOT] 模式 11：指定 bin')
            ok = _do_designated_bin_screen()
        else:
            print('[BOOT] 未知模式: %d' % mode)
            return False

        dt = time.ticks_diff(time.ticks_ms(), t0)
        print('[BOOT] 完成，用时 %d ms' % dt)
        return ok

    except KeyboardInterrupt:
        print('[BOOT] 被用户中断')
        try:
            epd.sleep()
        except Exception:
            pass
        return False
    except Exception as e:
        print('[BOOT] 执行失败:', e)
        try:
            import sys
            sys.print_exception(e)
        except Exception:
            pass
        return False


def _read_mode():
    try:
        pa = Pin(MODE_PIN_A, Pin.IN, MODE_PIN_PULL)
        pb = Pin(MODE_PIN_B, Pin.IN, MODE_PIN_PULL)
    except Exception as e:
        print('[BOOT] GPIO 初始化失败:', e)
        return None

    time.sleep_ms(DEBOUNCE_MS)
    sa = [pa.value() for _ in range(SAMPLES)]
    sb = [pb.value() for _ in range(SAMPLES)]

    ca = sum(1 for v in sa if v == MODE_ACTIVE_VALUE)
    cb = sum(1 for v in sb if v == MODE_ACTIVE_VALUE)
    va = 1 if ca > SAMPLES // 2 else 0
    vb = 1 if cb > SAMPLES // 2 else 0

    print('[BOOT] GPIO%d 采样 %s → %d' % (MODE_PIN_A, sa, va))
    print('[BOOT] GPIO%d 采样 %s → %d' % (MODE_PIN_B, sb, vb))

    if len(set(sa)) > 1 or len(set(sb)) > 1:
        print('[BOOT] 警告: 引脚值有跳变，可能接触不良或 UART0 冲突')

    return (va << 1) | vb


def _do_random_bin_screen():
    import random
    candidates = _scan_bins()
    if not candidates:
        print('[BOOT] bins 为空，跳过')
        return False

    path = candidates[random.randrange(len(candidates))]
    print('[BOOT] 从 %d 个 bin 中随机选中: %s' % (len(candidates), path))
    return _display_bin_file(path, mode='full')


def _do_netinfo_screen():
    bg = _compose_mosaic()
    if bg is None:
        print('[BOOT] 拼图失败，跳过')
        return False
    gc.collect()
    _render_netinfo(bg)
    return True


def _do_designated_bin_screen():
    cfg = _load_boot_cfg()
    storage = cfg.get('storage')
    index = cfg.get('index')

    if storage in ('sd', 'flash') and isinstance(index, int) and index >= 0:
        path = _storage_to_path(storage, index)
        print('[BOOT] 指定: %s 槽位 %d → %s'
              % (storage, index + 1, path))
        if _file_exists(path):
            return _display_bin_file(path, mode='full')
        print('[BOOT] 指定文件不存在，回退到最后一个')

    candidates = _scan_bins()
    if not candidates:
        print('[BOOT] bins 为空，跳过')
        return False
    candidates.sort()
    path = candidates[-1]
    print('[BOOT] 使用最后一个 bin: %s' % path)
    return _display_bin_file(path, mode='full')


def _load_boot_cfg():
    try:
        import json
        with open(BOOT_CFG_PATH, 'r') as f:
            cfg = json.load(f)
            if isinstance(cfg, dict):
                return cfg
    except (OSError, ValueError):
        pass
    return {}


def get_designated():
    return _load_boot_cfg()


def set_designated(storage, index):
    import json
    if storage not in ('sd', 'flash') or not isinstance(index, int) or index < 0:
        print('[BOOT] 非法参数')
        return False
    try:
        with open(BOOT_CFG_PATH, 'w') as f:
            json.dump({'storage': storage, 'index': index}, f)
        print('[BOOT] 已设置开机指定: %s 槽位 %d' % (storage, index + 1))
        return True
    except OSError as e:
        print('[BOOT] 写入配置失败:', e)
        return False


def clear_designated():
    try:
        os.remove(BOOT_CFG_PATH)
        print('[BOOT] 已清除开机指定')
    except OSError:
        pass
    return True


def _scan_bins():
    out = []
    for d in ('/bins', '/sd/bins'):
        try:
            for n in os.listdir(d):
                if n.endswith('.bin'):
                    out.append(d + '/' + n)
        except OSError:
            pass
    return out


def _storage_to_path(storage, index):
    if storage == 'sd':
        return '/sd/bins/%d.bin' % index
    return '/bins/%d.bin' % index


def _file_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _ensure_dir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _display_bin_file(path, mode='full'):
    """显示 1bpp BIN 文件（48000 字节）。

    mode: 'full' | 'fast' | 'partial'
    """
    try:
        size = os.stat(path)[6]
    except OSError:
        print('[BOOT] 文件不存在: %s' % path)
        return False
    if size != FRAME_BYTES:
        print('[BOOT] 文件大小异常: %d（应为 %d）' % (size, FRAME_BYTES))
        return False

    print('[BOOT] 开始显示 %s（%d 字节, mode=%s）…' % (path, size, mode))
    epd.init()
    epd.begin_frame()
    try:
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                epd.write_chunk(chunk)
                gc.collect()
    except Exception as e:
        print('[BOOT] 写数据失败:', e)
        try:
            epd.end_frame('full')
        except Exception:
            pass
        try:
            epd.sleep()
        except Exception:
            pass
        return False

    try:
        epd.end_frame(mode)
    except Exception as e:
        print('[BOOT] end_frame 失败:', e)
        return False
    try:
        epd.sleep()
    except Exception:
        pass
    print('[BOOT] 显示完成')
    return True


def _resolve_font():
    for p in (
            '/www/fonts/base_32_0.bin',
            '/sd/fonts/base_32_0.bin',
    ):
        try:
            os.stat(p)
            print('[BOOT] 字体: %s' % p)
            return [p]
        except OSError:
            pass

    for d in ('/www/fonts', '/sd/fonts'):
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith('_32_0.bin'))
            if names:
                print('[BOOT] 字体: %s/%s (fallback)' % (d, names[0]))
                return [d + '/' + names[0]]
        except OSError:
            pass

    print('[BOOT] 警告: 未找到 32px 字体')
    return ['/www/fonts/base_32_0.bin']


def _pick_tmp_path():
    _ensure_dir('/sd/bgs')
    try:
        os.stat('/sd/bgs')
        return TMP_BG_SD
    except OSError:
        pass
    _ensure_dir('/bgs')
    return TMP_BG_FLASH


def _shuffle(a):
    import random
    for i in range(len(a) - 1, 0, -1):
        j = random.randrange(i + 1)
        a[i], a[j] = a[j], a[i]


def _compose_mosaic():
    from mosaic import compose_to_file

    print('[BOOT] [1/4] 扫描 bins…')
    candidates = _scan_bins()
    if candidates:
        _shuffle(candidates)
        print('[BOOT]       找到 %d 个 bins，随机选 6 张' % len(candidates))
        left = [candidates[i % len(candidates)] for i in range(6)]
    else:
        print('[BOOT]       无可用 bins，左侧也填浅灰')
        left = [GRAY_FILL] * 6

    grid = [
        [left[0], left[1], left[2], GRAY_FILL, GRAY_FILL, GRAY_FILL],
        [left[3], left[4], left[5], GRAY_FILL, GRAY_FILL, GRAY_FILL],
    ]

    print('[BOOT] [2/4] 合成拼图…')
    tmp = _pick_tmp_path()
    try:
        n_bytes = compose_to_file(grid, tmp)
        print('[BOOT]       已写入 %s (%d 字节)' % (tmp, n_bytes))
        return tmp
    except Exception as e:
        print('[BOOT]       写入 %s 失败: %s' % (tmp, e))

    _ensure_dir('/bgs')
    tmp = TMP_BG_FLASH
    try:
        n_bytes = compose_to_file(grid, tmp)
        print('[BOOT]       已写入 %s (%d 字节)（Flash 兜底）' % (tmp, n_bytes))
        return tmp
    except Exception as e:
        print('[BOOT]       写入 %s 失败: %s' % (tmp, e))
        return None


def _get_ap_info():
    ssid = 'E6-Panel'
    pwd = 'e6epaper'
    ip = '192.168.4.1'
    try:
        import wifi_manager
        cfg = wifi_manager.load_config()
        ap = cfg.get('ap', {})
        ssid = ap.get('essid') or ssid
        pwd = ap.get('password') or pwd
    except Exception:
        pass
    try:
        import network
        w = network.WLAN(network.AP_IF)
        if w.active():
            ip = w.ifconfig()[0]
    except Exception:
        pass
    return ssid, pwd, ip


def _get_sta_info():
    ssid = '(not connected)'
    ip = '0.0.0.0'
    try:
        import network
        w = network.WLAN(network.STA_IF)
        if w.isconnected():
            try:
                s = w.config('essid')
                if isinstance(s, bytes):
                    s = s.decode()
                ssid = s or ssid
            except Exception:
                pass
            ip = w.ifconfig()[0]
    except Exception:
        pass
    return ssid, ip


def _build_netinfo_text():
    ap_ssid, ap_pwd, ap_ip = _get_ap_info()
    sta_ssid, sta_ip = _get_sta_info()

    return (
        "ap:\n"
        "  ssid: %s\n"
        "  password: %s\n"
        "  ip: %s\n"
        "----------------------------------------\n"
        "sta:\n"
        "  ssid: %s\n"
        "  ip: %s\n"
        "%s"
    ) % (ap_ssid, ap_pwd, ap_ip, sta_ssid, sta_ip, FIXED_BOTTOM)


def _render_netinfo(bg_path):
    from text_renderer import render_and_display_stream

    print('[BOOT] [3/4] 构造网络信息文字…')
    text = _build_netinfo_text()

    per_char = {}
    idx = 0
    for line in text.split('\n'):
        for _ in line:
            per_char[str(idx)] = {'bold': True}
            idx += 1

    font_paths = _resolve_font()
    gc.collect()

    print('[BOOT] [4/4] 渲染到屏幕（背景 %s）…' % bg_path)

    # 修复：传 epd 模块（有 init/begin_frame/write_chunk/end_frame/sleep），
    # 而不是 epd._display（SSD1677 实例只有 init_display()）
    render_and_display_stream(
        epd,
        text,
        size=32,
        direction=90,
        per_char=per_char,
        default_style={
            'fg': {'type': 'pseudo', 'a': 0, 'b': 1, 'density': 12},
            'bg': {'type': 'solid', 'idx': 1},
            'outline': {'type': 'pseudo', 'a': 0, 'b': 1, 'density': 4},
            'bold': False,
            'italic': False,
            'underline': False,
            'strikethrough': False,
            'letter_spacing': 3,
        },
        align='left',
        valign='top',
        line_height=0.8,
        line_extras={3: -15, 4: -15, 7: 50},
        margin_top=0,
        margin_bottom=20,
        margin_left=20,
        margin_right=20,
        band_rows=8,
        background=bg_path,
        font_paths=font_paths,
        mode='full',
    )