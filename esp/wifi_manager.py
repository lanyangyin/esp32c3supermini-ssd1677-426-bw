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
    except Exception:
        return {"known_networks": [],
                "ap": {"essid": AP_SSID, "password": AP_PASSWORD}}


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f)


def start_ap(cfg=None):
    if cfg is None:
        cfg = load_config()
    ap_cfg = cfg.get("ap", {})
    ap = network.WLAN(network.AP_IF)

    if not ap.active():
        ap.active(True)
        time.sleep(1.5)  # PHY 预热

    ap.config(essid=ap_cfg.get("essid", AP_SSID),
              password=ap_cfg.get("password", AP_PASSWORD),
              authmode=network.AUTH_WPA_WPA2_PSK)

    # 轮询等 IP
    for _ in range(20):
        ip = ap.ifconfig()[0]
        if ip and ip != '0.0.0.0':
            break
        time.sleep(0.5)

    print(f"[AP] http://{ap.ifconfig()[0]}")
    return ap


def scan_networks():
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
        time.sleep(1.0)  # PHY 预热
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


def connect_sta(ssid, password, timeout=20, retries=2):
    """连接 STA。超时含 PHY 预热 + 认证 + DHCP。

    关键改进：
      - 首次 sta.active(True) 后 sleep(1.5) 预热 PHY（原 0.5s 冷启动不够）
      - IP 等待 8 秒（原 5s 太短）
      - 默认 retries=2（原 3，多试浪费启动时间）
    """
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
        time.sleep(1.5)  # 预热
    else:
        time.sleep(0.3)

    try:
        if sta.isconnected():
            sta.disconnect()
            time.sleep(0.5)
    except Exception:
        pass

    for attempt in range(1, retries + 1):
        print(f"[STA] 尝试连接 {ssid}（第 {attempt}/{retries} 次）...")
        try:
            sta.connect(ssid, password)
        except Exception as e:
            print(f"[STA] 连接异常: {e}")
            time.sleep(1.5)
            continue

        start = time.time()
        while not sta.isconnected():
            if time.time() - start > timeout:
                print(f"[STA] {ssid} 连接超时（{timeout}s）")
                break
            time.sleep(0.3)

        if sta.isconnected():
            # 等 IP 分配
            start = time.time()
            while sta.ifconfig()[0] == '0.0.0.0':
                if time.time() - start > 8:
                    break
                time.sleep(0.3)
            print(f"[STA] 已连接 {ssid}, http://{sta.ifconfig()[0]}")
            return True

        time.sleep(1)

    return False


def auto_connect(force=False):
    cfg = load_config()
    networks = cfg.get("known_networks", [])
    networks.sort(key=lambda n: n.get("priority", 999))

    if not networks:
        print("[STA] 无已保存网络")
        return False

    for net in networks:
        ssid = net.get("ssid")
        pwd = net.get("password", "")
        if not ssid:
            continue
        if connect_sta(ssid, pwd):
            return True

    return False