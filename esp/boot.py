# boot.py — 只做 SD 挂载（快速、可中断）
#
# 所有耗时操作（开机画面、WiFi 连接、Web 服务器）都在 main.py 里，
# 每一阶段都可以 Ctrl+C 中断回到 REPL。

import gc

try:
    import sd_helper
    sd_helper.mount_sd()
    print('[BOOT] SD:', 'yes' if sd_helper.is_sd_active() else 'no')
except Exception as e:
    print('[BOOT] sd_helper:', e)

gc.collect()
print('[BOOT] 初始化完成, free=%d' % gc.mem_free())