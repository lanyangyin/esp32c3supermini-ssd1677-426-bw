# boot.py — 基础启动，按需挂载存储，快速、可中断
#
# 所有耗时操作（开机画面、WiFi 连接、Web 服务器）都在 main.py 里，
# 每一阶段都可以 Ctrl+C 中断回到 REPL。

import gc
import os

try:
    import storage
    print('[BOOT] Storage module loaded.')
    # 注意：此处不强制挂载，交由 main.py 或用户按需挂载
except Exception as e:
    print('[BOOT] storage error:', e)

# 确保必要的 Flash 目录存在（兜底用）
for d in ("/bins", "/bgs", "/www/fonts"):
    try:
        os.mkdir(d)
    except OSError:
        pass

gc.collect()
print('[BOOT] 初始化完成, free=%d' % gc.mem_free())