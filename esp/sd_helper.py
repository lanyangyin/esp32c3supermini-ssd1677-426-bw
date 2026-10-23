# sd_helper.py — SD 卡挂载（硬件 SPI，slot=2）
import os
import time

SD_MOUNT = '/sd'
SD_ROOT = '/sd/e6'
SD_BINS = '/sd/e6/bins'
SD_BGS = '/sd/e6/bgs'
SD_FONTS = '/sd/e6/fonts'
FLASH_BINS = '/bins'
FLASH_BGS = '/bgs'

_mounted = False
_sd_obj = None


def mount_sd():
    global _mounted, _sd_obj
    if _mounted:
        return True

    try:
        os.listdir(SD_MOUNT)
        _mounted = True
        return True
    except OSError:
        pass

    for attempt in range(3):
        try:
            import machine
            sd = machine.SDCard(
                slot=2,
                sck=machine.Pin(10),
                mosi=machine.Pin(7),
                miso=machine.Pin(1),
                cs=machine.Pin(5),
                freq=400_000
            )
            os.mount(sd, SD_MOUNT)
            _sd_obj = sd
            _mounted = True
            print('[SD] mounted at', SD_MOUNT, '(hardware SPI)')
            return True
        except Exception as e:
            print('[SD] attempt', attempt + 1, 'failed:', e)
            time.sleep(0.5)
    return False


def ensure_dirs():
    if mount_sd():
        for d in (SD_ROOT, SD_BINS, SD_BGS, SD_FONTS):
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