# e6_tool.py — E6 黑白图片转换工具 (800×480 1bpp)
import sys
import io
import copy
import json
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QLineEdit, QGroupBox,
    QComboBox, QSlider, QCheckBox, QScrollArea,
)

# ★ 从 e6_image_core 包导入
from e6_image_core.dithering import (
    DITHER_MODES, dither, pack, block_to_image,
)
from e6_image_core.tone import apply_all

# ---------- 配置路径 ----------
CONFIG_PATH = Path(__file__).parent / "e6_image_core" / "e6_tool_config.json"
LEGACY_CONFIG_PATH = Path.home() / ".e6_tool_config.json"

FIT_MODES = [
    ("铺满（裁剪）", "cover"),
    ("完整显示（白边）", "contain"),
]

CANVAS_W, CANVAS_H = 800, 480
FRAME_BYTES = CANVAS_W * CANVAS_H // 8  # 48000


# ---------- 配置读写 ----------

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    if LEGACY_CONFIG_PATH.exists():
        try:
            with open(LEGACY_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            save_config(data)
            print(f"[config] migrated {LEGACY_CONFIG_PATH} -> {CONFIG_PATH}")
            return data
        except Exception:
            pass

    return {}


def save_config(data):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- 等比缩放 + 偏移裁切 ----------

def resize_fit(img, w, h, mode="cover", x_offset=None, y_offset=None):
    img = img.convert("RGB")
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGB", (w, h), (255, 255, 255))

    if mode == "cover":
        scale = max(w / iw, h / ih)
        nw = max(w, int(round(iw * scale)))
        nh = max(h, int(round(ih * scale)))
        resized = img.resize((nw, nh), Image.LANCZOS)
        max_x = nw - w
        max_y = nh - h
        ox = max_x // 2 if x_offset is None else max(0, min(max_x, int(x_offset)))
        oy = max_y // 2 if y_offset is None else max(0, min(max_y, int(y_offset)))
        return resized.crop((ox, oy, ox + w, oy + h))

    scale = min(w / iw, h / ih)
    nw = min(w, max(1, int(round(iw * scale))))
    nh = min(h, max(1, int(round(ih * scale))))
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    canvas.paste(resized, ((w - nw) // 2, (h - nh) // 2))
    return canvas


def cover_limits(img, w, h):
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw = max(w, int(round(iw * scale)))
    nh = max(h, int(round(ih * scale)))
    return nw - w, nh - h


def pil_to_qpixmap(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return QPixmap.fromImage(QImage.fromData(buf.getvalue(), "PNG"))


def clean_ip(text):
    ip = (text or "").strip()
    if ip.startswith("http://"):
        ip = ip[7:]
    elif ip.startswith("https://"):
        ip = ip[8:]
    return ip.rstrip("/")


# ---------- 导航图 ----------

def make_nav_image(rotated, fit_mode, x_offset, y_offset, nav_max_w=300, nav_max_h=200):
    W, H = CANVAS_W, CANVAS_H
    iw, ih = rotated.size

    if fit_mode == "cover":
        scale = max(W / iw, H / ih)
        nw = max(W, int(round(iw * scale)))
        nh = max(H, int(round(ih * scale)))
        full_w, full_h = nw, nh
    else:
        full_w, full_h = W, H

    nav_scale = min(nav_max_w / full_w, nav_max_h / full_h)
    nav_w = max(1, int(full_w * nav_scale))
    nav_h = max(1, int(full_h * nav_scale))

    nav = Image.new("RGB", (nav_w, nav_h), (210, 210, 210))

    if fit_mode == "cover":
        small = rotated.resize((int(iw * scale * nav_scale),
                                int(ih * scale * nav_scale)), Image.LANCZOS)
        nav.paste(small, (0, 0))

        view_x = int(x_offset * nav_scale)
        view_y = int(y_offset * nav_scale)
        view_w = max(1, int(W * nav_scale))
        view_h = max(1, int(H * nav_scale))

        overlay = Image.new("RGBA", nav.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([0, 0, nav_w, view_y], fill=(0, 0, 0, 130))
        od.rectangle([0, view_y + view_h, nav_w, nav_h], fill=(0, 0, 0, 130))
        od.rectangle([0, view_y, view_x, view_y + view_h], fill=(0, 0, 0, 130))
        od.rectangle([view_x + view_w, view_y, nav_w, view_y + view_h], fill=(0, 0, 0, 130))
        nav = Image.alpha_composite(nav.convert("RGBA"), overlay).convert("RGB")

        d = ImageDraw.Draw(nav)
        d.rectangle([view_x, view_y, view_x + view_w - 1, view_y + view_h - 1],
                    outline=(255, 50, 50), width=2)
    else:
        s = min(W / iw, H / ih)
        nw = max(1, int(round(iw * s)))
        nh = max(1, int(round(ih * s)))
        ox = (W - nw) // 2
        oy = (H - nh) // 2
        small = rotated.resize((int(nw * nav_scale), int(nh * nav_scale)), Image.LANCZOS)
        nav.paste(small, (int(ox * nav_scale), int(oy * nav_scale)))

        d = ImageDraw.Draw(nav)
        d.rectangle([0, 0, nav_w - 1, nav_h - 1], outline=(150, 150, 150), width=1)

    return nav


# ---------- 网络线程 ----------

class CheckWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, ip, timeout=4):
        super().__init__()
        self.ip = ip
        self.timeout = timeout

    def run(self):
        try:
            import requests
        except ImportError:
            self.finished.emit(False, "缺少 requests 库")
            return
        try:
            r = requests.get(f"http://{self.ip}/", timeout=self.timeout)
            if r.status_code == 200:
                self.finished.emit(True, "设备在线")
            else:
                self.finished.emit(False, f"HTTP {r.status_code}")
        except Exception as e:
            msg = str(e)
            if "timeout" in msg.lower() or "timed out" in msg.lower():
                msg = "连接超时"
            elif "refused" in msg.lower():
                msg = "连接被拒绝"
            elif "getaddrinfo" in msg.lower():
                msg = "地址解析失败"
            self.finished.emit(False, msg)


class UploadWorker(QThread):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, url, data):
        super().__init__()
        self.url = url
        self.data = data

    def run(self):
        try:
            import requests
            r = requests.post(self.url, data=self.data,
                              headers={"Content-Type": "application/octet-stream"},
                              timeout=60)
            self.finished.emit(f"{r.status_code} {r.text}")
        except Exception as e:
            self.failed.emit(str(e))


# ---------- 滑块行 ----------

class SliderRow(QWidget):
    def __init__(self, label, min_v, max_v, default, scale=100):
        super().__init__()
        self.scale = scale
        self.default = default
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label), 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(min_v, max_v)
        self.slider.setValue(default)
        layout.addWidget(self.slider, 1)
        self.value = QLabel(self._fmt(default))
        self.value.setFixedWidth(50)
        layout.addWidget(self.value, 0)
        self.slider.valueChanged.connect(lambda v: self.value.setText(self._fmt(v)))

    def _fmt(self, v):
        return f"{v / self.scale:.2f}" if self.scale != 1 else str(v)

    def get(self):
        return self.slider.value() / self.scale

    def reset(self):
        self.slider.setValue(self.default)


# ---------- 主窗口 ----------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E6 黑白图片转换工具 (800×480)")
        self.resize(1400, 980)

        self.config = load_config()
        self.src_img = None
        self.rotation = 0
        self.worker = None
        self.check_worker = None
        self._upload_ip = ""

        self.fitted_img = None
        self.adjusted_img = None
        self.dithered_img = None
        self.last_block = None

        self.max_x = 0
        self.max_y = 0
        self._updating_offsets = False

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # --- 左侧面板 ---
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFixedWidth(380)
        left_w = QWidget()
        ll = QVBoxLayout(left_w)

        self.btn_choose = QPushButton("选择图片…")
        self.btn_choose.clicked.connect(self.on_choose)
        ll.addWidget(self.btn_choose)

        canvas_group = QGroupBox("画布 800×480")
        cg = QVBoxLayout(canvas_group)
        fit_row = QHBoxLayout()
        fit_row.addWidget(QLabel("填充方式:"))
        self.fit_combo = QComboBox()
        for name, _ in FIT_MODES:
            self.fit_combo.addItem(name)
        fit_row.addWidget(self.fit_combo)
        cg.addLayout(fit_row)
        ll.addWidget(canvas_group)

        offset_group = QGroupBox("裁切偏移（铺满模式）")
        og = QVBoxLayout(offset_group)

        x_row = QHBoxLayout()
        x_row.addWidget(QLabel("X 偏移:"))
        self.x_slider = QSlider(Qt.Horizontal)
        self.x_slider.setRange(0, 0)
        self.x_slider.setValue(0)
        x_row.addWidget(self.x_slider, 1)
        self.x_label = QLabel("0")
        self.x_label.setFixedWidth(50)
        x_row.addWidget(self.x_label)
        og.addLayout(x_row)

        y_row = QHBoxLayout()
        y_row.addWidget(QLabel("Y 偏移:"))
        self.y_slider = QSlider(Qt.Horizontal)
        self.y_slider.setRange(0, 0)
        self.y_slider.setValue(0)
        y_row.addWidget(self.y_slider, 1)
        self.y_label = QLabel("0")
        self.y_label.setFixedWidth(50)
        y_row.addWidget(self.y_label)
        og.addLayout(y_row)

        self.btn_center = QPushButton("居中")
        self.btn_center.clicked.connect(self.on_center)
        og.addWidget(self.btn_center)

        nav_row = QHBoxLayout()
        self.nav_label = QLabel()
        self.nav_label.setAlignment(Qt.AlignCenter)
        self.nav_label.setFixedSize(300, 200)
        self.nav_label.setStyleSheet("border: 1px solid #bbb; background: #eee;")
        nav_row.addWidget(self.nav_label)
        nav_row.addStretch()
        og.addLayout(nav_row)

        ll.addWidget(offset_group)

        tone_group = QGroupBox("色调调整")
        tl = QVBoxLayout(tone_group)
        self.exp = SliderRow("曝光", -100, 100, 0)
        self.con = SliderRow("对比度", -100, 100, 0)
        self.cla = SliderRow("清晰度", 0, 200, 0)
        self.sha = SliderRow("阴影", 0, 100, 0)
        self.hig = SliderRow("高光", 0, 100, 0)
        for s in (self.exp, self.con, self.cla, self.sha, self.hig):
            tl.addWidget(s)
        ll.addWidget(tone_group)

        dither_group = QGroupBox("黑白抖动设置")
        dl = QVBoxLayout(dither_group)
        dither_row = QHBoxLayout()
        dither_row.addWidget(QLabel("算法:"))
        self.dither_combo = QComboBox()
        for name, _ in DITHER_MODES:
            self.dither_combo.addItem(name)
        dither_row.addWidget(self.dither_combo)
        dl.addLayout(dither_row)

        self.serp_check = QCheckBox("蛇形扫描")
        self.serp_check.setChecked(True)
        dl.addWidget(self.serp_check)
        ll.addWidget(dither_group)

        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("重置调整")
        self.btn_reset.clicked.connect(self.on_reset)
        btn_row.addWidget(self.btn_reset)
        self.btn_rotate = QPushButton("旋转 90°")
        self.btn_rotate.clicked.connect(self.on_rotate)
        btn_row.addWidget(self.btn_rotate)
        ll.addLayout(btn_row)

        self.btn_factory = QPushButton("恢复默认设置")
        self.btn_factory.clicked.connect(self.on_factory_reset)
        ll.addWidget(self.btn_factory)

        self.btn_export = QPushButton("导出 image.bin（48000 字节）")
        self.btn_export.clicked.connect(self.on_export)
        self.btn_export.setEnabled(False)
        ll.addWidget(self.btn_export)

        ug = QGroupBox("上传到设备（AP 模式）")
        ul = QVBoxLayout(ug)

        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("设备 IP:"))
        self.edit_ip = QLineEdit("192.168.4.1")
        self.edit_ip.setPlaceholderText("例如 192.168.4.1")
        ip_row.addWidget(self.edit_ip)
        ul.addLayout(ip_row)

        self.lbl_conn_status = QLabel("就绪")
        self.lbl_conn_status.setStyleSheet("color: #888; font-weight: bold;")
        self.lbl_conn_status.setWordWrap(True)
        ul.addWidget(self.lbl_conn_status)

        self.btn_upload = QPushButton("检查连接并上传")
        self.btn_upload.clicked.connect(self.on_upload)
        self.btn_upload.setEnabled(False)
        ul.addWidget(self.btn_upload)

        ll.addWidget(ug)

        ll.addStretch()
        left.setWidget(left_w)
        main_layout.addWidget(left)

        # --- 右侧预览 ---
        right = QWidget()
        rl = QVBoxLayout(right)
        grid = QGridLayout()
        self.lbl_input = self._mk_label()
        self.lbl_adjusted = self._mk_label()
        self.lbl_dithered = self._mk_label()

        grid.addWidget(self._title("Input（旋转+填充后）"), 0, 0)
        grid.addWidget(self._title("Adjusted（色调调整）"), 0, 1)
        grid.addWidget(self._title("Dithered（黑白抖动）"), 2, 0)
        grid.addWidget(self.lbl_input, 1, 0)
        grid.addWidget(self.lbl_adjusted, 1, 1)
        grid.addWidget(self.lbl_dithered, 3, 0)

        rl.addLayout(grid)
        self.lbl_info = QLabel("就绪")
        rl.addWidget(self.lbl_info)
        main_layout.addWidget(right, 1)

        self.statusBar().showMessage("请选择一张图片")

        for s in (self.exp, self.con, self.cla, self.sha, self.hig):
            s.slider.valueChanged.connect(self.on_preview)
        self.dither_combo.currentIndexChanged.connect(self.on_preview)
        self.serp_check.stateChanged.connect(self.on_preview)
        self.fit_combo.currentIndexChanged.connect(self.on_fit_changed)
        self.x_slider.valueChanged.connect(self.on_offset_changed)
        self.y_slider.valueChanged.connect(self.on_offset_changed)

        self.apply_config(self.config)

    def _mk_label(self):
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFixedSize(400, 240)
        lbl.setStyleSheet("border: 1px solid #ccc; background: #fafafa;")
        return lbl

    def _title(self, text):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-weight: bold; padding: 4px;")
        return lbl

    def current_size(self):
        return (CANVAS_W, CANVAS_H)

    def current_fit_mode(self):
        return FIT_MODES[self.fit_combo.currentIndex()][1]

    def get_rotated_img(self):
        if self.src_img is None:
            return None
        img = self.src_img
        if self.rotation:
            img = img.rotate(-self.rotation, expand=True)
        return img

    def _update_offset_ranges(self):
        rotated = self.get_rotated_img()
        if rotated is None:
            return
        W, H = self.current_size()
        mx, my = cover_limits(rotated, W, H)
        self.max_x = mx
        self.max_y = my

        self._updating_offsets = True
        self.x_slider.setRange(0, mx)
        self.y_slider.setRange(0, my)
        self.x_slider.setValue(mx // 2)
        self.y_slider.setValue(my // 2)
        self.x_label.setText(str(mx // 2))
        self.y_label.setText(str(my // 2))
        self._updating_offsets = False

        enabled = self.current_fit_mode() == "cover"
        self.x_slider.setEnabled(enabled)
        self.y_slider.setEnabled(enabled)
        self.btn_center.setEnabled(enabled)

    def on_fit_changed(self):
        rotated = self.get_rotated_img()
        if rotated is not None:
            self._update_offset_ranges()
        self.on_preview()

    def on_offset_changed(self):
        if self._updating_offsets:
            return
        self.x_label.setText(str(self.x_slider.value()))
        self.y_label.setText(str(self.y_slider.value()))
        self.on_preview()

    def on_center(self):
        self._updating_offsets = True
        self.x_slider.setValue(self.max_x // 2)
        self.y_slider.setValue(self.max_y // 2)
        self.x_label.setText(str(self.max_x // 2))
        self.y_label.setText(str(self.max_y // 2))
        self._updating_offsets = False
        self.on_preview()
        self.statusBar().showMessage("已居中裁切")

    # ---------- 配置 ----------

    def _generate_bin(self):
        if self.last_block is None:
            raise RuntimeError("请先生成黑白抖动图")
        return pack(self.last_block)

    def apply_config(self, cfg):
        self.exp.slider.setValue(cfg.get("exposure", 0))
        self.con.slider.setValue(cfg.get("contrast", 0))
        self.cla.slider.setValue(cfg.get("clarity", 0))
        self.sha.slider.setValue(cfg.get("shadow", 0))
        self.hig.slider.setValue(cfg.get("highlight", 0))
        self.fit_combo.setCurrentIndex(cfg.get("fit_index", 0))

        self.dither_combo.setCurrentIndex(cfg.get("dither_index", 0))
        self.serp_check.setChecked(cfg.get("serpentine", True))

        device_ip = cfg.get("device_ip", "").strip()
        if not device_ip and "url" in cfg:
            device_ip = clean_ip(cfg["url"])
        if not device_ip:
            device_ip = "192.168.4.1"
        self.edit_ip.setText(device_ip)

    def save_current_config(self):
        cfg = {
            "exposure": self.exp.slider.value(),
            "contrast": self.con.slider.value(),
            "clarity": self.cla.slider.value(),
            "shadow": self.sha.slider.value(),
            "highlight": self.hig.slider.value(),
            "fit_index": self.fit_combo.currentIndex(),
            "dither_index": self.dither_combo.currentIndex(),
            "serpentine": self.serp_check.isChecked(),
            "device_ip": self.edit_ip.text().strip(),
        }
        save_config(cfg)

    def closeEvent(self, event):
        self.save_current_config()
        super().closeEvent(event)

    # ---------- 事件 ----------

    def on_choose(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        try:
            self.src_img = Image.open(path).convert("RGB")
            self.rotation = 0
            self._update_offset_ranges()
            self.on_preview()
            self.btn_export.setEnabled(True)
            self.btn_upload.setEnabled(True)
            self.statusBar().showMessage(f"已加载：{path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载失败：{e}")

    def on_reset(self):
        for s in (self.exp, self.con, self.cla, self.sha, self.hig):
            s.reset()
        self.rotation = 0
        self.fit_combo.setCurrentIndex(0)
        self._update_offset_ranges()
        self.on_preview()
        self.statusBar().showMessage("已重置所有调整")

    def on_factory_reset(self):
        for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

        self.apply_config({})
        self.rotation = 0
        self.lbl_conn_status.setText("就绪")
        self.lbl_conn_status.setStyleSheet("color: #888; font-weight: bold;")
        if self.src_img is not None:
            self._update_offset_ranges()
        self.on_preview()
        self.statusBar().showMessage("已恢复默认设置并清除记忆")

    def on_rotate(self):
        self.rotation = (self.rotation + 90) % 360
        self._update_offset_ranges()
        self.on_preview()
        self.statusBar().showMessage(f"已旋转到 {self.rotation}°")

    def on_preview(self):
        if self.src_img is None:
            return
        try:
            W, H = self.current_size()
            fit_mode = self.current_fit_mode()
            rotated = self.get_rotated_img()

            if fit_mode == "cover":
                fitted = resize_fit(rotated, W, H, "cover",
                                    self.x_slider.value(), self.y_slider.value())
            else:
                fitted = resize_fit(rotated, W, H, "contain")

            self.fitted_img = fitted
            self.lbl_input.setPixmap(pil_to_qpixmap(fitted).scaled(
                self.lbl_input.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

            adjusted = apply_all(
                fitted,
                exposure=self.exp.get(),
                saturation=0,
                contrast=self.con.get(),
                clarity=self.cla.get(),
                shadow=self.sha.get(),
                highlight=self.hig.get(),
            )
            self.adjusted_img = adjusted
            self.lbl_adjusted.setPixmap(pil_to_qpixmap(adjusted).scaled(
                self.lbl_adjusted.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

            mode = DITHER_MODES[self.dither_combo.currentIndex()][1]
            serpentine = self.serp_check.isChecked()
            block = dither(adjusted, mode, serpentine)
            self.last_block = block

            dithered_img = block_to_image(block)
            self.dithered_img = dithered_img
            self.lbl_dithered.setPixmap(pil_to_qpixmap(dithered_img).scaled(
                self.lbl_dithered.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

            nav = make_nav_image(
                rotated, fit_mode,
                self.x_slider.value() if fit_mode == "cover" else 0,
                self.y_slider.value() if fit_mode == "cover" else 0,
            )
            self.nav_label.setPixmap(pil_to_qpixmap(nav))

            if fit_mode == "cover":
                self.lbl_info.setText(
                    f"{W}×{H}  铺满  偏移({self.x_slider.value()},{self.y_slider.value()})"
                    f"  {mode}")
            else:
                self.lbl_info.setText(
                    f"{W}×{H}  完整   {mode}")
        except Exception as e:
            self.lbl_info.setText(f"预览失败：{e}")
            self.statusBar().showMessage(f"预览失败：{e}")

    def on_export(self):
        if self.last_block is None:
            QMessageBox.warning(self, "提示", "请先选择图片")
            return

        try:
            bin_data = self._generate_bin()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成数据失败：{e}")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "保存 image.bin（48000 字节）", "image.bw.bin", "Binary (*.bin)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(bin_data)
            self.statusBar().showMessage(
                f"已保存：{path}（{len(bin_data)} 字节）")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：{e}")

    def _set_upload_busy(self, busy):
        self.btn_upload.setEnabled(not busy)
        self.btn_export.setEnabled(not busy)
        self.btn_choose.setEnabled(not busy)

    def on_upload(self):
        if self.last_block is None:
            QMessageBox.warning(self, "提示", "请先选择图片")
            return

        ip = clean_ip(self.edit_ip.text())
        if not ip:
            QMessageBox.warning(self, "提示", "请输入设备 IP 地址")
            return

        try:
            import requests  # noqa: F401
        except ImportError:
            QMessageBox.warning(self, "缺少依赖", "pip install requests")
            return

        self._upload_ip = ip
        self._set_upload_busy(True)
        self.lbl_conn_status.setText(f"正在检查 {ip} …")
        self.lbl_conn_status.setStyleSheet("color: #888; font-weight: bold;")
        self.statusBar().showMessage(f"正在检查 {ip} 连接…")

        self.check_worker = CheckWorker(ip)
        self.check_worker.finished.connect(self.on_upload_check_done)
        self.check_worker.start()

    def on_upload_check_done(self, online, message):
        ip = self._upload_ip

        if not online:
            self.lbl_conn_status.setText(f"✗ {message}（上传已取消）")
            self.lbl_conn_status.setStyleSheet("color: #c00; font-weight: bold;")
            self.statusBar().showMessage(f"设备 {ip} 离线：{message}，上传已取消")
            self._set_upload_busy(False)
            QMessageBox.critical(
                self, "连接失败",
                f"设备 {ip} 无法连接：{message}\n\n上传已取消，请检查 IP 和设备状态。")
            return

        self.lbl_conn_status.setText(f"✓ {message}，正在准备上传…")
        self.lbl_conn_status.setStyleSheet("color: #0a7a0a; font-weight: bold;")
        self.statusBar().showMessage(f"设备 {ip} 在线")

        try:
            bin_data = self._generate_bin()
        except Exception as e:
            self.lbl_conn_status.setText(f"✓ {message}（生成数据失败）")
            self._set_upload_busy(False)
            QMessageBox.critical(self, "错误", f"生成数据失败：{e}")
            return

        url = f"http://{ip}/upload"
        self.lbl_conn_status.setText(f"↑ 上传中…")
        self.lbl_conn_status.setStyleSheet("color: #06c; font-weight: bold;")
        self.statusBar().showMessage(f"上传中（48000 字节）到 {ip}…")

        self.worker = UploadWorker(url, bin_data)
        self.worker.finished.connect(self.on_upload_ok)
        self.worker.failed.connect(self.on_upload_fail)
        self.worker.start()

    def on_upload_ok(self, msg):
        self._set_upload_busy(False)
        self.lbl_conn_status.setText(f"✓ 上传完成")
        self.lbl_conn_status.setStyleSheet("color: #0a7a0a; font-weight: bold;")
        self.statusBar().showMessage(f"上传完成：{msg}")

    def on_upload_fail(self, msg):
        self._set_upload_busy(False)
        self.lbl_conn_status.setText(f"✗ 上传失败")
        self.lbl_conn_status.setStyleSheet("color: #c00; font-weight: bold;")
        self.statusBar().showMessage("上传失败")
        QMessageBox.critical(self, "上传失败", msg)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()