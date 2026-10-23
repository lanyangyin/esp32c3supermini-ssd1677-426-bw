# boot_screen.py — 固定显示网络信息
import os
import gc

FIXED_BOTTOM = ">>>>>>>>>>>><<<<<<<<<<<<"
GRAY_FILL = 'pseudo:0,1,4'
TMP_BG_SD = '/sd/bgs/__boot_bg__.bin'
TMP_BG_FLASH = '/bgs/__boot_bg__.bin'

def show_netinfo():
    """固定展示网络信息（开机唯一动作）"""
    print('[BOOT] 显示网络信息')
    try:
        bg = _compose_mosaic()
        if bg:
            _render_netinfo(bg)
        else:
            print('[BOOT] 拼图背景失败')
    except Exception as e:
        print('[BOOT] 渲染网络信息失败:', e)
        try:
            import sys
            sys.print_exception(e)
        except Exception:
            pass

# ================= 内部逻辑 =================

def _ensure_dir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass

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

def _shuffle(a):
    import random
    for i in range(len(a) - 1, 0, -1):
        j = random.randrange(i + 1)
        a[i], a[j] = a[j], a[i]

def _pick_tmp_path():
    _ensure_dir('/sd')
    _ensure_dir('/sd/bgs')
    try:
        os.stat('/sd/bgs')
        return TMP_BG_SD
    except OSError:
        pass
    _ensure_dir('/bgs')
    return TMP_BG_FLASH

def _compose_mosaic():
    from mosaic import compose_to_file
    candidates = _scan_bins()
    if candidates:
        _shuffle(candidates)
        left = [candidates[i % len(candidates)] for i in range(6)]
    else:
        left = [GRAY_FILL] * 6

    grid = [
        [left[0], left[1], left[2], GRAY_FILL, GRAY_FILL, GRAY_FILL],
        [left[3], left[4], left[5], GRAY_FILL, GRAY_FILL, GRAY_FILL],
    ]
    tmp = _pick_tmp_path()
    try:
        compose_to_file(grid, tmp)
        return tmp
    except Exception as e:
        print('[BOOT] 拼图写入失败:', e)
        return None

def _get_ap_info():
    ssid, pwd, ip = 'E6-Panel', 'e6epaper', '192.168.4.1'
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
    ssid, ip = '(not connected)', '0.0.0.0'
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
    import epd
    from text_renderer import render_and_display_stream
    text = _build_netinfo_text()
    font_paths = ['/www/fonts/base_32_0.bin'] # 默认字体

    per_char = {}
    idx = 0
    for line in text.split('\n'):
        for _ in line:
            per_char[str(idx)] = {'bold': True}
            idx += 1

    render_and_display_stream(
        epd, text,
        size=32,
        direction=0,
        per_char=per_char,
        default_style={
            'gray_level': 0,  # 0 是纯黑
            'bold': False,
            'italic': False,
            'underline': False,
        },
        align='left',
        valign='top',
        line_height=1.2,
        margin_top=20, margin_bottom=20,
        margin_left=20, margin_right=20,
        band_rows=8,
        background=bg_path,
        font_paths=font_paths,
    )