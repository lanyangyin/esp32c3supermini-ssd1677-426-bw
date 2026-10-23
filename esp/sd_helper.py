# sd_helper.py — 基于 storage.py 的封装，统一挂载到 /sd
import os
import time
from storage import SdStorage

SD_MOUNT = '/sd'
SD_BINS = '/sd/bins'
SD_BGS = '/sd/bgs'
SD_FONTS = '/sd/fonts'
FLASH_BINS = '/bins'
FLASH_BGS = '/bgs'

_mounted = False
_sd = None

def mount_sd():
    global _mounted, _sd
    if _mounted:
        return True

    try:
        os.listdir(SD_MOUNT)
        _mounted = True
        return True
    except OSError:
        pass

    if _sd is None:
        # 复用原工程的 SPI 引脚配置
        _sd = SdStorage(
            mount_point=SD_MOUNT,
            slot=2,
            sck=10,
            mosi=7,
            miso=1,
            cs=5,
            freq=400_000
        )

    try:
        _sd.mount(auto_format=True)
        _mounted = True
        print('[SD] mounted at', SD_MOUNT, '(hardware SPI)')
        return True
    except Exception as e:
        print('[SD] mount failed:', e)
        return False


def ensure_dirs():
    if mount_sd():
        for d in (SD_BINS, SD_BGS, SD_FONTS):
            try:
                os.mkdir(d)
            except OSError:
                pass
    for d in (FLASH_BINS, FLASH_BGS):
        try:
            os.mkdir(d)
        except OSError:
            pass


def is_sd_active():
    return _mounted


def get_storage_paths(storage):
    if storage == 'sd':
        if not _mounted:
            return (None, None)
        return (SD_BINS, SD_BGS)
    return (FLASH_BINS, FLASH_BGS)


def is_available(storage):
    if storage == 'sd':
        return _mounted
    return True