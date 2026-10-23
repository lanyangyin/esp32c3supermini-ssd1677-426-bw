# wifi_manager.py — AP + STA 管理
import network
import json
import time

CONFIG_FILE = '/networks.json'
AP_SSID = 'EInkReader'
AP_PASSWORD = 'reader1234'


def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            'known_networks': [],
            'ap': {'essid': AP_SSID, 'password': AP_PASSWORD}
        }


def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f)
    except Exception as e:
        print('[WIFI] 保存配置失败:', e)


def start_ap(cfg=None):
    if cfg is None:
        cfg = load_config()
    ap_cfg = cfg.get('ap', {})
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ap_cfg.get('essid', AP_SSID),
              password=ap_cfg.get('password', AP_PASSWORD),
              authmode=network.AUTH_WPA_WPA2_PSK)
    try:
        ap.config(txpower=10)
    except Exception:
        pass
    print('[AP] %s  http://%s' % (ap_cfg.get('essid', AP_SSID),
                                   ap.ifconfig()[0]))
    return ap


def scan_networks():
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
        time.sleep(0.5)
    try:
        nets = sta.scan()
    except Exception as e:
        print('[WIFI] 扫描失败:', e)
        return []
    result = []
    for net in nets:
        ssid = net[0].decode() if net[0] else ''
        if ssid:
            result.append((ssid, net[3], net[4]))
    result.sort(key=lambda x: x[1], reverse=True)
    return result


def _sta_cleanup(sta):
    try:
        if sta.isconnected():
            sta.disconnect()
            for _ in range(30):
                if not sta.isconnected():
                    break
                time.sleep(0.1)
    except Exception:
        pass
    try:
        sta.disconnect()
    except Exception:
        pass
    time.sleep(0.5)


def connect_sta(ssid, password, timeout=25, retries=3):
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
        time.sleep(0.5)

    _sta_cleanup(sta)

    for attempt in range(1, retries + 1):
        print('[STA] 连接 %s（%d/%d）...' % (ssid, attempt, retries))
        try:
            sta.connect(ssid, password)
        except OSError as e:
            print('[STA] 异常:', e)
            _sta_cleanup(sta)
            time.sleep(1.5)
            continue
        except Exception as e:
            print('[STA] 异常:', e)
            return False

        start = time.time()
        while not sta.isconnected():
            if time.time() - start > timeout:
                print('[STA] %s 超时（%ds）' % (ssid, timeout))
                break
            time.sleep(0.2)

        if sta.isconnected():
            start = time.time()
            while sta.ifconfig()[0] == '0.0.0.0':
                if time.time() - start > 5:
                    break
                time.sleep(0.2)
            print('[STA] 已连接 %s  http://%s' % (ssid, sta.ifconfig()[0]))
            return True

        _sta_cleanup(sta)
        time.sleep(1)

    return False


def auto_connect(force=False):
    """按 priority 顺序尝试已保存的网络"""
    cfg = load_config()
    networks = cfg.get('known_networks', [])
    networks.sort(key=lambda n: n.get('priority', 999))

    if not networks:
        print('[STA] 无已保存网络')
        return False

    for net in networks:
        ssid = net.get('ssid')
        pwd = net.get('password', '')
        if not ssid:
            continue
        # 单次 12 秒，最多试 1 次；失败就下一个
        if connect_sta(ssid, pwd, timeout=12, retries=1):
            return True

    return False


def get_ap_info():
    ssid = AP_SSID
    pwd = AP_PASSWORD
    ip = '192.168.4.1'
    try:
        cfg = load_config()
        ap = cfg.get('ap', {})
        ssid = ap.get('essid') or ssid
        pwd = ap.get('password') or pwd
    except Exception:
        pass
    try:
        w = network.WLAN(network.AP_IF)
        if w.active():
            ip = w.ifconfig()[0]
    except Exception:
        pass
    return ssid, pwd, ip


def get_sta_info():
    ssid = '(not connected)'
    ip = '0.0.0.0'
    try:
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