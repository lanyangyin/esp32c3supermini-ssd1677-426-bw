# e6_studio.py — E6 一体化工具 (黑白 800x480)
import sys
import io
import math
import copy
import json
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import (
    Qt, QThread, Signal, QPointF, QRect, QSize, QTimer,
)
from PySide6.QtGui import (
    QPixmap, QImage, QColor, QPainter, QPen, QPainterPath, QFont,
    QFontMetrics, QIcon,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QLineEdit, QGroupBox,
    QComboBox, QSlider, QCheckBox, QScrollArea, QSpinBox, QTextEdit, QButtonGroup,
    QSizePolicy,
)

from e6_image_core.dithering import (
    DITHER_MODES, dither, block_to_image, pack, quantize
)
from e6_image_core.tone import apply_all

# ---------- 配置路径 ----------
_THIS_DIR = Path(__file__).parent
CORE_DIR = _THIS_DIR / "e6_image_core"
CONFIG_PATH = CORE_DIR / "e6_studio_config.json"

W, H = 800, 480
FRAME_BYTES = W * H // 8
TRANSPARENT = 255

FONT_FAMILIES = [("无衬线", "Sans Serif"), ("衬线", "Serif"), ("等宽", "Monospace")]
DIRECTIONS = [
    ("横排（左 → 右）", "horizontal"),
    ("竖排（列 左 → 右）", "vertical-lr"),
    ("竖排（列 右 → 左）", "vertical-rl"),
]
ALIGNS = [("左对齐", "left"), ("居中", "center"), ("右对齐", "right")]

FIT_MODES = [("铺满（裁剪）", "cover"), ("完整显示（白边）", "contain")]

MAX_HISTORY = 20


# ================= 配置 =================

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config():
    if CONFIG_PATH.exists():
        data = _read_json(CONFIG_PATH)
        if data:
            return data
    return {}


def save_config(data):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def clean_ip(text):
    ip = (text or "").strip()
    if ip.startswith("http://"):
        ip = ip[7:]
    elif ip.startswith("https://"):
        ip = ip[8:]
    return ip.rstrip("/")


# ================= 图像处理 =================

def _scaled_size(iw, ih, mode, scale):
    if iw <= 0 or ih <= 0:
        return W, H
    if mode == "cover":
        base = max(W / iw, H / ih)
        final = base * scale
        nw = max(W, int(round(iw * final)))
        nh = max(H, int(round(ih * final)))
    else:
        base = min(W / iw, H / ih)
        final = base * scale
        nw = max(1, min(W, int(round(iw * final))))
        nh = max(1, min(H, int(round(ih * final))))
    return nw, nh


def resize_fit(img, w, h, mode="cover", x_offset=None, y_offset=None, scale=1.0):
    img = img.convert("RGB")
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGB", (w, h), (255, 255, 255))
    nw, nh = _scaled_size(iw, ih, mode, scale)
    if mode == "cover":
        resized = img.resize((nw, nh), Image.LANCZOS)
        max_x = nw - w
        max_y = nh - h
        ox = max_x // 2 if x_offset is None else max(0, min(max_x, int(x_offset)))
        oy = max_y // 2 if y_offset is None else max(0, min(max_y, int(y_offset)))
        return resized.crop((ox, oy, ox + w, oy + h))
    else:
        resized = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h), (255, 255, 255))
        canvas.paste(resized, ((w - nw) // 2, (h - nh) // 2))
        return canvas


def cover_limits(img, w, h, scale=1.0):
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return 0, 0
    nw, nh = _scaled_size(iw, ih, "cover", scale)
    return max(0, nw - w), max(0, nh - h)


def pil_to_qpixmap(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return QPixmap.fromImage(QImage.fromData(buf.getvalue(), "PNG"))


def make_nav_image(rotated, fit_mode, x_offset, y_offset, zoom=1.0,
                   nav_max_w=260, nav_max_h=174):
    iw, ih = rotated.size
    if fit_mode == "cover":
        nw, nh = _scaled_size(iw, ih, "cover", zoom)
        full_w, full_h = nw, nh
    else:
        full_w, full_h = W, H
    nav_scale = min(nav_max_w / full_w, nav_max_h / full_h)
    nav_w = max(1, int(full_w * nav_scale))
    nav_h = max(1, int(full_h * nav_scale))
    nav = Image.new("RGB", (nav_w, nav_h), (210, 210, 210))

    if fit_mode == "cover":
        nw, nh = _scaled_size(iw, ih, "cover", zoom)
        small = rotated.resize(
            (max(1, int(nw * nav_scale)), max(1, int(nh * nav_scale))),
            Image.LANCZOS)
        nav.paste(small, (0, 0))
        vx = int(x_offset * nav_scale)
        vy = int(y_offset * nav_scale)
        vw = max(1, int(W * nav_scale))
        vh = max(1, int(H * nav_scale))
        overlay = Image.new("RGBA", nav.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([0, 0, nav_w, vy], fill=(0, 0, 0, 130))
        od.rectangle([0, vy + vh, nav_w, nav_h], fill=(0, 0, 0, 130))
        od.rectangle([0, vy, vx, vy + vh], fill=(0, 0, 0, 130))
        od.rectangle([vx + vw, vy, nav_w, vy + vh], fill=(0, 0, 0, 130))
        nav = Image.alpha_composite(nav.convert("RGBA"), overlay).convert("RGB")
        d = ImageDraw.Draw(nav)
        d.rectangle([vx, vy, vx + vw - 1, vy + vh - 1], outline=(255, 50, 50), width=2)
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


def _render_indices_with_rgb(indices):
    buf = bytearray(W * H * 4)
    for i in range(W * H):
        idx = indices[i]
        if idx == TRANSPARENT:
            continue
        c = 0 if idx == 0 else 255
        off = i * 4
        buf[off] = c
        buf[off + 1] = c
        buf[off + 2] = c
        buf[off + 3] = 255
    return QImage(bytes(buf), W, H, QImage.Format_ARGB32).copy()


# ================= 文字渲染 =================

def measure_text_bbox(family, size, bold, italic, direction, text,
                      line_height, letter_spacing=0):
    font = QFont(family)
    font.setPixelSize(size)
    font.setBold(bold)
    font.setItalic(italic)
    fm = QFontMetrics(font)
    lines = text.split("\n") if text else [""]
    if direction == "horizontal":
        max_w = 0.0
        for line in lines:
            chars = list(line)
            w = 0.0
            for j, ch in enumerate(chars):
                w += fm.horizontalAdvance(ch)
                if j < len(chars) - 1:
                    w += letter_spacing
            if w > max_w:
                max_w = w
        h = max(1, len(lines) * size * line_height)
        return max(1, max_w), h
    else:
        col_w = max(1, size * line_height + letter_spacing)
        max_chars = max((len(l) for l in lines), default=1)
        w = max(1, len(lines) * col_w)
        h = max(1, max_chars * col_w)
        return w, h


def render_text_mask(text, family, size, bold, italic, underline,
                     direction, align, line_height, rotate_deg, anchor,
                     letter_spacing=0):
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(0)
    if not text:
        return img

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    font = QFont(family)
    font.setPixelSize(size)
    font.setBold(bold)
    font.setItalic(italic)
    p.setFont(font)
    white = QColor("#FFFFFF")
    p.setPen(white)
    p.setBrush(white)

    p.translate(anchor[0], anchor[1])
    if rotate_deg != 0:
        p.rotate(rotate_deg)

    fm = QFontMetrics(font)
    line_h = size * line_height
    lines = text.split("\n")
    ul_thick = max(1, size // 14)

    if direction == "horizontal":
        for i, line in enumerate(lines):
            chars = list(line)
            total_w = 0.0
            for j, ch in enumerate(chars):
                total_w += fm.horizontalAdvance(ch)
                if j < len(chars) - 1:
                    total_w += letter_spacing

            if align == "left":
                dx = 0.0
            elif align == "center":
                dx = -total_w / 2
            else:
                dx = -total_w

            dy = i * line_h + fm.ascent()

            if line:
                cx = dx
                for ch in chars:
                    p.drawText(QPointF(cx, dy), ch)
                    cx += fm.horizontalAdvance(ch) + letter_spacing

            if underline and line:
                ul_y = i * line_h + size * 0.9
                p.fillRect(int(dx), int(ul_y), int(total_w), ul_thick, white)
    else:
        col_w = max(1, line_h + letter_spacing)
        total_w = len(lines) * col_w
        block_x = 0.0 if align == "left" else (-total_w / 2 if align == "center" else -total_w)
        for ci, line in enumerate(lines):
            if direction == "vertical-lr":
                col_cx = block_x + ci * col_w + col_w / 2
            else:
                col_cx = block_x + total_w - ci * col_w - col_w / 2
            for ji, ch in enumerate(line):
                if ch in " \t":
                    continue
                cw = fm.horizontalAdvance(ch)
                cy = ji * col_w + col_w / 2
                baseline = cy + (fm.ascent() - fm.descent()) / 2
                p.drawText(QPointF(col_cx - cw / 2, baseline), ch)
            if underline and line:
                clean = [c for c in line if c not in " \t"]
                if clean:
                    max_cw = max(fm.horizontalAdvance(c) for c in clean)
                    ul_x = col_cx + max_cw / 2 + 1
                    ul_y1 = col_w * 0.1
                    ul_y2 = len(clean) * col_w - col_w * 0.1
                    p.fillRect(int(ul_x), int(ul_y1), ul_thick, int(ul_y2 - ul_y1), white)
    p.end()
    return img


# ================= 线程 =================

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
                              timeout=120)
            self.finished.emit(f"{r.status_code} {r.text}")
        except Exception as e:
            self.failed.emit(str(e))


# ================= 滑条行 =================

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
        self.value.setFixedWidth(48)
        layout.addWidget(self.value, 0)
        self.slider.valueChanged.connect(lambda v: self.value.setText(self._fmt(v)))

    def _fmt(self, v):
        return f"{v / self.scale:.2f}" if self.scale != 1 else str(v)

    def get(self):
        return self.slider.value() / self.scale

    def reset(self):
        self.slider.setValue(self.default)


# ================= 文字预览 Widget =================

class TextPreviewWidget(QWidget):
    def __init__(self, studio, parent=None):
        super().__init__(parent)
        self.studio = studio
        self.setFixedSize(260, 130)
        self.setStyleSheet("background:#000;border:1px solid #34332f;border-radius:4px;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))

        params = self.studio._get_text_params()
        text = params["text"]
        if not text.strip():
            p.setPen(QColor("#555555"))
            p.setFont(QFont("Sans Serif", 10))
            p.drawText(self.rect(), Qt.AlignCenter, "（无文字）")
            return

        bw, bh = measure_text_bbox(
            params["family"], params["size"], params["bold"], params["italic"],
            params["direction"], text, params["line_height"],
            params["letter_spacing"],
        )
        rot = abs(params["rotate"]) * math.pi / 180
        cos_v = abs(math.cos(rot))
        sin_v = abs(math.sin(rot))
        bound_w = bw * cos_v + bh * sin_v
        bound_h = bw * sin_v + bh * cos_v

        pad = 8
        scale = min(
            (self.width() - 2 * pad) / max(bound_w, 1),
            (self.height() - 2 * pad) / max(bound_h, 1),
            1.0,
        )

        if params["align"] == "left":
            c_rel_x = bw / 2
        elif params["align"] == "right":
            c_rel_x = -bw / 2
        else:
            c_rel_x = 0
        c_rel_y = bh / 2

        anchor = (-c_rel_x, -c_rel_y)
        mask = render_text_mask(
            text, params["family"], params["size"], params["bold"], params["italic"],
            params["underline"], params["direction"], params["align"],
            params["line_height"], params["rotate"], anchor=anchor,
            letter_spacing=params["letter_spacing"],
        )

        p.save()
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(scale, scale)
        p.translate(-c_rel_x, -c_rel_y)
        p.drawImage(QPointF(0, 0), mask)
        p.restore()


# ================= 画布 =================

class CanvasWidget(QWidget):
    def __init__(self, studio, parent=None):
        super().__init__(parent)
        self.studio = studio
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._composite = None
        self._overlay = None
        self._drawing = False
        self._last_px = None

    def invalidate(self):
        self._composite = None
        self.update()

    def set_overlay(self, img):
        self._overlay = img
        self.update()

    def clear_overlay(self):
        if self._overlay is not None:
            self._overlay = None
            self.update()

    def _fit_rect(self):
        aw, ah = self.width(), self.height()
        if aw <= 0 or ah <= 0:
            return QRect(0, 0, 1, 1)
        scale = min(aw / W, ah / H)
        dw = max(1, int(W * scale))
        dh = max(1, int(H * scale))
        return QRect((aw - dw) // 2, (ah - dh) // 2, dw, dh)

    def _widget_to_pixel(self, pos):
        r = self._fit_rect()
        if r.width() <= 0 or r.height() <= 0:
            return None
        x = (pos.x() - r.x()) * W // r.width()
        y = (pos.y() - r.y()) * H // r.height()
        if 0 <= x < W and 0 <= y < H:
            return (x, y)
        return None

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#151512"))

        if self.studio.source_choice is None:
            p.setPen(QColor("#888"))
            f = QFont("Sans Serif")
            f.setPixelSize(14)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "请先选择图片（生成预览后即可编辑）")
            return

        if self._composite is None:
            self._composite = self.studio.composite_final()
        r = self._fit_rect()
        p.drawImage(r, self._composite)
        if self._overlay is not None:
            p.setOpacity(0.75)
            p.drawImage(r, self._overlay)
            p.setOpacity(1.0)
        p.setPen(QPen(QColor("#4a4943"), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))

        if self.studio.edit_locked:
            p.setPen(QColor("#f28b82"))
            f = QFont("Sans Serif")
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(r.adjusted(6, 0, -6, -6), Qt.AlignLeft | Qt.AlignBottom,
                       "🔒 编辑已锁定")

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.studio.source_choice is None:
            return
        if self.studio.edit_locked:
            return
        pos = self._widget_to_pixel(event.position().toPoint())
        if pos is None:
            return
        self.studio.on_canvas_press(pos[0], pos[1])
        self._drawing = True
        self._last_px = pos

    def mouseMoveEvent(self, event):
        if self.studio.source_choice is None:
            return
        if self.studio.edit_locked:
            return
        pos = self._widget_to_pixel(event.position().toPoint())
        if pos is None:
            return
        if self._drawing and self._last_px is not None:
            self.studio.on_canvas_move(self._last_px[0], self._last_px[1], pos[0], pos[1])
            self._last_px = pos
        elif self.studio.text_mode_active:
            self.studio.on_canvas_hover(pos[0], pos[1])

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._drawing:
            self.studio.on_canvas_release()
            self._drawing = False
            self._last_px = None

    def leaveEvent(self, event):
        self.studio.on_canvas_leave()


# ================= 主窗口 =================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E6 Studio · 黑白转换 + 编辑 + 上传")
        self.resize(1600, 1000)

        self.config = load_config()

        # 图像处理
        self.src_img = None
        self.rotation = 0
        self.fitted_img = None
        self.adjusted_img = None
        self.dithered_img = None
        self.last_block = None

        # 数据源：None / "dithered" / "bin"
        self.source_choice = None
        self._bin_source_path = ""

        # 编辑层
        self.bin_layer_indices = bytearray([TRANSPARENT]) * (W * H)
        self.bin_image = QImage(W, H, QImage.Format_ARGB32)
        self.bin_image.fill(0)
        self.edit_layer = bytearray([TRANSPARENT]) * (W * H)
        self.edit_image = QImage(W, H, QImage.Format_ARGB32)
        self.edit_image.fill(0)

        self.bg_color_idx = 1
        self.current_color = {"index": 0}
        self.current_tool = "pencil"
        self.brush_size = 4
        self.text_mode_active = False

        self.edit_locked = False

        self.undo_stack = []
        self.redo_stack = []

        self.worker = None
        self.check_worker = None
        self._upload_ip = ""

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.on_preview)

        self._build_ui()
        self._apply_config()
        self._push_undo()
        self._update_preview_labels()
        self._update_source_label()
        self._update_source_button_state()

    # ---------- UI ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(420)
        left_w = QWidget()
        ll = QVBoxLayout(left_w)
        left_scroll.setWidget(left_w)
        root.addWidget(left_scroll)

        # 图像
        fg = QGroupBox("图像")
        fl = QVBoxLayout(fg)
        self.btn_choose = QPushButton("选择图片…")
        self.btn_choose.clicked.connect(self.on_choose)
        fl.addWidget(self.btn_choose)
        self.lbl_img = QLabel("未加载图片")
        self.lbl_img.setStyleSheet("color:#888;font-size:11px;")
        self.lbl_img.setWordWrap(True)
        fl.addWidget(self.lbl_img)
        ll.addWidget(fg)

        # 数据源
        sg = QGroupBox("数据源")
        sl = QVBoxLayout(sg)
        self.btn_choose_source = QPushButton("使用转换结果")
        self.btn_choose_source.clicked.connect(self.on_choose_source)
        self.btn_choose_source.setToolTip("请先选择图片，等预览计算完成后即可使用")
        sl.addWidget(self.btn_choose_source)
        self.btn_load_bin_source = QPushButton("上传 BIN 作为数据源…")
        self.btn_load_bin_source.clicked.connect(self.on_load_bin_source)
        sl.addWidget(self.btn_load_bin_source)
        self.lbl_source = QLabel("数据源：未选择")
        self.lbl_source.setStyleSheet("color:#c77;font-size:11px;font-weight:bold;")
        self.lbl_source.setWordWrap(True)
        sl.addWidget(self.lbl_source)
        ll.addWidget(sg)

        # 色调
        tg = QGroupBox("色调调整")
        tl = QVBoxLayout(tg)
        self.exp = SliderRow("曝光", -100, 100, 0)
        self.con = SliderRow("对比度", -100, 100, 0)
        self.cla = SliderRow("清晰度", 0, 200, 0)
        self.sha = SliderRow("阴影", 0, 100, 0)
        self.hig = SliderRow("高光", 0, 100, 0)
        for s in (self.exp, self.con, self.cla, self.sha, self.hig):
            tl.addWidget(s)
        tone_btn_row = QHBoxLayout()
        self.btn_reset_tone = QPushButton("重置调整")
        self.btn_reset_tone.clicked.connect(self.on_reset_tone)
        tone_btn_row.addWidget(self.btn_reset_tone)
        self.btn_rotate = QPushButton("旋转 90°")
        self.btn_rotate.clicked.connect(self.on_rotate)
        tone_btn_row.addWidget(self.btn_rotate)
        tl.addLayout(tone_btn_row)
        ll.addWidget(tg)

        # 抖动
        dg = QGroupBox("黑白抖动")
        dl = QVBoxLayout(dg)
        d_row = QHBoxLayout()
        d_row.addWidget(QLabel("算法:"))
        self.dither_combo = QComboBox()
        for name, _ in DITHER_MODES:
            self.dither_combo.addItem(name)
        d_row.addWidget(self.dither_combo)
        dl.addLayout(d_row)
        self.serp_check = QCheckBox("蛇形扫描")
        self.serp_check.setChecked(True)
        dl.addWidget(self.serp_check)
        ll.addWidget(dg)

        # 画布
        cg = QGroupBox("画布")
        cl = QVBoxLayout(cg)
        fit_row = QHBoxLayout()
        fit_row.addWidget(QLabel("填充:"))
        self.fit_combo = QComboBox()
        for name, _ in FIT_MODES:
            self.fit_combo.addItem(name)
        fit_row.addWidget(self.fit_combo, 1)
        self.btn_center = QPushButton("居中")
        self.btn_center.clicked.connect(self.on_center)
        fit_row.addWidget(self.btn_center)
        cl.addLayout(fit_row)

        z_row = QHBoxLayout()
        z_row.addWidget(QLabel("缩放:"))
        self.sld_zoom = QSlider(Qt.Horizontal)
        self.sld_zoom.setRange(100, 300)
        self.sld_zoom.setSingleStep(10)
        self.sld_zoom.setValue(100)
        self.sld_zoom.valueChanged.connect(self.on_zoom_changed)
        z_row.addWidget(self.sld_zoom, 1)
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setFixedWidth(50)
        z_row.addWidget(self.lbl_zoom)
        cl.addLayout(z_row)

        ox_row = QHBoxLayout()
        ox_row.addWidget(QLabel("X 偏移:"))
        self.sld_offset_x = QSlider(Qt.Horizontal)
        self.sld_offset_x.setRange(0, 0)
        self.sld_offset_x.valueChanged.connect(self.on_offset_changed)
        ox_row.addWidget(self.sld_offset_x, 1)
        self.lbl_off_x = QLabel("0")
        self.lbl_off_x.setFixedWidth(44)
        ox_row.addWidget(self.lbl_off_x)
        cl.addLayout(ox_row)

        oy_row = QHBoxLayout()
        oy_row.addWidget(QLabel("Y 偏移:"))
        self.sld_offset_y = QSlider(Qt.Horizontal)
        self.sld_offset_y.setRange(0, 0)
        self.sld_offset_y.valueChanged.connect(self.on_offset_changed)
        oy_row.addWidget(self.sld_offset_y, 1)
        self.lbl_off_y = QLabel("0")
        self.lbl_off_y.setFixedWidth(44)
        oy_row.addWidget(self.lbl_off_y)
        cl.addLayout(oy_row)

        nav_row = QHBoxLayout()
        self.lbl_nav = QLabel()
        self.lbl_nav.setFixedSize(260, 174)
        self.lbl_nav.setStyleSheet("border:1px solid #bbb;background:#eee;")
        self.lbl_nav.setAlignment(Qt.AlignCenter)
        nav_row.addWidget(self.lbl_nav)
        nav_row.addStretch()
        cl.addLayout(nav_row)
        ll.addWidget(cg)

        # 编辑工具
        eg = QGroupBox("编辑工具（作用于最终预览板）")
        el = QVBoxLayout(eg)

        self.chk_edit_lock = QCheckBox("🔒 编辑锁（锁定后画布不可编辑）")
        self.chk_edit_lock.setStyleSheet("font-weight:bold;")
        self.chk_edit_lock.stateChanged.connect(self._on_edit_lock_changed)
        el.addWidget(self.chk_edit_lock)

        tool_row = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_btns = {}
        for tool_id, lbl in [("pencil", "✏️ 画笔"), ("eraser", "🧽 橡皮"),
                             ("eyedropper", "💧 吸管")]:
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            if tool_id == "pencil":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked=False, t=tool_id: self._on_tool_clicked(t))
            self.tool_group.addButton(btn)
            self.tool_btns[tool_id] = btn
            tool_row.addWidget(btn)
        el.addLayout(tool_row)

        brush_row = QHBoxLayout()
        brush_row.addWidget(QLabel("笔刷:"))
        self.sld_brush = QSlider(Qt.Horizontal)
        self.sld_brush.setRange(2, 32)
        self.sld_brush.setSingleStep(2)
        self.sld_brush.setValue(4)
        self.sld_brush.valueChanged.connect(self._on_brush_changed)
        brush_row.addWidget(self.sld_brush, 1)
        self.lbl_brush = QLabel("4")
        self.lbl_brush.setFixedWidth(30)
        brush_row.addWidget(self.lbl_brush)
        el.addLayout(brush_row)

        el.addWidget(QLabel("基础色:"))
        basic_row = QHBoxLayout()
        self.basic_btns = []
        for i in range(2):  # 只有 0(黑) 和 1(白)
            hex_color = "#000000" if i == 0 else "#FFFFFF"
            btn = QPushButton()
            btn.setFixedSize(44, 44)
            btn.setStyleSheet(
                f"background:{hex_color};border:2px solid "
                f"{'#75c7bd' if i == 0 else '#4a4943'};border-radius:4px;")
            btn.clicked.connect(lambda checked=False, idx=i: self._on_basic_clicked(idx))
            basic_row.addWidget(btn)
            self.basic_btns.append(btn)
        el.addLayout(basic_row)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("背景:"))
        self.bg_btns = []
        for i in range(2):
            hex_color = "#000000" if i == 0 else "#FFFFFF"
            btn = QPushButton()
            btn.setFixedSize(32, 32)
            btn.setStyleSheet(
                f"background:{hex_color};border:2px solid "
                f"{'#75c7bd' if i == 1 else '#4a4943'};border-radius:4px;")
            btn.clicked.connect(lambda checked=False, idx=i: self._on_bg_clicked(idx))
            bg_row.addWidget(btn)
            self.bg_btns.append(btn)
        el.addLayout(bg_row)

        self.chk_lock = QCheckBox("锁定源层（橡皮只擦自己画的）")
        self.chk_lock.setToolTip("未锁定时：橡皮会把源内容和笔画一起擦掉，露出背景色")
        el.addWidget(self.chk_lock)
        ll.addWidget(eg)

        # 文字
        txt_g = QGroupBox("文字")
        tgl = QVBoxLayout(txt_g)
        self.text_edit = QTextEdit()
        self.text_edit.setFixedHeight(60)
        self.text_edit.setPlaceholderText("输入要放置的文字，支持换行")
        self.text_edit.textChanged.connect(self._on_text_param_changed)
        tgl.addWidget(self.text_edit)

        f_row = QHBoxLayout()
        f_row.addWidget(QLabel("字体:"))
        self.cmb_font = QComboBox()
        for lbl, fam in FONT_FAMILIES:
            self.cmb_font.addItem(lbl, fam)
        self.cmb_font.currentIndexChanged.connect(self._on_text_param_changed)
        f_row.addWidget(self.cmb_font, 1)
        f_row.addWidget(QLabel("字号:"))
        self.spn_size = QSpinBox()
        self.spn_size.setRange(6, 240)
        self.spn_size.setValue(32)
        self.spn_size.valueChanged.connect(self._on_text_param_changed)
        f_row.addWidget(self.spn_size)
        tgl.addLayout(f_row)

        d_row = QHBoxLayout()
        d_row.addWidget(QLabel("方向:"))
        self.cmb_dir = QComboBox()
        for lbl, d in DIRECTIONS:
            self.cmb_dir.addItem(lbl, d)
        self.cmb_dir.currentIndexChanged.connect(self._on_text_param_changed)
        d_row.addWidget(self.cmb_dir, 1)
        tgl.addLayout(d_row)

        a_row = QHBoxLayout()
        a_row.addWidget(QLabel("对齐:"))
        self.cmb_align = QComboBox()
        for lbl, a in ALIGNS:
            self.cmb_align.addItem(lbl, a)
        self.cmb_align.currentIndexChanged.connect(self._on_text_param_changed)
        a_row.addWidget(self.cmb_align, 1)
        a_row.addWidget(QLabel("样式:"))
        self.btn_bold = QPushButton("B")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setFixedSize(32, 30)
        self.btn_bold.setStyleSheet("font-weight:bold;")
        self.btn_bold.clicked.connect(self._on_text_param_changed)
        a_row.addWidget(self.btn_bold)
        self.btn_italic = QPushButton("I")
        self.btn_italic.setCheckable(True)
        self.btn_italic.setFixedSize(32, 30)
        self.btn_italic.setStyleSheet("font-style:italic;")
        self.btn_italic.clicked.connect(self._on_text_param_changed)
        a_row.addWidget(self.btn_italic)
        self.btn_underline = QPushButton("U")
        self.btn_underline.setCheckable(True)
        self.btn_underline.setFixedSize(32, 30)
        self.btn_underline.clicked.connect(self._on_text_param_changed)
        a_row.addWidget(self.btn_underline)
        tgl.addLayout(a_row)

        lh_row = QHBoxLayout()
        lh_row.addWidget(QLabel("行高:"))
        self.sld_lh = QSlider(Qt.Horizontal)
        self.sld_lh.setRange(8, 25)
        self.sld_lh.setValue(12)
        self.sld_lh.valueChanged.connect(self._on_text_param_changed)
        lh_row.addWidget(self.sld_lh, 1)
        self.lbl_lh = QLabel("1.2")
        self.lbl_lh.setFixedWidth(36)
        lh_row.addWidget(self.lbl_lh)
        tgl.addLayout(lh_row)

        ls_row = QHBoxLayout()
        ls_row.addWidget(QLabel("字间距:"))
        self.sld_ls = QSlider(Qt.Horizontal)
        self.sld_ls.setRange(-20, 40)
        self.sld_ls.setValue(0)
        self.sld_ls.valueChanged.connect(self._on_text_param_changed)
        ls_row.addWidget(self.sld_ls, 1)
        self.lbl_ls = QLabel("0")
        self.lbl_ls.setFixedWidth(36)
        ls_row.addWidget(self.lbl_ls)
        tgl.addLayout(ls_row)

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("旋转:"))
        self.sld_rot = QSlider(Qt.Horizontal)
        self.sld_rot.setRange(-180, 180)
        self.sld_rot.setValue(0)
        self.sld_rot.valueChanged.connect(self._on_text_param_changed)
        rot_row.addWidget(self.sld_rot, 1)
        self.lbl_rot = QLabel("0°")
        self.lbl_rot.setFixedWidth(40)
        rot_row.addWidget(self.lbl_rot)
        tgl.addLayout(rot_row)

        tgl.addWidget(QLabel("预览:"))
        self.text_preview = TextPreviewWidget(self)
        tgl.addWidget(self.text_preview)

        self.btn_place_text = QPushButton("放置文字（点击后在画布点选位置）")
        self.btn_place_text.clicked.connect(self.on_place_text)
        tgl.addWidget(self.btn_place_text)
        ll.addWidget(txt_g)

        # 历史 / 清空
        hg = QGroupBox("历史 / 清空")
        hl = QVBoxLayout(hg)
        u_row = QHBoxLayout()
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(self.on_undo)
        u_row.addWidget(self.btn_undo)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.clicked.connect(self.on_redo)
        u_row.addWidget(self.btn_redo)
        hl.addLayout(u_row)

        c_row = QHBoxLayout()
        self.btn_clear_draw = QPushButton("清空绘制层")
        self.btn_clear_draw.clicked.connect(self.on_clear_draw)
        c_row.addWidget(self.btn_clear_draw)
        self.btn_clear_all = QPushButton("清空全部")
        self.btn_clear_all.clicked.connect(self.on_clear_all)
        c_row.addWidget(self.btn_clear_all)
        hl.addLayout(c_row)
        ll.addWidget(hg)

        # 上传
        ug = QGroupBox("上传")
        ul = QVBoxLayout(ug)
        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("设备 IP:"))
        self.edit_ip = QLineEdit("192.168.4.1")
        ip_row.addWidget(self.edit_ip, 1)
        ul.addLayout(ip_row)

        self.lbl_conn = QLabel("就绪")
        self.lbl_conn.setStyleSheet("color:#888;font-weight:bold;")
        self.lbl_conn.setWordWrap(True)
        ul.addWidget(self.lbl_conn)

        b_row = QHBoxLayout()
        self.btn_export = QPushButton("导出 BIN")
        self.btn_export.clicked.connect(self.on_export)
        b_row.addWidget(self.btn_export)
        self.btn_upload = QPushButton("检查连接并上传")
        self.btn_upload.setStyleSheet(
            "background:#f2f2ed;color:#1a1a1a;border:1px solid #f2f2ed;"
            "padding:9px 12px;border-radius:4px;font-weight:700;")
        self.btn_upload.clicked.connect(self.on_upload)
        b_row.addWidget(self.btn_upload)
        ul.addLayout(b_row)
        ll.addWidget(ug)

        ll.addStretch()

        # 右侧
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(8, 8, 8, 8)
        root.addWidget(right, 1)

        top_w = QWidget()
        top_grid = QGridLayout(top_w)
        top_grid.setSpacing(6)
        self.thumb_input = self._mk_thumb()
        self.thumb_adjusted = self._mk_thumb()
        self.thumb_dithered = self._mk_thumb()

        for i, (title, lbl) in enumerate([
            ("Input（旋转+填充）", self.thumb_input),
            ("Adjusted（色调调整）", self.thumb_adjusted),
            ("Dithered（黑白抖动）", self.thumb_dithered),
        ]):
            tl_ = QLabel(title)
            tl_.setStyleSheet("font-weight:bold;font-size:11px;padding:2px;")
            tl_.setAlignment(Qt.AlignCenter)
            top_grid.addWidget(tl_, 0, i)
            top_grid.addWidget(lbl, 1, i)
        right_l.addWidget(top_w)

        final_bar = QHBoxLayout()
        ft = QLabel("最终预览板（可编辑 · 上传就是这幅图）")
        ft.setStyleSheet("font-weight:bold;font-size:13px;padding:4px;")
        final_bar.addWidget(ft)
        final_bar.addStretch()
        self.lbl_current_source = QLabel("")
        self.lbl_current_source.setStyleSheet("color:#0a7a0a;font-weight:bold;font-size:11px;padding:4px;")
        final_bar.addWidget(self.lbl_current_source)
        right_l.addLayout(final_bar)

        self.canvas = CanvasWidget(self)
        right_l.addWidget(self.canvas, 1)

        self.statusBar().showMessage("就绪：请先选择图片，生成预览后即可编辑")

        # 事件
        self.exp.slider.valueChanged.connect(self.schedule_refresh)
        self.con.slider.valueChanged.connect(self.schedule_refresh)
        self.cla.slider.valueChanged.connect(self.schedule_refresh)
        self.sha.slider.valueChanged.connect(self.schedule_refresh)
        self.hig.slider.valueChanged.connect(self.schedule_refresh)
        self.dither_combo.currentIndexChanged.connect(self.schedule_refresh)
        self.serp_check.stateChanged.connect(self.schedule_refresh)
        self.fit_combo.currentIndexChanged.connect(self.on_fit_changed)

        self._update_undo_buttons()

    def _mk_thumb(self):
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFixedSize(240, 160)
        lbl.setStyleSheet("border:1px solid #bbb;background:#fafafa;")
        return lbl

    # ---------- 预览就绪判定 ----------

    def _previews_ready(self):
        return (self.src_img is not None and
                self.fitted_img is not None and
                self.adjusted_img is not None and
                self.dithered_img is not None)

    def _update_source_button_state(self):
        ready = self._previews_ready()
        self.btn_choose_source.setEnabled(ready)
        if ready:
            self.btn_choose_source.setToolTip("点击直接使用当前的二值化结果")
        else:
            self.btn_choose_source.setToolTip("请先选择图片")

    # ---------- 编辑锁 ----------

    def _on_edit_lock_changed(self, state):
        self.edit_locked = bool(state)
        if self.edit_locked and self.text_mode_active:
            self.text_mode_active = False
            self.canvas.clear_overlay()
        self.canvas.invalidate()
        self.statusBar().showMessage(
            "编辑已锁定，画布不可操作" if self.edit_locked else "编辑已解锁")

    # ---------- 图像加载 ----------

    def schedule_refresh(self):
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
        self._refresh_timer.start(60)

    def on_choose(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        try:
            self.src_img = Image.open(path).convert("RGB")
            self.rotation = 0
            self.lbl_img.setText(f"已加载：{Path(path).name}")
            self._update_offset_ranges(reset_to_center=True)
            self._compute_previews()
            self._update_preview_labels()
            self._update_nav()
            if self.source_choice in ("dithered", "bin"):
                self._sync_bin_from_block()
                self._rebuild_bin_image()
                self.canvas.invalidate()
            self._update_source_button_state()
            ready = self._previews_ready()
            tip = "预览已就绪，可点击「使用转换结果」" if ready else "预览仍在计算…"
            self.statusBar().showMessage(f"图片已加载：{path} · {tip}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"加载失败：{e}")

    def _compute_previews(self):
        if self.src_img is None:
            return
        try:
            fit_mode = FIT_MODES[self.fit_combo.currentIndex()][1]
            zoom = self.sld_zoom.value() / 100.0
            rotated = self._get_rotated()

            if fit_mode == "cover":
                fitted = resize_fit(rotated, W, H, "cover",
                                    self.sld_offset_x.value(),
                                    self.sld_offset_y.value(),
                                    zoom)
            else:
                fitted = resize_fit(rotated, W, H, "contain")
            self.fitted_img = fitted

            adjusted = apply_all(
                fitted,
                exposure=self.exp.get(),
                saturation=0,  # 强制去色
                contrast=self.con.get(),
                clarity=self.cla.get(),
                shadow=self.sha.get(),
                highlight=self.hig.get(),
            )
            self.adjusted_img = adjusted

            mode = DITHER_MODES[self.dither_combo.currentIndex()][1]
            block = dither(adjusted, mode, self.serp_check.isChecked())
            self.last_block = block

            self.dithered_img = block_to_image(block)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"预览计算失败：{e}")

    def _sync_bin_from_block(self):
        if self.last_block is None:
            return
        new_bin = bytearray(W * H)
        for y in range(H):
            row = self.last_block[y]
            base = y * W
            for x in range(W):
                # block 是 0(黑) 1(白)，透明层是 255
                # 这里因为选择了黑白源，不需要透明，全部铺满
                new_bin[base + x] = row[x]
        self.bin_layer_indices = new_bin

    # ---------- 选择数据源 ----------

    def on_choose_source(self):
        if not self._previews_ready():
            QMessageBox.warning(self, "提示",
                                "请先加载图片，等预览就绪后再使用")
            return
        self.source_choice = "dithered"
        self._bin_source_path = ""
        self._update_source_label()
        self._sync_bin_from_block()
        self._rebuild_bin_image()
        self.canvas.invalidate()
        self.statusBar().showMessage("已使用转换结果作为数据源")

    # ---------- 上传 BIN 作为源 ----------

    def on_load_bin_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "上传 BIN 作为数据源", "", "Binary (*.bin)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            if len(data) != FRAME_BYTES:
                QMessageBox.critical(
                    self, "错误",
                    f"文件大小错误：{len(data)} 字节，应为 {FRAME_BYTES}")
                return
            indices = self._unpack_bin(data)
            self.bin_layer_indices = indices
            self.source_choice = "bin"
            self._bin_source_path = path
            self._update_source_label()
            self._rebuild_bin_image()

            self.edit_layer = bytearray([TRANSPARENT]) * (W * H)
            self.edit_image = QImage(W, H, QImage.Format_ARGB32)
            self.edit_image.fill(0)
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._push_undo()

            self._update_preview_labels()
            self.canvas.invalidate()
            self._update_source_button_state()
            self.statusBar().showMessage(f"已加载 BIN 作为数据源：{path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"加载失败：{e}")

    def _unpack_bin(self, data):
        out = bytearray([TRANSPARENT]) * (W * H)
        row_bytes = W // 8
        for y in range(H):
            for x in range(W):
                byte_idx = y * row_bytes + (x >> 3)
                bit = (data[byte_idx] >> (7 - (x & 7))) & 1
                out[y * W + x] = 0 if bit else 1
        return out

    def _update_source_label(self):
        if self.source_choice == "dithered":
            text = "数据源：转换结果"
            color = "#0a7a0a"
        elif self.source_choice == "bin":
            name = Path(self._bin_source_path).name if self._bin_source_path else "BIN"
            text = f"数据源：上传的 BIN（{name}）"
            color = "#0a7a0a"
        else:
            text = "数据源：未选择"
            color = "#c77"

        self.lbl_source.setText(text)
        self.lbl_source.setStyleSheet(f"color:{color};font-size:11px;font-weight:bold;")
        self.lbl_current_source.setText(text if self.source_choice else "")

    # ---------- 图像处理 ----------

    def on_preview(self):
        if self.source_choice == "bin":
            self.canvas.invalidate()
            return
        if self.src_img is None:
            return
        try:
            self._compute_previews()
            if self.source_choice == "dithered":
                self._sync_bin_from_block()
                self._rebuild_bin_image()
            self._update_preview_labels()
            self._update_nav()
            self.canvas.invalidate()
            self._update_source_button_state()
            self.statusBar().showMessage("预览已更新")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"预览失败：{e}")

    def _rebuild_bin_image(self):
        self.bin_image = _render_indices_with_rgb(self.bin_layer_indices)

    def _update_preview_labels(self):
        if self.src_img is None:
            for lbl in (self.thumb_input, self.thumb_adjusted, self.thumb_dithered):
                lbl.clear()
                lbl.setText("（空）")
            self.lbl_nav.clear()
            return

        for lbl, img in [
            (self.thumb_input, self.fitted_img),
            (self.thumb_adjusted, self.adjusted_img),
            (self.thumb_dithered, self.dithered_img),
        ]:
            if img is not None:
                pix = pil_to_qpixmap(img).scaled(
                    lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                lbl.setPixmap(pix)
            else:
                lbl.clear()
                lbl.setText("（计算中…）")

    def _get_rotated(self):
        if self.src_img is None:
            return None
        img = self.src_img
        if self.rotation:
            img = img.rotate(-self.rotation, expand=True)
        return img

    def _update_offset_ranges(self, reset_to_center=False):
        rotated = self._get_rotated()
        if rotated is None:
            return
        fit_mode = FIT_MODES[self.fit_combo.currentIndex()][1]
        zoom = self.sld_zoom.value() / 100.0

        if fit_mode == "cover":
            mx, my = cover_limits(rotated, W, H, zoom)
            self.sld_offset_x.setRange(0, mx)
            self.sld_offset_y.setRange(0, my)
            if reset_to_center:
                self.sld_offset_x.setValue(mx // 2)
                self.sld_offset_y.setValue(my // 2)
            else:
                self.sld_offset_x.setValue(min(self.sld_offset_x.value(), mx))
                self.sld_offset_y.setValue(min(self.sld_offset_y.value(), my))
            self.sld_offset_x.setEnabled(True)
            self.sld_offset_y.setEnabled(True)
            self.sld_zoom.setEnabled(True)
            self.btn_center.setEnabled(True)
        else:
            self.sld_offset_x.setRange(0, 0)
            self.sld_offset_y.setRange(0, 0)
            self.sld_offset_x.setValue(0)
            self.sld_offset_y.setValue(0)
            self.sld_offset_x.setEnabled(False)
            self.sld_offset_y.setEnabled(False)
            self.sld_zoom.setEnabled(False)
            self.btn_center.setEnabled(False)

        self.lbl_off_x.setText(str(self.sld_offset_x.value()))
        self.lbl_off_y.setText(str(self.sld_offset_y.value()))

    def _update_nav(self):
        if self.src_img is None:
            return
        rotated = self._get_rotated()
        fit_mode = FIT_MODES[self.fit_combo.currentIndex()][1]
        zoom = self.sld_zoom.value() / 100.0
        nav = make_nav_image(
            rotated, fit_mode,
            self.sld_offset_x.value() if fit_mode == "cover" else 0,
            self.sld_offset_y.value() if fit_mode == "cover" else 0,
            zoom,
        )
        self.lbl_nav.setPixmap(pil_to_qpixmap(nav))

    def on_fit_changed(self):
        fit_mode = FIT_MODES[self.fit_combo.currentIndex()][1]
        if fit_mode == "contain":
            self.sld_zoom.setValue(100)
        self._update_offset_ranges(reset_to_center=True)
        self.schedule_refresh()

    def on_zoom_changed(self, v):
        self.lbl_zoom.setText(f"{v}%")
        self._update_offset_ranges(reset_to_center=False)
        self.schedule_refresh()

    def on_offset_changed(self):
        self.lbl_off_x.setText(str(self.sld_offset_x.value()))
        self.lbl_off_y.setText(str(self.sld_offset_y.value()))
        self.schedule_refresh()

    def on_center(self):
        self._update_offset_ranges(reset_to_center=True)
        self.schedule_refresh()

    def on_rotate(self):
        self.rotation = (self.rotation + 90) % 360
        self._update_offset_ranges(reset_to_center=True)
        self.schedule_refresh()
        self.statusBar().showMessage(f"已旋转到 {self.rotation}°")

    def on_reset_tone(self):
        for s in (self.exp, self.con, self.cla, self.sha, self.hig):
            s.reset()
        self.rotation = 0
        self.fit_combo.setCurrentIndex(0)
        self.sld_zoom.setValue(100)
        self._update_offset_ranges(reset_to_center=True)
        self.schedule_refresh()

    # ---------- 合成画布 ----------

    def composite_final(self):
        img = QImage(W, H, QImage.Format_RGB32)
        p = QPainter(img)
        bg = QColor("#FFFFFF" if self.bg_color_idx == 1 else "#000000")
        p.fillRect(0, 0, W, H, bg)
        if self.bin_image is not None:
            p.drawImage(0, 0, self.bin_image)
        p.drawImage(0, 0, self.edit_image)
        p.end()
        return img

    # ---------- 像素 ----------

    def _set_edit_pixel(self, x, y, index):
        self.edit_layer[y * W + x] = index
        if index == TRANSPARENT:
            self.edit_image.setPixelColor(x, y, QColor(0, 0, 0, 0))
        else:
            c = 0 if index == 0 else 255
            self.edit_image.setPixelColor(x, y, QColor(c, c, c, 255))

    def _set_bin_pixel(self, x, y, index):
        self.bin_layer_indices[y * W + x] = index
        if index == TRANSPARENT:
            self.bin_image.setPixelColor(x, y, QColor(0, 0, 0, 0))
        else:
            c = 0 if index == 0 else 255
            self.bin_image.setPixelColor(x, y, QColor(c, c, c, 255))

    def _paint_pixel(self, x, y):
        if x < 0 or x >= W or y < 0 or y >= H:
            return
        if self.current_tool == "eraser":
            self._set_edit_pixel(x, y, TRANSPARENT)
            if not self.chk_lock.isChecked():
                self._set_bin_pixel(x, y, TRANSPARENT)
        else:
            idx = self.current_color["index"]
            self._set_edit_pixel(x, y, idx)

    def _paint_brush(self, cx, cy):
        half = self.brush_size // 2
        for dy in range(-half, self.brush_size - half):
            for dx in range(-half, self.brush_size - half):
                self._paint_pixel(cx + dx, cy + dy)

    def _draw_line(self, x0, y0, x1, y1):
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self._paint_brush(x0, y0)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    # ---------- 画布事件 ----------

    def on_canvas_press(self, x, y):
        if self.source_choice is None:
            return
        if self.edit_locked:
            return
        if self.text_mode_active:
            self._push_undo()
            ok = self._draw_text_to_layer(x, y)
            if ok:
                self.text_mode_active = False
                self.canvas.clear_overlay()
                self.canvas.invalidate()
                self.statusBar().showMessage("文字已放置")
            else:
                self.undo_stack.pop()
                self._update_undo_buttons()
                self.statusBar().showMessage("文字未产生任何像素")
            return

        if self.current_tool == "eyedropper":
            self._pick_color_at(x, y)
            return

        self._push_undo()
        self._paint_brush(x, y)
        self.canvas.invalidate()

    def on_canvas_move(self, x0, y0, x1, y1):
        if self.edit_locked:
            return
        self._draw_line(x0, y0, x1, y1)
        self.canvas.invalidate()

    def on_canvas_release(self):
        self.canvas.invalidate()

    def on_canvas_hover(self, x, y):
        if not self.text_mode_active or self.edit_locked:
            return
        p = self._get_text_params()
        mask = render_text_mask(
            p["text"], p["family"], p["size"], p["bold"], p["italic"],
            p["underline"], p["direction"], p["align"], p["line_height"],
            p["rotate"], anchor=(x, y),
            letter_spacing=p["letter_spacing"],
        )
        preview = QImage(mask.size(), QImage.Format_ARGB32)
        preview.fill(0)
        pp = QPainter(preview)
        pp.drawImage(0, 0, mask)
        pp.setCompositionMode(QPainter.CompositionMode_SourceIn)
        pp.fillRect(preview.rect(), QColor("#75C7BD"))
        pp.end()
        self.canvas.set_overlay(preview)

    def on_canvas_leave(self):
        self.canvas.clear_overlay()

    # ---------- 吸管 ----------

    def _pick_color_at(self, x, y):
        R = 5
        black_count = 0
        total = 0
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                px, py = x + dx, y + dy
                if 0 <= px < W and 0 <= py < H:
                    if self._resolve_final_index(px, py) == 0:
                        black_count += 1
                    total += 1
        if total == 0:
            return

        if black_count / total > 0.5:
            self.current_color = {"index": 0}
            self._highlight_basic(0)
            self.statusBar().showMessage("已取色：黑")
        else:
            self.current_color = {"index": 1}
            self._highlight_basic(1)
            self.statusBar().showMessage("已取色：白")

    def _resolve_final_index(self, x, y):
        i = y * W + x
        v = self.edit_layer[i]
        if v == TRANSPARENT:
            v = self.bin_layer_indices[i]
        if v == TRANSPARENT:
            v = self.bg_color_idx
        return v

    # ---------- 文字 ----------

    def _get_text_params(self):
        return {
            "text": self.text_edit.toPlainText(),
            "family": self.cmb_font.currentData(),
            "size": self.spn_size.value(),
            "bold": self.btn_bold.isChecked(),
            "italic": self.btn_italic.isChecked(),
            "underline": self.btn_underline.isChecked(),
            "direction": self.cmb_dir.currentData(),
            "align": self.cmb_align.currentData(),
            "line_height": self.sld_lh.value() / 10.0,
            "letter_spacing": int(self.sld_ls.value()),
            "rotate": self.sld_rot.value(),
        }

    def _draw_text_to_layer(self, x, y):
        p = self._get_text_params()
        if not p["text"]:
            return False
        mask = render_text_mask(
            p["text"], p["family"], p["size"], p["bold"], p["italic"],
            p["underline"], p["direction"], p["align"], p["line_height"],
            p["rotate"], anchor=(x, y),
            letter_spacing=p["letter_spacing"],
        )
        count = 0
        for py in range(H):
            for px in range(W):
                if QColor(mask.pixel(px, py)).alpha() > 128:
                    self._set_edit_pixel(px, py, self.current_color["index"])
                    count += 1
        return count > 0

    def on_place_text(self):
        if self.source_choice is None:
            QMessageBox.warning(self, "提示", "请先选择数据源")
            return
        if self.edit_locked:
            QMessageBox.warning(self, "提示", "编辑锁已开启，请先解锁")
            return
        if not self.text_edit.toPlainText():
            QMessageBox.warning(self, "提示", "请先输入文字")
            return
        self.text_mode_active = True
        self.statusBar().showMessage("点击画布放置文字（Esc 取消）")

    def _on_text_param_changed(self):
        self.lbl_lh.setText(f"{self.sld_lh.value() / 10:.1f}")
        self.lbl_ls.setText(str(self.sld_ls.value()))
        self.lbl_rot.setText(f"{self.sld_rot.value()}°")
        if hasattr(self, "text_preview"):
            self.text_preview.update()
        if self.text_mode_active:
            self.canvas.clear_overlay()

    def _on_tool_clicked(self, tool_id):
        self.current_tool = tool_id
        if self.text_mode_active:
            self.text_mode_active = False
            self.canvas.clear_overlay()

    def _on_brush_changed(self, v):
        if v % 2 != 0:
            v -= 1
            self.sld_brush.setValue(v)
            return
        self.brush_size = v
        self.lbl_brush.setText(str(v))

    def _on_basic_clicked(self, idx):
        self.current_color = {"index": idx}
        self._highlight_basic(idx)

    def _highlight_basic(self, active_idx):
        for i, btn in enumerate(self.basic_btns):
            border = "#75c7bd" if i == active_idx else "#4a4943"
            hex_color = "#000000" if i == 0 else "#FFFFFF"
            btn.setStyleSheet(
                f"background:{hex_color};"
                f"border:2px solid {border};border-radius:4px;")

    def _on_bg_clicked(self, idx):
        self.bg_color_idx = idx
        for i, btn in enumerate(self.bg_btns):
            border = "#75c7bd" if i == idx else "#4a4943"
            hex_color = "#000000" if i == 0 else "#FFFFFF"
            btn.setStyleSheet(
                f"background:{hex_color};"
                f"border:2px solid {border};border-radius:4px;")
        self.canvas.invalidate()

    # ---------- 撤销 / 重做 ----------

    def _push_undo(self):
        self.undo_stack.append((
            bytearray(self.bin_layer_indices),
            bytearray(self.edit_layer),
        ))
        if len(self.undo_stack) > MAX_HISTORY:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._update_undo_buttons()

    def _restore_snapshot(self, snap):
        self.bin_layer_indices = bytearray(snap[0])
        self.edit_layer = bytearray(snap[1])
        self._rebuild_bin_image()
        self.edit_image = _render_indices_with_rgb(self.edit_layer)
        self.canvas.invalidate()

    def _update_undo_buttons(self):
        self.btn_undo.setEnabled(len(self.undo_stack) > 1)
        self.btn_redo.setEnabled(len(self.redo_stack) > 0)

    def on_undo(self):
        if len(self.undo_stack) <= 1:
            return
        self.redo_stack.append((
            bytearray(self.bin_layer_indices),
            bytearray(self.edit_layer),
        ))
        self._restore_snapshot(self.undo_stack.pop())
        self._update_undo_buttons()
        self.statusBar().showMessage("已撤销")

    def on_redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append((
            bytearray(self.bin_layer_indices),
            bytearray(self.edit_layer),
        ))
        self._restore_snapshot(self.redo_stack.pop())
        self._update_undo_buttons()
        self.statusBar().showMessage("已重做")

    def on_clear_draw(self):
        self._push_undo()
        self.edit_layer = bytearray([TRANSPARENT]) * (W * H)
        self.edit_image = QImage(W, H, QImage.Format_ARGB32)
        self.edit_image.fill(0)
        self.canvas.invalidate()
        self.statusBar().showMessage("已清空绘制层")

    def on_clear_all(self):
        self._push_undo()
        self.bin_layer_indices = bytearray([TRANSPARENT]) * (W * H)
        self.edit_layer = bytearray([TRANSPARENT]) * (W * H)
        self._rebuild_bin_image()
        self.edit_image = QImage(W, H, QImage.Format_ARGB32)
        self.edit_image.fill(0)
        self.canvas.invalidate()
        self.statusBar().showMessage("已清空全部（含源层）")

    # ---------- BIN 生成 ----------

    def _generate_bin(self):
        if self.source_choice is None:
            raise RuntimeError("未选择数据源")
        if self.source_choice == "dithered" and self.dithered_img is None:
            raise RuntimeError("请先加载图片")
        buf = bytearray(FRAME_BYTES)
        row_bytes = W // 8
        for y in range(H):
            for x in range(W):
                idx = self._resolve_final_index(x, y)
                if idx == 0:  # 黑色
                    buf[y * row_bytes + (x >> 3)] |= (0x80 >> (x & 7))
        return bytes(buf)

    # ---------- 导出 ----------

    def on_export(self):
        if self.source_choice is None:
            QMessageBox.warning(self, "提示", "请先选择数据源")
            return
        try:
            data = self._generate_bin()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成数据失败：{e}")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 BIN", "image_studio.bin", "Binary (*.bin)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(data)
            self.statusBar().showMessage(
                f"已保存：{path}（{len(data)} 字节）")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：{e}")

    # ---------- 上传 ----------

    def _set_upload_busy(self, busy):
        self.btn_upload.setEnabled(not busy)
        self.btn_export.setEnabled(not busy)
        self.btn_choose.setEnabled(not busy)
        self.btn_choose_source.setEnabled((not busy) and self._previews_ready())
        self.btn_load_bin_source.setEnabled(not busy)

    def on_upload(self):
        if self.source_choice is None:
            QMessageBox.warning(self, "提示", "请先选择数据源")
            return
        ip = clean_ip(self.edit_ip.text())
        if not ip:
            QMessageBox.warning(self, "提示", "请输入设备 IP")
            return
        try:
            import requests  # noqa: F401
        except ImportError:
            QMessageBox.warning(self, "缺少依赖", "pip install requests")
            return

        self._upload_ip = ip
        self._set_upload_busy(True)
        self.lbl_conn.setText(f"正在检查 {ip} …")
        self.lbl_conn.setStyleSheet("color:#888;font-weight:bold;")
        self.statusBar().showMessage(f"正在检查 {ip} 连接…")

        self.check_worker = CheckWorker(ip)
        self.check_worker.finished.connect(self._on_check_done)
        self.check_worker.start()

    def _on_check_done(self, online, message):
        ip = self._upload_ip
        if not online:
            self.lbl_conn.setText(f"✗ {message}（上传已取消）")
            self.lbl_conn.setStyleSheet("color:#c00;font-weight:bold;")
            self._set_upload_busy(False)
            QMessageBox.critical(self, "连接失败",
                                 f"设备 {ip} 无法连接：{message}")
            return

        self.lbl_conn.setText(f"✓ {message}，正在上传…")
        self.lbl_conn.setStyleSheet("color:#0a7a0a;font-weight:bold;")

        try:
            data = self._generate_bin()
        except Exception as e:
            self.lbl_conn.setText(f"✓ {message}（生成失败）")
            self._set_upload_busy(False)
            QMessageBox.critical(self, "错误", f"生成数据失败：{e}")
            return

        url = f"http://{ip}/upload"
        self.worker = UploadWorker(url, data)
        self.worker.finished.connect(self._on_upload_ok)
        self.worker.failed.connect(self._on_upload_fail)
        self.worker.start()

    def _on_upload_ok(self, msg):
        self._set_upload_busy(False)
        self.lbl_conn.setText("✓ 上传完成")
        self.lbl_conn.setStyleSheet("color:#0a7a0a;font-weight:bold;")
        self.statusBar().showMessage(f"上传完成：{msg}")

    def _on_upload_fail(self, msg):
        self._set_upload_busy(False)
        self.lbl_conn.setText("✗ 上传失败")
        self.lbl_conn.setStyleSheet("color:#c00;font-weight:bold;")
        QMessageBox.critical(self, "上传失败", msg)

    # ---------- 配置 ----------

    def _apply_config(self):
        cfg = self.config
        self.exp.slider.setValue(cfg.get("exposure", 0))
        self.con.slider.setValue(cfg.get("contrast", 0))
        self.cla.slider.setValue(cfg.get("clarity", 0))
        self.sha.slider.setValue(cfg.get("shadow", 0))
        self.hig.slider.setValue(cfg.get("highlight", 0))
        self.fit_combo.setCurrentIndex(cfg.get("fit_index", 0))
        self.dither_combo.setCurrentIndex(cfg.get("dither_index", 0))
        self.serp_check.setChecked(cfg.get("serpentine", True))
        self.edit_ip.setText(cfg.get("device_ip", "192.168.4.1"))
        self.sld_zoom.setValue(cfg.get("zoom", 100))

        self.bg_color_idx = cfg.get("bg_color_idx", 1)
        self._on_bg_clicked(self.bg_color_idx)
        self.sld_brush.setValue(cfg.get("brush_size", 4))

        self.spn_size.setValue(cfg.get("font_size", 32))
        fam = cfg.get("font_family", "Sans Serif")
        for i in range(self.cmb_font.count()):
            if self.cmb_font.itemData(i) == fam:
                self.cmb_font.setCurrentIndex(i)
                break
        d = cfg.get("direction", "horizontal")
        for i in range(self.cmb_dir.count()):
            if self.cmb_dir.itemData(i) == d:
                self.cmb_dir.setCurrentIndex(i)
                break
        a = cfg.get("align", "left")
        for i in range(self.cmb_align.count()):
            if self.cmb_align.itemData(i) == a:
                self.cmb_align.setCurrentIndex(i)
                break
        self.sld_lh.setValue(cfg.get("line_height_x10", 12))
        self.sld_ls.setValue(cfg.get("letter_spacing", 0))
        self.sld_rot.setValue(cfg.get("rotate", 0))

        self.btn_bold.setChecked(cfg.get("text_bold", False))
        self.btn_italic.setChecked(cfg.get("text_italic", False))
        self.btn_underline.setChecked(cfg.get("text_underline", False))
        self.text_edit.setPlainText(cfg.get("text_content", ""))

        tool = cfg.get("current_tool", "pencil")
        if tool in self.tool_btns:
            self.tool_btns[tool].setChecked(True)
        self.current_tool = tool

        color = cfg.get("current_color", {"index": 0})
        if isinstance(color, dict) and "index" in color:
            self.current_color = dict(color)
        else:
            self.current_color = {"index": 0}

        self._highlight_basic(self.current_color.get("index", 0))

        self.edit_locked = bool(cfg.get("edit_locked", False))
        self.chk_edit_lock.setChecked(self.edit_locked)

        self._on_text_param_changed()

    def _save_current_config(self):
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
            "zoom": self.sld_zoom.value(),

            "bg_color_idx": self.bg_color_idx,
            "brush_size": self.sld_brush.value(),
            "current_tool": self.current_tool,
            "current_color": dict(self.current_color),
            "edit_locked": self.edit_locked,

            "font_size": self.spn_size.value(),
            "font_family": self.cmb_font.currentData(),
            "direction": self.cmb_dir.currentData(),
            "align": self.cmb_align.currentData(),
            "line_height_x10": self.sld_lh.value(),
            "letter_spacing": self.sld_ls.value(),
            "rotate": self.sld_rot.value(),
            "text_bold": self.btn_bold.isChecked(),
            "text_italic": self.btn_italic.isChecked(),
            "text_underline": self.btn_underline.isChecked(),
            "text_content": self.text_edit.toPlainText(),
        }
        save_config(cfg)

    def closeEvent(self, event):
        self._save_current_config()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.text_mode_active:
            self.text_mode_active = False
            self.canvas.clear_overlay()
            self.statusBar().showMessage("已取消文字放置")
            return
        if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_Z:
            if event.modifiers() & Qt.ShiftModifier:
                self.on_redo()
            else:
                self.on_undo()
            return
        if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_Y:
            self.on_redo()
            return
        super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()