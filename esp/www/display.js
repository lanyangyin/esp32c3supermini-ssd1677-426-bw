// www/display.js
(function(){
'use strict';

window.E6Display = {
    clearGhosting: async function(mode, statusCallback) {
        if (statusCallback) statusCallback('正在清除残影（全刷）...', 'busy');
        try {
            const r = await fetch('/clear?mode=' + (mode || 'full'), { cache: 'no-store' });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            if (statusCallback) statusCallback('残影清除完成 ✓', 'ok');
            return true;
        } catch (e) {
            if (statusCallback) statusCallback('清除失败：' + e.message, 'err');
            return false;
        }
    },

    uploadToScreen: async function(binData, mode, statusCallback, bounds) {
        if (statusCallback) statusCallback('上传到屏幕（' + mode + '）…', 'busy');
        try {
            let url = '/upload?mode=' + encodeURIComponent(mode);
            if (bounds && mode === 'partial') {
                url += `&x0=${bounds.x0}&y0=${bounds.y0}&x1=${bounds.x1}&y1=${bounds.y1}`;
            }
            const r = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/octet-stream'},
                body: binData
            });
            const t = await r.text();
            if (!r.ok) throw new Error('HTTP ' + r.status + ': ' + t);
            if (statusCallback) statusCallback('已显示到屏幕（' + t + '）', 'ok');
            return true;
        } catch (e) {
            if (statusCallback) statusCallback('上传失败：' + e.message, 'err');
            return false;
        }
    }
};
})();