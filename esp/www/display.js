// www/display.js — E6 公用屏幕刷新工具
(function(){
'use strict';

window.E6Display = {
    // 清除残影：全屏刷白
    clearGhosting: async function(mode, statusCallback) {
        if (statusCallback) statusCallback('正在清除残影（全刷）...', 'busy');
        try {
            const r = await fetch('/clear?mode=' + (mode || 'full'), { cache: 'no-store' });
            if (!r.ok) {
                const t = await r.text();
                throw new Error('HTTP ' + r.status + ': ' + t);
            }
            if (statusCallback) statusCallback('残影清除完成 ✓', 'ok');
            return true;
        } catch (e) {
            if (statusCallback) statusCallback('清除失败：' + e.message, 'err');
            return false;
        }
    },

    // 上传图像并显示
    uploadToScreen: async function(binData, mode, statusCallback) {
        if (statusCallback) statusCallback('上传到屏幕（' + (mode || 'full') + '）…', 'busy');
        try {
            const r = await fetch('/upload?mode=' + encodeURIComponent(mode || 'full'), {
                method: 'POST',
                headers: {'Content-Type': 'application/octet-stream'},
                body: binData
            });
            const t = await r.text();
            if (!r.ok) throw new Error('HTTP ' + r.status + ': ' + t);
            if (statusCallback) statusCallback('已显示到屏幕（' + t + ' 字节）', 'ok');
            return true;
        } catch (e) {
            if (statusCallback) statusCallback('上传失败：' + e.message, 'err');
            return false;
        }
    }
};
})();