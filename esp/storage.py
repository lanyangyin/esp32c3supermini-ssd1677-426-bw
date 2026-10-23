#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""storage.py — 统一外部存储接口（W25Q / SD 卡）"""

import os
from machine import SPI, Pin, SDCard
from winbond import W25QFlash


class FatStorage:
    """文件系统通用操作，子类只需实现 _blockdev() 与 _do_mount()"""

    def __init__(self, mount_point):
        self.mount_point = mount_point.rstrip("/") or "/"
        self._mounted = False
        self._dev = None

    # ---- 子类实现 ----
    def _blockdev(self):
        raise NotImplementedError

    def _do_mount(self):
        os.mount(self._blockdev(), self.mount_point)

    # ---- 生命周期 ----
    def is_mounted(self):
        if self._mounted:
            try:
                os.statvfs(self.mount_point)
            except OSError:
                self._mounted = False
        return self._mounted

    def mount(self, auto_format=False):
        if self.is_mounted():
            return True
        self._mkdirs(self.mount_point)
        try:
            self._do_mount()
        except OSError:
            if not auto_format:
                raise OSError("挂载失败，可能未格式化；可用 mount(auto_format=True)")
            self.format()
            self._do_mount()
        self._mounted = True
        return True

    def umount(self):
        if self._mounted:
            try:
                os.umount(self.mount_point)
            finally:
                self._mounted = False

    def format(self):
        if self._mounted:
            self.umount()
        os.VfsFat.mkfs(self._blockdev())

    # ---- 路径 / 工具 ----
    def _p(self, name):
        name = name or ""
        mp = self.mount_point
        if name == mp or name.startswith(mp + "/"):
            return name
        name = name.lstrip("/")
        return mp + "/" + name if name else mp

    def _isdir(self, p):
        try:
            return bool(os.stat(p)[0] & 0x4000)
        except OSError:
            return False

    def _mkdirs(self, path):
        cur = ""
        for part in path.strip("/").split("/"):
            cur += "/" + part
            try:
                os.mkdir(cur)
            except OSError:
                pass

    def _require_mount(self):
        if not self.is_mounted():
            raise OSError("存储未挂载，请先 mount()")

    # ---- 信息 ----
    def df(self):
        self._require_mount()
        s = os.statvfs(self.mount_point)
        bs = s[1] or s[0]
        total, free = bs * s[2], bs * s[3]
        return {"total": total, "used": total - free, "free": free,
                "total_kb": total // 1024, "used_kb": (total - free) // 1024,
                "free_kb": free // 1024}

    # ---- 目录 ----
    def ls(self, path="/"):
        self._require_mount()
        return os.listdir(self._p(path))

    def mkdir(self, path):
        self._require_mount()
        os.mkdir(self._p(path))

    def rmdir(self, path):
        self._require_mount()
        os.rmdir(self._p(path))

    def isdir(self, path):
        self._require_mount()
        return self._isdir(self._p(path))

    def walk(self, path="/"):
        self._require_mount()
        base = self._p(path)
        out = []
        for name in os.listdir(base):
            sub = base + "/" + name
            if self._isdir(sub):
                out.extend(self.walk(sub))
            else:
                out.append(self._rel(sub))
        return out

    def _rel(self, full):
        mp = self.mount_point
        return full[len(mp) + 1:] if full.startswith(mp + "/") else full

    def rmtree(self, path):
        self._require_mount()
        full = self._p(path)
        for name in os.listdir(full):
            sub = full + "/" + name
            if self._isdir(sub):
                self.rmtree(sub)
            else:
                os.remove(sub)
        os.rmdir(full)

    # ---- 文件 ----
    def exists(self, path):
        self._require_mount()
        try:
            os.stat(self._p(path)); return True
        except OSError:
            return False

    def size(self, path):
        self._require_mount()
        return os.stat(self._p(path))[6]

    def read(self, path):
        self._require_mount()
        with open(self._p(path), "rb") as f:
            return f.read()

    def read_text(self, path):
        self._require_mount()
        with open(self._p(path), "r") as f:
            return f.read()

    def write(self, path, data, mode=None):
        self._require_mount()
        if mode is None:
            mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
        with open(self._p(path), mode) as f:
            f.write(data)

    def append(self, path, data):
        self._require_mount()
        mode = "ab" if isinstance(data, (bytes, bytearray)) else "a"
        with open(self._p(path), mode) as f:
            f.write(data)

    def remove(self, path):
        self._require_mount()
        os.remove(self._p(path))
    rm = remove

    def rename(self, old, new):
        self._require_mount()
        os.rename(self._p(old), self._p(new))
    mv = rename


# ===================== 后端：W25Q =====================
class W25QStorage(FatStorage):
    def __init__(self, mount_point="/external",
                 spi_id=2, sck=19, mosi=22, miso=21, cs=5,
                 baudrate=2_000_000, software_reset=True):
        super().__init__(mount_point)
        self._spi = SPI(spi_id, baudrate=baudrate,
                        sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self._cs = Pin(cs, Pin.OUT, value=1)
        self._baud = baudrate
        self._sw_reset = software_reset

    def _blockdev(self):
        if self._dev is None:
            self._dev = W25QFlash(spi=self._spi, cs=self._cs,
                                  baud=self._baud,
                                  software_reset=self._sw_reset)
        return self._dev

    def info(self):
        d = self._blockdev()
        return {"type": "W25Q", "manufacturer": d.manufacturer,
                "memory_type": d.mem_type, "device_type": d.device,
                "capacity": d.capacity, "mount_point": self.mount_point,
                "mounted": self.is_mounted()}


# ===================== 后端：SD 卡 =====================
class SdStorage(FatStorage):
    def __init__(self, mount_point="/sd",
                 slot=2, sck=19, mosi=22, miso=21, cs=5,
                 freq=20_000_000):
        super().__init__(mount_point)
        self._slot = slot
        self._pins = dict(sck=Pin(sck), mosi=Pin(mosi),
                          miso=Pin(miso), cs=Pin(cs))
        self._freq = freq

    def _blockdev(self):
        if self._dev is None:
            self._dev = SDCard(slot=self._slot, width=1,
                               freq=self._freq, **self._pins)
        return self._dev

    def info(self):
        d = self._blockdev()
        return {"type": "SDCard", "capacity": d.info()[1] if hasattr(d, "info") else None,
                "mount_point": self.mount_point, "mounted": self.is_mounted()}