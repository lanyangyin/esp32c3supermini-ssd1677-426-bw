# main.py
import gc
gc.collect()
print('[MAIN] start')

try:
    import wifi_manager
    print('[MAIN] 连接 STA...')
    wifi_manager.auto_connect()
    wifi_manager.start_ap()
    print('[MAIN] WiFi 初始化完成')
except Exception as e:
    print('[MAIN] WiFi 初始化失败:', e)

# 固定显示网络信息
try:
    import boot_screen
    boot_screen.show_netinfo()
except KeyboardInterrupt:
    print('[MAIN] 显示被中断')
except Exception as e:
    print('[MAIN] 显示失败:', e)

gc.collect()

# 启动 Web 服务器
print('[MAIN] 启动 Web 服务器...')
try:
    from web_server import start_server
    start_server()
except KeyboardInterrupt:
    pass