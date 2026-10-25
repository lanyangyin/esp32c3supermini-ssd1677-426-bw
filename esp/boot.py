# boot.py — 先起 AP（配置入口永远在线），再扫描 STA（只连已知网络）
import gc
import time
import network

gc.collect()

AP_SSID = "E6-Panel"
AP_PASS = "e6epaper"
bind_ip = "0.0.0.0"

print('[BOOT] boot start')

# ============================================================
# 1. 先启动 AP（配置入口永远在线）
# ============================================================
ap_ip = None
try:
    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PASS,
              authmode=network.AUTH_WPA_WPA2_PSK)
    ap.active(True)
    # 轮询等 AP IP 就绪（替代固定 sleep 3s）
    for _ in range(20):
        ip = ap.ifconfig()[0]
        if ip and ip != '0.0.0.0':
            ap_ip = ip
            break
        time.sleep(0.5)
    if ap_ip:
        print('[BOOT] AP started:', ap_ip)
    else:
        ap_ip = ap.ifconfig()[0]
        print('[BOOT] AP started (IP delayed):', ap_ip)
except Exception as e:
    print('[BOOT] AP init fail:', e)

# ============================================================
# 2. 读已保存的网络
# ============================================================
nets = []
try:
    import json
    with open("/networks.json", "r") as f:
        cfg = json.load(f)
    nets = cfg.get("known_networks", [])
    nets.sort(key=lambda n: n.get("priority", 999))
    print('[BOOT] 已保存网络:', len(nets))
except Exception as e:
    print('[BOOT] 读配置失败:', e)
    nets = []

# ============================================================
# 3. STA 预热 + 扫描 + 匹配 + 连接
# ============================================================
# 流程：
#   1) sta.active(True) + sleep(2) —— 预热 PHY（冷启动关键）
#   2) sta.scan() —— 一次扫描，得到可见 SSID 集合
#   3) 只遍历 "已保存 ∩ 可见" —— 没匹配就完全不连
#   4) 逐个 connect，任一成功即停（总超时 20 秒）
sta_ok = False
sta_ip = None

if nets:
    try:
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        time.sleep(2)  # 预热 PHY

        # --- 扫描 ---
        scanned = set()
        try:
            print('[BOOT] STA 扫描中…')
            results = sta.scan()
            for r in results:
                raw = r[0]
                ssid = raw.decode() if isinstance(raw, bytes) else raw
                if ssid:
                    scanned.add(ssid)
            print('[BOOT] 扫描到 %d 个网络' % len(scanned))
        except Exception as e:
            print('[BOOT] 扫描失败:', e)

        # --- 匹配 ---
        if not scanned:
            print('[BOOT] 扫描结果为空，跳过 STA 连接')
        else:
            to_try = [n for n in nets if n.get('ssid') in scanned]
            if not to_try:
                print('[BOOT] 扫描结果中没有已保存的网络，跳过 STA')
            else:
                print('[BOOT] 匹配到 %d 个已保存网络，开始尝试' % len(to_try))
                deadline = time.time() + 20

                for net in to_try:
                    if time.time() >= deadline:
                        print('[BOOT] STA 总超时，停止尝试')
                        break

                    ssid = net['ssid']
                    pwd = net.get('password', '')
                    print('[BOOT] STA 尝试:', ssid)

                    try:
                        try:
                            if sta.isconnected():
                                sta.disconnect()
                                time.sleep(0.3)
                        except Exception:
                            pass

                        sta.connect(ssid, pwd)

                        # 等 isconnected + IP 分配
                        while time.time() < deadline:
                            if sta.isconnected():
                                ip = sta.ifconfig()[0]
                                if ip and ip != '0.0.0.0':
                                    sta_ok = True
                                    sta_ip = ip
                                    print('[BOOT] STA 连接成功:', ip)
                                    break
                            time.sleep(0.3)

                        if sta_ok:
                            break

                        print('[BOOT] STA %s 连接失败' % ssid)
                        try:
                            sta.disconnect()
                        except Exception:
                            pass
                        time.sleep(0.3)
                    except Exception as e:
                        print('[BOOT] STA %s 异常: %s' % (ssid, e))
                        try:
                            sta.disconnect()
                        except Exception:
                            pass
    except Exception as e:
        print('[BOOT] STA 模块异常:', e)

# ============================================================
# 4. bind_ip：只要有 AP 或 STA 在，就 bind 0.0.0.0
# ============================================================
# 这样无论 AP 还是 STA 起，都能访问；STA 通过网页连上后也无需重启
if ap_ip or sta_ok:
    bind_ip = "0.0.0.0"
    if sta_ok:
        print('[BOOT] STA + AP 双模式，绑定 0.0.0.0')
    else:
        print('[BOOT] 仅 AP 模式，绑定 0.0.0.0（AP 客户端可通过 %s 访问）' % ap_ip)
else:
    bind_ip = "0.0.0.0"
    print('[BOOT] AP/STA 均未起，绑定 0.0.0.0 (可能无法访问)')

gc.collect()
print('[BOOT] 绑定目标 IP:', bind_ip)
print('[BOOT] free:', gc.mem_free())