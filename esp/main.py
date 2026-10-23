# main.py — 编排：读模式 → 01/11 先显示 → WiFi → 10 后显示 → Web
#
# 顺序设计：
#   - 00: 什么都不做，直接启动 Web
#   - 01: 先显示随机 bin（30~60s），再连 STA，再启动 Web
#   - 10: 先连 STA，再显示网络信息，再启动 Web（需要 STA 的 IP）
#   - 11: 先显示指定 bin（30~60s），再连 STA，再启动 Web
#
# Ctrl+C 行为：
#   - 开机画面渲染中被中断 → 跳过显示，进下一阶段
#   - WiFi 连接中被中断   → 跳过 STA，进 AP + Web
#   - Web 服务器中按两次   → 回到 REPL

import gc
gc.collect()
print('[MAIN] start')

# ---------- 1. 读开机模式 ----------
mode = 0
try:
    import boot_screen
    mode = boot_screen._read_mode()
    if mode is None:
        mode = 0
    print('[MAIN] 开机模式: %d%d (mode=%d)'
          % ((mode >> 1) & 1, mode & 1, mode))
except Exception as e:
    print('[MAIN] 读模式失败:', e)
    mode = 0

# ---------- 2. 01/11 先显示（不依赖 WiFi） ----------
if mode in (1, 3):
    print('[MAIN] 先显示 bin（模式 %d），之后连 STA' % mode)
    try:
        import boot_screen
        boot_screen.run_mode(mode)
    except KeyboardInterrupt:
        print('[MAIN] 显示被 Ctrl+C 中断')
    except Exception as e:
        print('[MAIN] 显示失败:', e)
        try:
            import sys
            sys.print_exception(e)
        except Exception:
            pass

gc.collect()

# ---------- 3. WiFi 初始化 ----------
try:
    import network
    import time
    import wifi_manager

    print('[MAIN] 连接 STA...')
    wifi_manager.auto_connect()

    sta = network.WLAN(network.STA_IF)
    if not sta.isconnected():
        for _ in range(10):
            if sta.isconnected():
                break
            time.sleep(0.5)

    if sta.isconnected():
        print('[MAIN] STA:', 'http://' + sta.ifconfig()[0])
    else:
        print('[MAIN] STA 未连接，仅 AP 模式')

    wifi_manager.start_ap()
    print('[MAIN] AP: http://192.168.4.1')
except KeyboardInterrupt:
    print('[MAIN] WiFi 初始化被 Ctrl+C 中断')
except Exception as e:
    print('[MAIN] WiFi 初始化失败:', e)
    try:
        import sys
        sys.print_exception(e)
    except Exception:
        pass

# ---------- 4. 10 后显示（需要 WiFi 状态） ----------
if mode == 2:
    print('[MAIN] 显示网络信息（模式 10）')
    try:
        import boot_screen
        boot_screen.run_mode(2)
    except KeyboardInterrupt:
        print('[MAIN] 显示被 Ctrl+C 中断')
    except Exception as e:
        print('[MAIN] 显示失败:', e)
        try:
            import sys
            sys.print_exception(e)
        except Exception:
            pass

gc.collect()

# ---------- 5. Web 服务器 ----------
print('[MAIN] 启动 Web 服务器...')
try:
    from web_server import start_server
    start_server()
except KeyboardInterrupt:
    print('[MAIN] Web 服务器被 Ctrl+C 中断，回到 REPL')
except Exception as e:
    print('[MAIN] web_server 失败:', e)
    try:
        import sys
        sys.print_exception(e)
    except Exception:
        pass