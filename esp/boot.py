# boot.py — 核心初始化：先启动 AP，再尝试 STA
import gc
import time
import network

gc.collect()

bind_ip = "0.0.0.0"

try:
    ap = network.WLAN(network.AP_IF)
    # 1. 强制先启动 AP
    ap.config(essid="E6-Panel", password="e6epaper", authmode=network.AUTH_WPA_WPA2_PSK)
    ap.active(True)
    time.sleep(3)  # 等待网卡注册
    bind_ip = ap.ifconfig()[0]
    print('[BOOT] AP started:', bind_ip)

    # 2. 在 AP 存活的前提下，尝试连接 STA
    try:
        import json

        sta = network.WLAN(network.STA_IF)
        sta.active(True)

        with open("/networks.json", "r") as f:
            cfg = json.load(f)
        nets = cfg.get("known_networks", [])
        nets.sort(key=lambda n: n.get("priority", 999))

        for net in nets:
            ssid = net.get("ssid")
            pwd = net.get("password", "")
            if not ssid:
                continue

            print(f"[BOOT] STA 尝试连接 {ssid}...")
            try:
                sta.connect(ssid, pwd)
                # 短超时等待，别卡死整个开机
                for _ in range(20):  # 约 10 秒
                    if sta.isconnected():
                        break
                    time.sleep(0.5)

                if sta.isconnected():
                    sta_ip = sta.ifconfig()[0]
                    print(f"[BOOT] STA 连接成功: {sta_ip}")
                    # 只要 STA 连上了，就绑定 0.0.0.0，AP 和 STA 都能访问
                    bind_ip = "0.0.0.0"
                    break
                else:
                    print(f"[BOOT] STA {ssid} 连接超时")
                    sta.disconnect()
            except Exception as e:
                print(f"[BOOT] STA 连接异常: {e}")
                try:
                    sta.disconnect()
                except:
                    pass
    except Exception as e:
        print('[BOOT] STA 模块异常:', e)

except Exception as e:
    print('[BOOT] AP 初始化彻底失败:', e)

gc.collect()
print('[BOOT] 绑定目标 IP:', bind_ip)
print('[BOOT] free:', gc.mem_free())