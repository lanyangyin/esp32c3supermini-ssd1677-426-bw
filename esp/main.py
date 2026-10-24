# main.py — 仅启动 Web 服务器
import gc
import time
import os

gc.collect()
print('[MAIN] start')
print('[MAIN] 外部存储挂载已暂时注释，仅使用 Flash 内部存储')

# 动态判断应该绑定哪个 IP
import network
ap = network.WLAN(network.AP_IF)
sta = network.WLAN(network.STA_IF)

if sta.isconnected():
    bind_ip = "0.0.0.0"
    print('[MAIN] STA 已连接，绑定 0.0.0.0 (覆盖 AP + STA)')
    print('[MAIN] STA IP:', sta.ifconfig()[0])
elif ap.active():
    bind_ip = ap.ifconfig()[0]
    print('[MAIN] 仅 AP 模式，绑定:', bind_ip)
else:
    bind_ip = "0.0.0.0"

gc.collect()

# 启动 Web 服务器
print('[MAIN] 启动 Web 服务器...')
try:
    from web_server import start_server
    start_server(bind_ip)
except KeyboardInterrupt:
    print('[MAIN] Web 服务器被 Ctrl+C 中断，回到 REPL')
except Exception as e:
    print('[MAIN] web_server 失败:', e)
    try:
        import sys
        sys.print_exception(e)
    except Exception:
        pass