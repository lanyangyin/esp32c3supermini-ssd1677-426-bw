# wifi_manager.py
import network
import json
import time

CONFIG_FILE = "/networks.json"
AP_SSID = "E6-Panel"
AP_PASSWORD = "e6epaper"


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except:
        return {"known_networks": [], "ap": {"essid": AP_SSID, "password": AP_PASSWORD}}


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f)


def start_ap(cfg=None):
    if cfg is None:
        cfg = load_config()
    ap_cfg = cfg.get("ap", {})
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ap_cfg.get("essid", AP_SSID),
              password=ap_cfg.get("password", AP_PASSWORD),
              authmode=network.AUTH_WPA_WPA2_PSK)
    try:
        ap.config(txpower=10)
    except:
        pass
    print(f"[AP] http://{ap.ifconfig()[0]}")
    return ap


def scan_networks():
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
    nets = sta.scan()
    result = []
    for net in nets:
        ssid = net[0].decode() if net[0] else ""
        rssi = net[3]
        auth = net[4]
        if ssid:
            result.append((ssid, rssi, auth))
    result.sort(key=lambda x: x[1], reverse=True)
    return result


def _sta_cleanup(sta):
    """强制把 STA 接口恢复到干净状态（未连接、非 connecting）"""
    try:
        if sta.isconnected():
            sta.disconnect()
            for _ in range(30):
                if not sta.isconnected():
                    break
                time.sleep(0.1)
    except Exception:
        pass
    # 即便 isconnected() 为 False，也可能残留 connecting 状态
    try:
        sta.disconnect()
    except Exception:
        pass
    time.sleep(0.5)


def connect_sta(ssid, password, timeout=25, retries=3):
    """
    尝试连接 STA。
    - timeout: 单次连接等待上限（秒），APSTA 共存下给足时间
    - retries: 遇到 Internal State Error 或超时的重试次数
    """
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
        time.sleep(0.5)

    _sta_cleanup(sta)

    for attempt in range(1, retries + 1):
        print(f"[STA] 尝试连接 {ssid}（第 {attempt}/{retries} 次）...")
        try:
            sta.connect(ssid, password)
        except OSError as e:
            print(f"[STA] 连接异常: {e}")
            _sta_cleanup(sta)
            time.sleep(1.5)
            continue
        except Exception as e:
            print(f"[STA] 连接异常: {e}")
            return False

        start = time.time()
        while not sta.isconnected():
            if time.time() - start > timeout:
                print(f"[STA] {ssid} 连接超时（{timeout}s）")
                break
            time.sleep(0.2)

        if sta.isconnected():
            # 等 DHCP 分配 IP（防 0.0.0.0）
            start = time.time()
            while sta.ifconfig()[0] == '0.0.0.0':
                if time.time() - start > 5:
                    break
                time.sleep(0.2)
            print(f"[STA] 已连接 {ssid}, http://{sta.ifconfig()[0]}")
            return True

        _sta_cleanup(sta)
        time.sleep(1)

    return False


def _scan_visible_ssids():
    """
    扫描当前可见的 SSID 集合。
    返回 set 表示扫描成功，返回 None 表示扫描失败（调用方应退化为不筛选）。
    """
    try:
        sta = network.WLAN(network.STA_IF)
        if not sta.active():
            sta.active(True)
            time.sleep(0.5)
        t0 = time.time()
        visible = set()
        for net in sta.scan():
            ssid = net[0].decode() if net[0] else ""
            if ssid:
                visible.add(ssid)
        elapsed = time.time() - t0
        print(f"[STA] 扫描完成（{elapsed:.1f}s），发现 {len(visible)} 个网络")
        return visible
    except Exception as e:
        print(f"[STA] 扫描失败: {e}")
        return None


def auto_connect(force=False):
    """
    按优先级依次尝试连接已保存的网络。
    - force=False（默认）：先扫描，只尝试扫描到的网络
    - force=True：跳过扫描，盲试所有已保存网络（用于隐藏 SSID 等场景）
    """
    cfg = load_config()
    networks = cfg.get("known_networks", [])
    networks.sort(key=lambda n: n.get("priority", 999))

    if not networks:
        print("[STA] 无已保存网络")
        return False

    if force:
        candidates = networks
    else:
        visible = _scan_visible_ssids()
        if visible is None:
            # 扫描失败，退化为盲试
            print("[STA] 退化为逐个尝试")
            candidates = networks
        else:
            candidates = []
            for n in networks:
                ssid = n.get("ssid")
                if ssid in visible:
                    candidates.append(n)
                else:
                    print(f"[STA] 跳过 {ssid}（不在扫描范围内）")
            if not candidates:
                print("[STA] 已知网络均不在范围内")
                return False

    for net in candidates:
        ssid = net.get("ssid")
        pwd = net.get("password", "")
        if not ssid:
            continue
        if connect_sta(ssid, pwd):
            return True

    return False