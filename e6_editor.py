# e6_editor.py — E6 黑白 BIN 编辑器（Qt 桌面客户端）
import sys
import io
import json
import copy
import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    Qt, QThread, Signal, QPoint, QPointF, QRect, QSize, QTimer,
)
from PySide6.QtGui import (
    QImage, QPixmap, QColor, QPainter, QPen, QFont, QFontMetrics,
    QIcon, QPainterPath,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QLineEdit, QGroupBox,
    QComboBox, QSlider, QCheckBox, QScrollArea, QSpinBox, QTextEdit,
    QSizePolicy, QFrame, QButtonGroup,
)

# ---------- 配置路径 ----------
CONFIG_PATH = Path(__file__).parent / "e6_image_core" / "e6_editor_config.json"
LEGACY_CONFIG_PATH = Path.home() / ".e6_editor_config.json"

W, H = 800, 480
FRAME_BYTES = W * H // 8
TRANSPARENT = 255

# 黑白调色板
PALETTE = [
    {"id": "black", "hex": "#000000", "label": "黑"},
    {"id": "white", "hex": "#FFFFFF", "label": "白"},
]

FONT_FAMILIES = [
    ("无衬线", "Sans Serif"),
    ("衬线", "Serif"),
    ("等宽", "Monospace"),
]

DIRECTIONS = [
    ("横排（左 → 右）", "horizontal"),
    ("竖排（列 左 → 右）", "vertical-lr"),
    ("竖排（列 右 → 左）", "vertical-rl"),
]

ALIGNS = [
    ("左对齐", "left"),
    ("居中", "center"),
    ("右对齐", "right"),
]

MAX_HISTORY = 20


# ================= 配置文件 =================

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


def clean_ip(text):
    ip = (text or "").strip()
    if ip.startswith("http://"):
        ip = ip[7:]
    elif ip.startswith("https://"):
        ip = ip[8:]
    return ip.rstrip("/")


# ================= 图层索引 <-> QImage =================

def indices_to_qimage(indices):
    buf = bytearray(W * H * 4)
    for i in range(W * H):
        idx = indices[i]
        off = i * 4
        if idx == TRANSPARENT:
            buf[off + 3] = 0
        else:
            c = 0 if idx == 0 else 255
            buf[off] = c
            buf[off + 1] = c
            buf[off + 2] = c
            buf[off + 3] = 255
    return QImage(bytes(buf), W, H, QImage.Format_ARGB32).copy()


# ================= 文字渲染 =================

def render_text_image(text, family, size, bold, italic, underline,
                      direction, align, line_height, rotate_deg,
                      anchor=(0, 0), color="#FFFFFF",
                      canvas_w=W, canvas_h=H, letter_spacing=0):
    if not text:
        return None

    img = QImage(canvas_w, canvas_h, QImage.Format_ARGB32)
    img.fill(0)

    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    font = QFont(family)
    font.setPixelSize(size)
    font.setBold(bold)
    font.setItalic(italic)
    painter.setFont(font)

    color_obj = QColor(color)
    painter.setPen(color_obj)
    painter.setBrush(color_obj)

    painter.translate(anchor[0], anchor[1])
    if rotate_deg != 0:
        painter.rotate(rotate_deg)

    fm = painter.fontMetrics()
    line_h_px = size * line_height
    lines = text.split("\n") if text else []
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

            dy = i * line_h_px + fm.ascent()

            if line:
                cx = dx
                for ch in chars:
                    painter.drawText(QPointF(cx, dy), ch)
                    cx += fm.horizontalAdvance(ch) + letter_spacing

            if underline and line:
                ul_y = i * line_h_px + size * 0.9
                painter.fillRect(int(dx), int(ul_y),
                                 int(total_w), ul_thick, color_obj)
    else:
        col_w = max(1, line_h_px + letter_spacing)
        total_w = len(lines) * col_w

        if align == "left":
            block_x = 0.0
        elif align == "center":
            block_x = -total_w / 2
        else:
            block_x = -total_w

        for ci, line in enumerate(lines):
            if direction == "vertical-lr":
                col_center_x = block_x + ci * col_w + col_w / 2
            else:
                col_center_x = block_x + total_w - ci * col_w - col_w / 2

            for ji, ch in enumerate(line):
                if ch in " \t":
                    continue
                cw = fm.horizontalAdvance(ch)
                char_center_y = ji * col_w + col_w / 2
                baseline_y = char_center_y + (fm.ascent() - fm.descent()) / 2
                painter.drawText(QPointF(col_center_x - cw / 2, baseline_y), ch)

            if underline and line:
                clean = [c for c in line if c not in " \t"]
                if clean:
                    max_cw = max(fm.horizontalAdvance(c) for c in clean)
                    ul_x = col_center_x + max_cw / 2 + 1
                    ul_y1 = col_w * 0.1
                    ul_y2 = len(clean) * col_w - col_w * 0.1
                    painter.fillRect(int(ul_x), int(ul_y1),
                                     ul_thick, int(ul_y2 - ul_y1), color_obj)

    painter.end()
    return img


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


# ================= 画布 Widget =================

class CanvasWidget(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
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

        if self._composite is None:
            self._composite = self.editor.composite_frame()

        r = self._fit_rect()
        p.drawImage(r, self._composite)

        if self._overlay is not None:
            p.setOpacity(0.75)
            p.drawImage(r, self._overlay)
            p.setOpacity(1.0)

        p.setPen(QPen(QColor("#4a4943"), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))

        if self.editor.edit_locked:
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
        if self.editor.edit_locked:
            return
        pos = self._widget_to_pixel(event.position().toPoint())
        if pos is None:
            return
        self.editor.on_canvas_press(pos[0], pos[1])
        self._drawing = True
        self._last_px = pos

    def mouseMoveEvent(self, event):
        if self.editor.edit_locked:
            return
        pos = self._widget_to_pixel(event.position().toPoint())
        if pos is None:
            return
        if self._drawing and self._last_px is not None:
            self.editor.on_canvas_move(self._last_px[0], self._last_px[1], pos[0], pos[1])
            self._last_px = pos
        elif self.editor.text_mode_active:
            self.editor.on_canvas_hover(pos[0], pos[1])

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._drawing:
            self.editor.on_canvas_release()
            self._drawing = False
            self._last_px = None

    def leaveEvent(self, event):
        self.editor.on_canvas_leave()


# ================= 文字预览 Widget =================

class TextPreviewWidget(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setFixedSize(260, 150)
        self.setStyleSheet("background:#000;border:1px solid #34332f;border-radius:4px;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))

        params = self.editor.get_text_params()
        text = params["text"]
        if not text.strip():
            p.setPen(QColor("#555555"))
            p.setFont(QFont("Sans Serif", 10))
            p.drawText(self.rect(), Qt.AlignCenter, "（无文字）")
            return

        bw, bh = measure_text_bbox(
            params["family"], params["size"], params["bold"],
            params["italic"], params["direction"], text,
            params["line_height"], params["letter_spacing"],
        )
        rot = abs(params["rotate"]) * math.pi / 180
        cos_v, sin_v = abs(math.cos(rot)), abs(math.sin(rot))
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
        img = render_text_image(
            text, params["family"], params["size"], params["bold"],
            params["italic"], params["underline"], params["direction"],
            params["align"], params["line_height"], params["rotate"],
            anchor=anchor, color="#FFFFFF",
            canvas_w=W, canvas_h=H,
            letter_spacing=params["letter_spacing"],
        )
        if img is None:
            return

        p.save()
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(scale, scale)
        p.translate(-c_rel_x, -c_rel_y)
        p.drawImage(QPointF(0, 0), img)
        p.restore()


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
                              timeout=60)
            self.finished.emit(f"{r.status_code} {r.text}")
        except Exception as e:
            self.failed.emit(str(e))


# ================= 主窗口 =================

class EditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E6 黑白 BIN 编辑器")
        self.resize(1400, 1000)

        self.config = load_config()

        self.bin_indices = bytearray([TRANSPARENT]) * (W * H)
        self.draw_indices = bytearray([TRANSPARENT]) * (W * H)
        self.bin_image = None
        self.draw_image = None
        self._rebuild_images()

        self.bg_color_idx = 1
        self.content_locked = False
        self.current_color = {"index": 0}
        self.current_tool = "pencil"
        self.brush_size = 4
        self.text_mode_active = False

        self.edit_locked = False

        self._last_bin_path = ""
        self._last_save_dir = ""

        self.undo_stack = [(self.bin_indices[:], self.draw_indices[:])]
        self.redo_stack = []

        self.worker = None
        self.check_worker = None
        self._upload_ip = ""

        self._build_ui()
        self._apply_config()
        self._refresh_canvas()

    def _rebuild_images(self):
        self.bin_image = indices_to_qimage(self.bin_indices)
        self.draw_image = indices_to_qimage(self.draw_indices)

    def composite_frame(self):
        img = QImage(W, H, QImage.Format_RGB32)
        p = QPainter(img)
        bg = QColor(PALETTE[self.bg_color_idx]["hex"])
        p.fillRect(0, 0, W, H, bg)
        p.drawImage(0, 0, self.bin_image)
        p.drawImage(0, 0, self.draw_image)
        p.end()
        return img

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(400)
        left_w = QWidget()
        ll = QVBoxLayout(left_w)
        left_scroll.setWidget(left_w)
        root.addWidget(left_scroll)

        # 文件
        fg = QGroupBox("文件")
        fl = QVBoxLayout(fg)
        self.btn_load = QPushButton("打开 BIN")
        self.btn_load.clicked.connect(self.on_load_bin)
        fl.addWidget(self.btn_load)
        self.lbl_file = QLabel("未加载文件")
        self.lbl_file.setStyleSheet("color:#888;font-size:11px;")
        self.lbl_file.setWordWrap(True)
        fl.addWidget(self.lbl_file)
        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("设备 IP:"))
        self.edit_ip = QLineEdit("192.168.4.1")
        ip_row.addWidget(self.edit_ip, 1)
        fl.addLayout(ip_row)
        ll.addWidget(fg)

        # 工具（含编辑锁）
        tg = QGroupBox("工具")
        tl = QVBoxLayout(tg)

        self.chk_edit_lock = QCheckBox("🔒 编辑锁（锁定后画布不可编辑）")
        self.chk_edit_lock.setStyleSheet("font-weight:bold;")
        self.chk_edit_lock.stateChanged.connect(self._on_edit_lock_changed)
        tl.addWidget(self.chk_edit_lock)

        tool_row = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        for tool_id, label in [("pencil", "✏️ 画笔"), ("eraser", "🧽 橡皮"),
                               ("eyedropper", "💧 吸管")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("tool_id", tool_id)
            if tool_id == "pencil":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked=False, t=tool_id: self._on_tool_clicked(t))
            self.tool_group.addButton(btn)
            tool_row.addWidget(btn)
        tl.addLayout(tool_row)

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
        tl.addLayout(brush_row)
        ll.addWidget(tg)

        # 基础色（黑白）
        bg_group = QGroupBox("基础色")
        bgl = QVBoxLayout(bg_group)
        basic_row = QHBoxLayout()
        self.basic_btns = []
        for i, p in enumerate(PALETTE):
            btn = QPushButton()
            btn.setFixedSize(50, 50)
            btn.setStyleSheet(
                f"background:{p['hex']};"
                f"border:2px solid {'#75c7bd' if i == 0 else '#4a4943'};"
                f"border-radius:4px;"
            )
            btn.setProperty("color_idx", i)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_basic_clicked(idx))
            basic_row.addWidget(btn)
            self.basic_btns.append(btn)
        bgl.addLayout(basic_row)
        ll.addWidget(bg_group)

        # 背景色
        bgc_group = QGroupBox("背景色")
        bgcl = QVBoxLayout(bgc_group)
        bgc_row = QHBoxLayout()
        self.bg_btns = []
        for i, p in enumerate(PALETTE):
            btn = QPushButton()
            btn.setFixedSize(40, 40)
            btn.setStyleSheet(
                f"background:{p['hex']};border:2px solid "
                f"{'#75c7bd' if i == 1 else '#4a4943'};border-radius:4px;"
            )
            btn.setProperty("bg_idx", i)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_bg_clicked(idx))
            bgc_row.addWidget(btn)
            self.bg_btns.append(btn)
        bgcl.addLayout(bgc_row)
        ll.addWidget(bgc_group)

        # 导入层锁定
        lock_group = QGroupBox("导入层锁定")
        lock_l = QVBoxLayout(lock_group)
        self.chk_lock = QCheckBox("锁定（橡皮只擦笔画和文字）")
        self.chk_lock.stateChanged.connect(self._on_lock_changed)
        lock_l.addWidget(self.chk_lock)
        hint = QLabel("未锁定时：橡皮会把笔画和下方的 BIN 内容一起擦掉。")
        hint.setStyleSheet("color:#888;font-size:11px;")
        hint.setWordWrap(True)
        lock_l.addWidget(hint)
        ll.addWidget(lock_group)

        # 文字
        text_group = QGroupBox("文字")
        tgl = QVBoxLayout(text_group)

        tgl.addWidget(QLabel("内容（支持换行）:"))
        self.text_edit = QTextEdit()
        self.text_edit.setFixedHeight(60)
        self.text_edit.setPlaceholderText("输入要放置的文字，可换行")
        self.text_edit.textChanged.connect(self._on_text_param_changed)
        tgl.addWidget(self.text_edit)

        f_row = QHBoxLayout()
        f_row.addWidget(QLabel("字体:"))
        self.cmb_font = QComboBox()
        for label, fam in FONT_FAMILIES:
            self.cmb_font.addItem(label, fam)
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
        for label, d in DIRECTIONS:
            self.cmb_dir.addItem(label, d)
        self.cmb_dir.currentIndexChanged.connect(self._on_text_param_changed)
        d_row.addWidget(self.cmb_dir, 1)
        tgl.addLayout(d_row)

        al_row = QHBoxLayout()
        al_row.addWidget(QLabel("对齐:"))
        self.cmb_align = QComboBox()
        for label, a in ALIGNS:
            self.cmb_align.addItem(label, a)
        self.cmb_align.currentIndexChanged.connect(self._on_text_param_changed)
        al_row.addWidget(self.cmb_align, 1)
        al_row.addWidget(QLabel("样式:"))
        self.btn_bold = QPushButton("B")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setFixedSize(32, 32)
        self.btn_bold.setStyleSheet("font-weight:bold;font-family:serif;")
        self.btn_bold.clicked.connect(self._on_text_param_changed)
        al_row.addWidget(self.btn_bold)
        self.btn_italic = QPushButton("I")
        self.btn_italic.setCheckable(True)
        self.btn_italic.setFixedSize(32, 32)
        self.btn_italic.setStyleSheet("font-style:italic;font-family:serif;")
        self.btn_italic.clicked.connect(self._on_text_param_changed)
        al_row.addWidget(self.btn_italic)
        self.btn_underline = QPushButton("U")
        self.btn_underline.setCheckable(True)
        self.btn_underline.setFixedSize(32, 32)
        self.btn_underline.setStyleSheet("text-decoration:underline;font-family:serif;")
        self.btn_underline.clicked.connect(self._on_text_param_changed)
        al_row.addWidget(self.btn_underline)
        tgl.addLayout(al_row)

        lh_row = QHBoxLayout()
        lh_row.addWidget(QLabel("行高:"))
        self.sld_line_height = QSlider(Qt.Horizontal)
        self.sld_line_height.setRange(8, 25)
        self.sld_line_height.setValue(12)
        self.sld_line_height.valueChanged.connect(self._on_line_height_changed)
        lh_row.addWidget(self.sld_line_height, 1)
        self.lbl_line_height = QLabel("1.2")
        self.lbl_line_height.setFixedWidth(36)
        lh_row.addWidget(self.lbl_line_height)
        tgl.addLayout(lh_row)

        ls_row = QHBoxLayout()
        ls_row.addWidget(QLabel("字间距:"))
        self.sld_letter_spacing = QSlider(Qt.Horizontal)
        self.sld_letter_spacing.setRange(-20, 40)
        self.sld_letter_spacing.setValue(0)
        self.sld_letter_spacing.valueChanged.connect(self._on_letter_spacing_changed)
        ls_row.addWidget(self.sld_letter_spacing, 1)
        self.lbl_letter_spacing = QLabel("0")
        self.lbl_letter_spacing.setFixedWidth(36)
        ls_row.addWidget(self.lbl_letter_spacing)
        tgl.addLayout(ls_row)

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("旋转:"))
        self.sld_rotate = QSlider(Qt.Horizontal)
        self.sld_rotate.setRange(-180, 180)
        self.sld_rotate.setValue(0)
        self.sld_rotate.valueChanged.connect(self._on_rotate_changed)
        rot_row.addWidget(self.sld_rotate, 1)
        self.lbl_rotate = QLabel("0°")
        self.lbl_rotate.setFixedWidth(40)
        rot_row.addWidget(self.lbl_rotate)
        tgl.addLayout(rot_row)

        tgl.addWidget(QLabel("预览:"))
        self.text_preview = TextPreviewWidget(self)
        tgl.addWidget(self.text_preview)

        self.btn_place_text = QPushButton("放置文字")
        self.btn_place_text.clicked.connect(self.on_place_text)
        tgl.addWidget(self.btn_place_text)
        tip = QLabel("点击后在画布上点一次确定锚点，Esc 取消。")
        tip.setStyleSheet("color:#888;font-size:11px;")
        tip.setWordWrap(True)
        tgl.addWidget(tip)
        ll.addWidget(text_group)

        # 历史 / 清空
        hist_group = QGroupBox("历史 / 清空")
        hl = QVBoxLayout(hist_group)
        undo_row = QHBoxLayout()
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(self.on_undo)
        undo_row.addWidget(self.btn_undo)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.clicked.connect(self.on_redo)
        undo_row.addWidget(self.btn_redo)
        hl.addLayout(undo_row)

        clear_row = QHBoxLayout()
        self.btn_clear = QPushButton("清空绘制层")
        self.btn_clear.clicked.connect(self.on_clear_draw)
        clear_row.addWidget(self.btn_clear)
        self.btn_clear_all = QPushButton("清空全部")
        self.btn_clear_all.setStyleSheet(
            "color:#ffb4a8;border:1px solid #6a2a2a;"
            "background:#2d2c27;padding:9px 12px;border-radius:4px;font-weight:700;"
        )
        self.btn_clear_all.clicked.connect(self.on_clear_all)
        clear_row.addWidget(self.btn_clear_all)
        hl.addLayout(clear_row)

        save_row = QHBoxLayout()
        self.btn_save = QPushButton("保存 BIN")
        self.btn_save.clicked.connect(self.on_save_bin)
        save_row.addWidget(self.btn_save)
        self.btn_upload = QPushButton("显示到屏幕")
        self.btn_upload.setStyleSheet(
            "background:#f2f2ed;color:#1a1a1a;border:1px solid #f2f2ed;"
            "padding:9px 12px;border-radius:4px;font-weight:700;"
        )
        self.btn_upload.clicked.connect(self.on_upload)
        save_row.addWidget(self.btn_upload)
        hl.addLayout(save_row)

        self.lbl_conn_status = QLabel("就绪")
        self.lbl_conn_status.setStyleSheet("color:#888;font-weight:bold;")
        self.lbl_conn_status.setWordWrap(True)
        hl.addWidget(self.lbl_conn_status)

        ll.addWidget(hist_group)
        ll.addStretch()

        # 右侧画布
        self.canvas = CanvasWidget(self)
        root.addWidget(self.canvas, 1)

        self.statusBar().showMessage("就绪")
        self._update_undo_buttons()

    def _on_edit_lock_changed(self, state):
        self.edit_locked = bool(state)
        if self.edit_locked and self.text_mode_active:
            self.text_mode_active = False
            self.canvas.clear_overlay()
        self.canvas.invalidate()
        self.statusBar().showMessage(
            "编辑已锁定，画布不可操作" if self.edit_locked else "编辑已解锁")

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
            if i == active_idx:
                btn.setStyleSheet(
                    f"background:{PALETTE[i]['hex']};"
                    f"border:2px solid #75c7bd;border-radius:4px;")
            else:
                btn.setStyleSheet(
                    f"background:{PALETTE[i]['hex']};"
                    f"border:2px solid #4a4943;border-radius:4px;")

    def _on_bg_clicked(self, idx):
        self.bg_color_idx = idx
        for i, btn in enumerate(self.bg_btns):
            border = "#75c7bd" if i == idx else "#4a4943"
            btn.setStyleSheet(
                f"background:{PALETTE[i]['hex']};"
                f"border:2px solid {border};border-radius:4px;")
        self._refresh_canvas()

    def _on_lock_changed(self, state):
        self.content_locked = (state == Qt.CheckState.Checked.value or state == 2)

    def _on_line_height_changed(self, v):
        lh = v / 10.0
        self.lbl_line_height.setText(f"{lh:.1f}")
        self._on_text_param_changed()

    def _on_letter_spacing_changed(self, v):
        self.lbl_letter_spacing.setText(str(v))
        self._on_text_param_changed()

    def _on_rotate_changed(self, v):
        self.lbl_rotate.setText(f"{v}°")
        self._on_text_param_changed()

    def _on_text_param_changed(self):
        if hasattr(self, "text_preview"):
            self.text_preview.update()
        if self.text_mode_active:
            self.canvas.clear_overlay()

    def get_text_params(self):
        return {
            "text": self.text_edit.toPlainText(),
            "family": self.cmb_font.currentData(),
            "size": self.spn_size.value(),
            "bold": self.btn_bold.isChecked(),
            "italic": self.btn_italic.isChecked(),
            "underline": self.btn_underline.isChecked(),
            "direction": self.cmb_dir.currentData(),
            "align": self.cmb_align.currentData(),
            "line_height": self.sld_line_height.value() / 10.0,
            "letter_spacing": int(self.sld_letter_spacing.value()),
            "rotate": self.sld_rotate.value(),
        }

    def _set_bin_pixel(self, x, y, index):
        self.bin_indices[y * W + x] = index
        if index == TRANSPARENT:
            self.bin_image.setPixelColor(x, y, QColor(0, 0, 0, 0))
        else:
            c = 0 if index == 0 else 255
            self.bin_image.setPixelColor(x, y, QColor(c, c, c, 255))

    def _set_draw_pixel(self, x, y, index):
        self.draw_indices[y * W + x] = index
        if index == TRANSPARENT:
            self.draw_image.setPixelColor(x, y, QColor(0, 0, 0, 0))
        else:
            c = 0 if index == 0 else 255
            self.draw_image.setPixelColor(x, y, QColor(c, c, c, 255))

    def _paint_pixel(self, x, y):
        if x < 0 or x >= W or y < 0 or y >= H:
            return
        if self.current_tool == "eraser":
            self._set_draw_pixel(x, y, TRANSPARENT)
            if not self.content_locked:
                self._set_bin_pixel(x, y, TRANSPARENT)
        else:
            self._set_draw_pixel(x, y, self.current_color["index"])

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

    def on_canvas_press(self, x, y):
        if self.edit_locked:
            return
        if self.text_mode_active:
            self._push_undo()
            ok = self._draw_text_to_layer(x, y)
            if ok:
                self.text_mode_active = False
                self.canvas.clear_overlay()
                self._refresh_canvas()
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
        p = self.get_text_params()
        img = render_text_image(
            p["text"], p["family"], p["size"], p["bold"], p["italic"],
            p["underline"], p["direction"], p["align"], p["line_height"],
            p["rotate"], anchor=(x, y), color="#75C7BD",
            letter_spacing=p["letter_spacing"],
        )
        if img is not None:
            self.canvas.set_overlay(img)

    def on_canvas_leave(self):
        self.canvas.clear_overlay()

    def _pick_color_at(self, x, y):
        R = 5
        black_count = 0
        total = 0
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                px, py = x + dx, y + dy
                if 0 <= px < W and 0 <= py < H:
                    if self._resolve_index(px, py) == 0:
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

    def _resolve_index(self, x, y):
        i = y * W + x
        v = self.draw_indices[i]
        if v == TRANSPARENT:
            v = self.bin_indices[i]
        if v == TRANSPARENT:
            v = self.bg_color_idx
        return v

    def _draw_text_to_layer(self, x, y):
        p = self.get_text_params()
        if not p["text"]:
            return False

        img = render_text_image(
            p["text"], p["family"], p["size"], p["bold"], p["italic"],
            p["underline"], p["direction"], p["align"], p["line_height"],
            p["rotate"], anchor=(x, y), color="#FFFFFF",
            letter_spacing=p["letter_spacing"],
        )
        if img is None:
            return False

        count = 0
        for py in range(H):
            for px in range(W):
                if QColor(img.pixel(px, py)).alpha() > 128:
                    self._set_draw_pixel(px, py, self.current_color["index"])
                    count += 1

        return count > 0

    def on_place_text(self):
        if self.edit_locked:
            QMessageBox.warning(self, "提示", "编辑锁已开启，请先解锁")
            return
        if not self.text_edit.toPlainText():
            QMessageBox.warning(self, "提示", "请先输入文字")
            return
        self.text_mode_active = True
        self.statusBar().showMessage("点击画布放置文字（Esc 取消）")

    def _push_undo(self):
        self.undo_stack.append((self.bin_indices[:], self.draw_indices[:]))
        if len(self.undo_stack) > MAX_HISTORY:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._update_undo_buttons()

    def _restore(self, snap):
        self.bin_indices = bytearray(snap[0])
        self.draw_indices = bytearray(snap[1])
        self._rebuild_images()
        self._refresh_canvas()

    def _update_undo_buttons(self):
        self.btn_undo.setEnabled(len(self.undo_stack) > 1)
        self.btn_redo.setEnabled(len(self.redo_stack) > 0)

    def on_undo(self):
        if len(self.undo_stack) <= 1:
            return
        self.redo_stack.append((self.bin_indices[:], self.draw_indices[:]))
        self._restore(self.undo_stack.pop())
        self._update_undo_buttons()
        self.statusBar().showMessage("已撤销")

    def on_redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append((self.bin_indices[:], self.draw_indices[:]))
        self._restore(self.redo_stack.pop())
        self._update_undo_buttons()
        self.statusBar().showMessage("已重做")

    def on_clear_draw(self):
        self._push_undo()
        self.draw_indices = bytearray([TRANSPARENT]) * (W * H)
        self._rebuild_images()
        self._refresh_canvas()
        self.statusBar().showMessage("已清空绘制层")

    def on_clear_all(self):
        unlocked_msg = ""
        if self.content_locked:
            self.content_locked = False
            self.chk_lock.setChecked(False)
            unlocked_msg = "（已自动解锁导入层）"
        self._push_undo()
        self.draw_indices = bytearray([TRANSPARENT]) * (W * H)
        self.bin_indices = bytearray([TRANSPARENT]) * (W * H)
        self._rebuild_images()
        self._refresh_canvas()
        self.statusBar().showMessage(f"已清空全部{unlocked_msg}")

    def _pack_layers(self):
        buf = bytearray(W * H // 8)
        row_bytes = W // 8
        for y in range(H):
            for x in range(W):
                idx = self._resolve_index(x, y)
                if idx == 0:  # 黑色
                    buf[y * row_bytes + (x >> 3)] |= (0x80 >> (x & 7))
        return bytes(buf)

    def _unpack(self, data):
        out = bytearray([TRANSPARENT]) * (W * H)
        row_bytes = W // 8
        for y in range(H):
            for x in range(W):
                byte_idx = y * row_bytes + (x >> 3)
                bit = (data[byte_idx] >> (7 - (x & 7))) & 1
                out[y * W + x] = 0 if bit else 1
        return out

    def on_load_bin(self):
        initial = self._last_bin_path
        if initial and not Path(initial).exists():
            initial = ""
        if not initial and self._last_save_dir:
            initial = self._last_save_dir

        path, _ = QFileDialog.getOpenFileName(
            self, "打开 BIN", initial, "Binary (*.bin)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            if len(data) != FRAME_BYTES:
                QMessageBox.critical(
                    self, "错误",
                    f"文件大小错误：{len(data)} 字节，应为 {FRAME_BYTES}"
                )
                return
            self._push_undo()
            self.bin_indices = self._unpack(data)
            self.draw_indices = bytearray([TRANSPARENT]) * (W * H)
            self._rebuild_images()
            self._refresh_canvas()
            self._last_bin_path = path
            self._last_save_dir = str(Path(path).parent)

            self.lbl_file.setText(
                f"已加载：{Path(path).name}（{len(data)} 字节）\n"
                f"路径：{path}"
            )
            self._save_current_config()
            self.statusBar().showMessage("BIN 已加载到导入层")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载失败：{e}")

    def on_save_bin(self):
        if self._last_save_dir:
            default_path = str(Path(self._last_save_dir) / "image_edited.bin")
        else:
            default_path = "image_edited.bin"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 BIN", default_path, "Binary (*.bin)"
        )
        if not path:
            return
        try:
            data = self._pack_layers()
            with open(path, "wb") as f:
                f.write(data)
            self._last_save_dir = str(Path(path).parent)
            self.statusBar().showMessage(f"已保存：{path}（{len(data)} 字节）")
            self._save_current_config()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：{e}")

    def _set_upload_busy(self, busy):
        self.btn_upload.setEnabled(not busy)
        self.btn_load.setEnabled(not busy)
        self.btn_save.setEnabled(not busy)

    def on_upload(self):
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
        self.lbl_conn_status.setText(f"正在检查 {ip} …")
        self.lbl_conn_status.setStyleSheet("color:#888;font-weight:bold;")

        self.check_worker = CheckWorker(ip)
        self.check_worker.finished.connect(self._on_check_done)
        self.check_worker.start()

    def _on_check_done(self, online, message):
        ip = self._upload_ip
        if not online:
            self.lbl_conn_status.setText(f"✗ {message}（上传已取消）")
            self.lbl_conn_status.setStyleSheet("color:#c00;font-weight:bold;")
            self._set_upload_busy(False)
            QMessageBox.critical(self, "连接失败",
                                 f"设备 {ip} 无法连接：{message}")
            return

        self.lbl_conn_status.setText(f"✓ {message}，正在上传…")
        self.lbl_conn_status.setStyleSheet("color:#0a7a0a;font-weight:bold;")

        try:
            data = self._pack_layers()
        except Exception as e:
            self.lbl_conn_status.setText(f"✓ {message}（生成失败）")
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
        self.lbl_conn_status.setText("✓ 上传完成")
        self.lbl_conn_status.setStyleSheet("color:#0a7a0a;font-weight:bold;")
        self.statusBar().showMessage(f"上传完成：{msg}")

    def _on_upload_fail(self, msg):
        self._set_upload_busy(False)
        self.lbl_conn_status.setText("✗ 上传失败")
        self.lbl_conn_status.setStyleSheet("color:#c00;font-weight:bold;")
        QMessageBox.critical(self, "上传失败", msg)

    def _refresh_canvas(self):
        self.canvas.invalidate()

    def _apply_config(self):
        cfg = self.config

        self.edit_ip.setText(cfg.get("device_ip", "192.168.4.1"))
        self.sld_brush.setValue(cfg.get("brush_size", 4))
        self.bg_color_idx = cfg.get("bg_color_idx", 1)
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

        self.sld_line_height.setValue(cfg.get("line_height_x10", 12))
        self.sld_letter_spacing.setValue(cfg.get("letter_spacing", 0))
        self.sld_rotate.setValue(cfg.get("rotate", 0))

        self.btn_bold.setChecked(cfg.get("text_bold", False))
        self.btn_italic.setChecked(cfg.get("text_italic", False))
        self.btn_underline.setChecked(cfg.get("text_underline", False))

        self.text_edit.setPlainText(cfg.get("text_content", ""))

        self.content_locked = bool(cfg.get("content_locked", False))
        self.chk_lock.setChecked(self.content_locked)

        self.edit_locked = bool(cfg.get("edit_locked", False))
        self.chk_edit_lock.setChecked(self.edit_locked)

        tool = cfg.get("current_tool", "pencil")
        for btn in self.tool_group.buttons():
            if btn.property("tool_id") == tool:
                btn.setChecked(True)
                break
        self.current_tool = tool

        color = cfg.get("current_color", {"index": 0})
        if isinstance(color, dict) and "index" in color:
            self.current_color = dict(color)
        else:
            self.current_color = {"index": 0}

        self._highlight_basic(self.current_color.get("index", 0))

        self._last_bin_path = cfg.get("last_bin_path", "")
        self._last_save_dir = cfg.get("last_save_dir", "")

        if self._last_bin_path:
            p = Path(self._last_bin_path)
            if p.exists():
                self.lbl_file.setText(
                    f"上次：{p.name}（点击「打开 BIN」加载）\n"
                    f"路径：{self._last_bin_path}"
                )
            else:
                self.lbl_file.setText(
                    f"上次文件已不存在：\n{self._last_bin_path}"
                )

        self._on_bg_clicked(self.bg_color_idx)
        self._on_text_param_changed()

    def _save_current_config(self):
        cfg = {
            "device_ip": self.edit_ip.text().strip(),
            "brush_size": self.sld_brush.value(),
            "bg_color_idx": self.bg_color_idx,
            "font_size": self.spn_size.value(),
            "font_family": self.cmb_font.currentData(),
            "direction": self.cmb_dir.currentData(),
            "align": self.cmb_align.currentData(),
            "line_height_x10": self.sld_line_height.value(),
            "letter_spacing": self.sld_letter_spacing.value(),
            "rotate": self.sld_rotate.value(),

            "current_tool": self.current_tool,
            "current_color": dict(self.current_color),
            "text_bold": self.btn_bold.isChecked(),
            "text_italic": self.btn_italic.isChecked(),
            "text_underline": self.btn_underline.isChecked(),
            "text_content": self.text_edit.toPlainText(),
            "content_locked": self.content_locked,
            "edit_locked": self.edit_locked,

            "last_bin_path": self._last_bin_path,
            "last_save_dir": self._last_save_dir,
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
    w = EditorWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()