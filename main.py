import os
import sys
import ctypes
import random
import time
from typing import Optional, Dict, Tuple, List

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QSettings, QRect,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
)
from PySide6.QtGui import (
    QFont, QIcon, QAction, QActionGroup, QKeySequence, QShortcut,
    QColor, QCursor, QFontMetrics
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QFrame,
    QSystemTrayIcon, QMenu, QWidgetAction, QSlider,
    QGraphicsDropShadowEffect, QHBoxLayout
)

import psutil

from sensors_lhm import LhmReader
from sensors_psutil import PsutilReader


# -------------------------
# Windows helpers (click-through + startup)
# -------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080

try:
    import winreg
except Exception:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "MonitorOverlay"


def _get_hwnd(widget: QWidget) -> int:
    return int(widget.winId())


def set_click_through(widget: QWidget, enabled: bool):
    hwnd = _get_hwnd(widget)
    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex_style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW
    if enabled:
        ex_style |= WS_EX_TRANSPARENT
    else:
        ex_style &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                        0x0001 | 0x0002 | 0x0020)  # NOMOVE|NOSIZE|FRAMECHANGED


def _exe_path_for_startup() -> str:
    if getattr(sys, "frozen", False):
        return f"\"{sys.executable}\""
    py = sys.executable
    script = os.path.abspath(sys.argv[0])
    return f"\"{py}\" \"{script}\""


def is_startup_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            v, _ = winreg.QueryValueEx(k, RUN_VALUE_NAME)
            return bool(v)
    except Exception:
        return False


def set_startup_enabled(enabled: bool):
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, _exe_path_for_startup())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        pass


# -------------------------
# Formatting helpers
# -------------------------
def clamp(n: int, a: int, b: int) -> int:
    return max(a, min(b, n))


def fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "--"
    return f"{v:.0f}%"


def fmt_rate_mb_s(v: Optional[float]) -> str:
    if v is None:
        return "--MB/s"
    return f"{v:.2f}MB/s"


def fmt_rate_short(v: Optional[float]) -> str:
    """
    简写速度（用于 DISK 超短显示）：
      <10 => 1位小数
      >=10 => 0位
    """
    if v is None:
        return "--"
    if v < 10:
        return f"{v:.1f}"
    return f"{v:.0f}"


# -------------------------
# Disk rate reader (R/W MB/s)
# -------------------------
class DiskRateReader:
    """
    使用 psutil.disk_io_counters() 计算全盘总读/写 MB/s
    """
    def __init__(self):
        self._last = None
        self._last_t = None

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            io = psutil.disk_io_counters()
            if io is None:
                return None, None

            now = time.time()
            if self._last is None or self._last_t is None:
                self._last = io
                self._last_t = now
                return 0.0, 0.0

            dt = max(1e-3, now - self._last_t)
            read_bps = (io.read_bytes - self._last.read_bytes) / dt
            write_bps = (io.write_bytes - self._last.write_bytes) / dt

            self._last = io
            self._last_t = now

            r = read_bps / (1024 * 1024)
            w = write_bps / (1024 * 1024)
            if r < 0:
                r = 0.0
            if w < 0:
                w = 0.0
            return r, w
        except Exception:
            return None, None


# -------------------------
# Phrase buckets (stable by range)
# -------------------------
Buckets = List[Tuple[float, float, List[str]]]

CPU_BUCKETS: Buckets = [
    (0, 10, ["摸鱼模式 😴", "待机冥想 🧘", "风扇在休假 🌿"]),
    (10, 30, ["轻松写写 ✍️", "低功耗巡航 🛫", "小跑一下 🏃"]),
    (30, 60, ["认真干活中 🛠️", "多线程开工 🧵", "正在加班 ☕"]),
    (60, 85, ["火力全开 💪", "CPU 在狂奔 🏎️", "性能模式 ON ⚡"]),
    (85, 101, ["快冒烟了 🔥", "别再加任务了 😵", "风扇起飞 ✈️"]),
]
GPU_BUCKETS: Buckets = [
    (0, 10, ["显卡在睡觉 💤", "桌面模式 🖥️", "低频养生 🌙"]),
    (10, 35, ["轻量渲染 🎨", "小试牛刀 🐮", "打个小怪 👾"]),
    (35, 65, ["稳稳输出 🎯", "正在加速 🚀", "甜品负载 🍰"]),
    (65, 90, ["光追开到爽 ✨", "显卡在燃烧 🔥", "帧数冲刺 🏁"]),
    (90, 101, ["核弹渲染 ☢️", "GPU：我尽力了 😭", "要爆了 📢"]),
]
RAM_BUCKETS: Buckets = [
    (0, 25, ["内存很松 🫧", "还很空 😌", "随便开都行 🧃"]),
    (25, 55, ["占用正常 ✅", "稳稳的 🧘", "够用就好 🙂"]),
    (55, 75, ["开始拥挤了 🚶", "有点挤 😅", "注意后台 👀"]),
    (75, 90, ["内存吃紧 🧨", "要清理了 🧹", "后台太多了 😵"]),
    (90, 101, ["内存告急 🚨", "快溢出了 🫠", "请关闭点东西 😭"]),
]

# ✅ DISK 文案：基于 (读+写) 的 MB/s 合计分档，保持短
DISK_IO_BUCKETS: Buckets = [
    (0, 0.3,   ["磁盘打盹 💤", "几乎不动 🤫", "空闲摸鱼 🫧"]),
    (0.3, 5,   ["轻轻翻页 📄", "读写小忙 🧃", "稳稳的 🙂"]),
    (5, 30,    ["读写加速 ⚙️", "缓存热身 🔥", "开始认真 🛠️"]),
    (30, 120,  ["磁盘起飞 🚀", "吞吐拉满 💽", "别打扰我 😵"]),
    (120, 1e9, ["IO 爆表 🚨", "疯狂读写 ☢️", "卡顿预警 ⚠️"]),
]

MOOD_BUCKETS: Buckets = [
    (0, 20,  ["摸鱼中 😴", "静默 🫧", "养生 🌿"]),
    (20, 40, ["巡航 🛫", "小忙 🙂", "不慌 🧘"]),
    (40, 60, ["认真 🛠️", "开工 ⚙️", "稳稳输出 ✅"]),
    (60, 80, ["加速 🚀", "火力全开 💪", "起飞 ✈️"]),
    (80, 101, ["爆肝 🔥", "快冒烟 🥵", "救命 🚨"]),
]


def _pick_bucket(buckets: Buckets, usage: float) -> Tuple[int, List[str]]:
    for i, (lo, hi, texts) in enumerate(buckets):
        if lo <= usage < hi:
            return i, texts
    return len(buckets) - 1, buckets[-1][2]


class PhraseManager:
    """跨档才更新文案：kind -> (bucket_index, phrase)"""
    def __init__(self):
        self.state: Dict[str, Tuple[Optional[int], str]] = {}

    def get(self, kind: str, usage: Optional[float], buckets: Buckets) -> str:
        if usage is None:
            return "数据缺席 🤷"

        idx, texts = _pick_bucket(buckets, float(usage))
        last_idx, last_phrase = self.state.get(kind, (None, ""))

        if last_idx == idx and last_phrase:
            return last_phrase

        phrase = random.choice(texts)
        if phrase == last_phrase and len(texts) > 1:
            alt = [t for t in texts if t != last_phrase]
            phrase = random.choice(alt)

        self.state[kind] = (idx, phrase)
        return phrase


# -------------------------
# Themes
# -------------------------
THEMES: Dict[str, dict] = {
    "mica": {
        "name": "Windows 11 云母感",
        "bg": (18, 18, 22),
        "border": (255, 255, 255, 42),
        "badge": (255, 255, 255, 185),
        "value": (255, 255, 255, 240),
        "shadow": (0, 0, 0, 140),
        "glow": (255, 255, 255, 120),
    },
    "neon": {
        "name": "霓虹",
        "bg": (10, 10, 14),
        "border": (120, 220, 255, 95),
        "badge": (170, 240, 255, 230),
        "value": (255, 255, 255, 246),
        "shadow": (120, 220, 255, 150),
        "glow": (120, 220, 255, 170),
    },
    "cyber": {
        "name": "赛博",
        "bg": (6, 12, 10),
        "border": (80, 255, 200, 95),
        "badge": (120, 255, 220, 235),
        "value": (235, 255, 250, 246),
        "shadow": (80, 255, 200, 140),
        "glow": (80, 255, 200, 170),
    },
}


# -------------------------
# Panel
# -------------------------
class Panel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Panel")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        self.title = QLabel("⚡ Eric")
        self.title.setObjectName("PanelTitle")

        self.line1 = QLabel("--")
        self.line2 = QLabel("--")
        self.line3 = QLabel("--")
        self.line4 = QLabel("--")  # DISK
        self.line5 = QLabel("--")  # NET

        for lb in (self.line1, self.line2, self.line3, self.line4, self.line5):
            lb.setObjectName("PanelLine")
            lb.setWordWrap(False)
            lb.setTextFormat(Qt.PlainText)

        self.mood = QLabel("")
        self.mood.setObjectName("PanelMood")
        self.mood.setAlignment(Qt.AlignCenter)
        self.mood.setVisible(False)

        glow = QGraphicsDropShadowEffect(self.mood)
        glow.setBlurRadius(28)
        glow.setOffset(0, 0)
        glow.setColor(QColor(255, 255, 255, 120))
        self._mood_glow = glow
        self.mood.setGraphicsEffect(self._mood_glow)

        lay.addWidget(self.title)
        lay.addWidget(self.line1)
        lay.addWidget(self.line2)
        lay.addWidget(self.line3)
        lay.addWidget(self.line4)
        lay.addWidget(self.line5)
        lay.addStretch(1)
        lay.addWidget(self.mood)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 5)
        self._shadow = shadow
        self.setGraphicsEffect(self._shadow)

        f = self.mood.font()
        f.setPointSize(11)
        f.setBold(True)
        self.mood.setFont(f)

    def set_shadow_color(self, rgba):
        self._shadow.setColor(QColor(*rgba))

    def set_mood_glow(self, rgba):
        self._mood_glow.setColor(QColor(*rgba))

    def set_lines(self, a: str, b: str, c: str, d: str, e: str):
        self.line1.setText(a)
        self.line2.setText(b)
        self.line3.setText(c)
        self.line4.setText(d)
        self.line5.setText(e)

    def set_collapsed_mode(self, collapsed: bool, vertical_text: str = ""):
        self.title.setVisible(not collapsed)
        self.line1.setVisible(not collapsed)
        self.line2.setVisible(not collapsed)
        self.line3.setVisible(not collapsed)
        self.line4.setVisible(not collapsed)
        self.line5.setVisible(not collapsed)

        self.mood.setVisible(collapsed)
        if collapsed:
            self.mood.setText("\n".join(list(vertical_text)) if vertical_text else "")
        else:
            self.mood.setText("")


# -------------------------
# Overlay
# -------------------------
class Overlay(QWidget):
    EDGE_SAFE_PX = 20

    AUTOHIDE_DELAY_MS = 900
    POLL_MS = 80
    HOVER_PAD_PX = 16

    NET_SAT_MB_S = 50.0

    ANIM_MS = 260
    ANIM_EASE = QEasingCurve.OutBack

    EXPANDED_OPACITY = 1.00
    COLLAPSED_OPACITY = 0.86

    COLLAPSED_MIN_H = 64
    COLLAPSED_MAX_H_RATIO = 0.55

    COLLAPSED_MIN_W = 22
    COLLAPSED_MAX_W = 52

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MonitorOverlay")
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.settings = QSettings("MonitorOverlay", "MonitorOverlay")
        self.theme_key = str(self.settings.value("theme", "mica"))
        self.opacity_pct = int(self.settings.value("opacity", 75))
        self.click_through = bool(self.settings.value("click_through", False, type=bool))
        self.auto_hide = bool(self.settings.value("auto_hide", True, type=bool))

        self.expanded_geo: Optional[QRect] = None
        self.collapsed = False
        self.dock_side: str = "right"

        self._dragging = False
        self._drag_off = QPoint()

        self.lhm = LhmReader()
        self.psr = PsutilReader()
        self.disk_rate = DiskRateReader()
        self.phrases = PhraseManager()

        self.last_cpu: Optional[float] = None
        self.last_gpu: Optional[float] = None
        self.last_ram: Optional[float] = None
        self.last_disk_r: Optional[float] = None
        self.last_disk_w: Optional[float] = None
        self.last_up: Optional[float] = None
        self.last_down: Optional[float] = None

        self._anim_group = QParallelAnimationGroup(self)

        self._geo_anim = QPropertyAnimation(self, b"geometry", self)
        self._geo_anim.setDuration(self.ANIM_MS)
        self._geo_anim.setEasingCurve(self.ANIM_EASE)

        self._opa_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opa_anim.setDuration(self.ANIM_MS)
        self._opa_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._anim_group.addAnimation(self._geo_anim)
        self._anim_group.addAnimation(self._opa_anim)
        self._anim_group.finished.connect(self._on_anim_finished)

        self._anim_target_collapsed: Optional[bool] = None
        self._anim_inflight = False

        self.setWindowOpacity(self.EXPANDED_OPACITY)

        font = QFont()
        font.setPointSize(8)
        self.setFont(font)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self.panel = Panel()
        root.addWidget(self.panel)

        self.apply_theme()
        self.adjustSize()
        self._restore_or_default_pos()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

        self.poll = QTimer(self)
        self.poll.timeout.connect(self._poll_mouse)
        self.poll.start(self.POLL_MS)

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse_if_still_far)

        self._init_tray()
        self._init_hotkeys()

        QTimer.singleShot(60, lambda: set_click_through(self, self.click_through))
        QTimer.singleShot(300, self._maybe_autocollapse_on_start)

    def _is_effectively_collapsed(self) -> bool:
        if self._anim_inflight and self._anim_target_collapsed is not None:
            return bool(self._anim_target_collapsed)
        return bool(self.collapsed)

    def _on_anim_finished(self):
        self._anim_inflight = False
        if self._anim_target_collapsed is True:
            self.collapsed = True
            self.setWindowOpacity(self.COLLAPSED_OPACITY)
        elif self._anim_target_collapsed is False:
            self.collapsed = False
            self.setWindowOpacity(self.EXPANDED_OPACITY)
        self._anim_target_collapsed = None

    def _start_anim(self, start_geo: QRect, end_geo: QRect, start_op: float, end_op: float, target_collapsed: bool):
        if self._anim_inflight:
            try:
                self._anim_group.stop()
            except Exception:
                pass
        self._anim_inflight = True
        self._anim_target_collapsed = target_collapsed

        self._geo_anim.setStartValue(start_geo)
        self._geo_anim.setEndValue(end_geo)
        self._opa_anim.setStartValue(float(start_op))
        self._opa_anim.setEndValue(float(end_op))
        self._anim_group.start()

    def _screen(self):
        return QApplication.primaryScreen().availableGeometry()

    def _decide_nearest_side(self, rect: QRect) -> str:
        s = self._screen()
        return "left" if rect.center().x() < ((s.left() + s.right()) // 2) else "right"

    def _calc_collapsed_height_for_text(self, mood_text: str) -> int:
        s = self._screen()
        max_h = int(s.height() * self.COLLAPSED_MAX_H_RATIO)

        n = max(1, len(mood_text))
        fm = QFontMetrics(self.panel.mood.font())
        line_h = fm.height()

        pad = 30
        h = pad + int(n * line_h * 1.05)
        return clamp(h, self.COLLAPSED_MIN_H, max_h)

    def _calc_collapsed_width_for_text(self, mood_text: str) -> int:
        fm = QFontMetrics(self.panel.mood.font())
        max_char_w = 0
        for ch in (mood_text or " "):
            r = fm.boundingRect(ch)
            max_char_w = max(max_char_w, r.width())
        w = int(max_char_w + 18)
        return clamp(w, self.COLLAPSED_MIN_W, self.COLLAPSED_MAX_W)

    def _collapsed_rect_for(self, expanded: QRect, side: str, collapsed_w: int, collapsed_h: int) -> QRect:
        s = self._screen()
        y = clamp(expanded.y(), s.top() + self.EDGE_SAFE_PX, s.bottom() - collapsed_h - self.EDGE_SAFE_PX)

        if side == "left":
            x = s.left() + self.EDGE_SAFE_PX
        else:
            x = s.right() - collapsed_w + 1 - self.EDGE_SAFE_PX

        return QRect(x, y, collapsed_w, collapsed_h)

    def _hover_region_rect(self) -> QRect:
        g = self.geometry()
        pad = self.HOVER_PAD_PX
        return QRect(g.x() - pad, g.y() - pad, g.width() + 2 * pad, g.height() + 2 * pad)

    def _cursor_in_rect(self, r: QRect, pad: int = 0) -> bool:
        p = QCursor.pos()
        return r.adjusted(-pad, -pad, pad, pad).contains(p)

    def _restore_or_default_pos(self):
        screen = self._screen()
        pos = self.settings.value("pos", None)
        if pos:
            try:
                x, y = map(int, str(pos).split(","))
                self.move(x, y)
                g = self.geometry()
                nx = clamp(g.x(), screen.left(), screen.right() - g.width())
                ny = clamp(g.y(), screen.top(), screen.bottom() - g.height())
                self.move(nx, ny)
                return
            except Exception:
                pass
        self.move(screen.right() - self.width() - 10, screen.top() + 10)

    def _persist_pos(self):
        p = self.pos()
        self.settings.setValue("pos", f"{p.x()},{p.y()}")

    def apply_theme(self):
        t = THEMES.get(self.theme_key, THEMES["mica"])
        alpha = int(255 * clamp(self.opacity_pct, 30, 95) / 100)

        self.setStyleSheet(f"""
            #Panel {{
                background: rgba({t['bg'][0]},{t['bg'][1]},{t['bg'][2]},{alpha});
                border: 1px solid rgba({t['border'][0]},{t['border'][1]},{t['border'][2]},{t['border'][3]});
                border-radius: 16px;
            }}
            #PanelTitle {{
                color: rgba({t['badge'][0]},{t['badge'][1]},{t['badge'][2]},{t['badge'][3]});
                font-weight: 950;
                letter-spacing: 0.4px;
            }}
            #PanelLine {{
                color: rgba({t['value'][0]},{t['value'][1]},{t['value'][2]},{t['value'][3]});
                font-weight: 850;
            }}
            #PanelMood {{
                color: rgba({t['value'][0]},{t['value'][1]},{t['value'][2]},{t['value'][3]});
                font-weight: 950;
            }}
        """)

        self.panel.set_shadow_color((*t["shadow"][:3], t["shadow"][3]))
        self.panel.set_mood_glow((*t["glow"][:3], t["glow"][3]))

        self.settings.setValue("theme", self.theme_key)
        self.settings.setValue("opacity", int(self.opacity_pct))

    def mousePressEvent(self, e):
        if self.click_through or self._anim_inflight:
            return
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if self.collapsed:
                self.expand_animated()

    def mouseMoveEvent(self, e):
        if self.click_through or self._anim_inflight:
            return
        if self._dragging:
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self._persist_pos()
            self._schedule_collapse_if_far()

    def _schedule_collapse_if_far(self):
        if (not self.auto_hide) or self._anim_inflight:
            return
        if self._cursor_in_rect(self.geometry(), pad=6):
            self._collapse_timer.stop()
            return
        if not self._collapse_timer.isActive():
            self._collapse_timer.start(self.AUTOHIDE_DELAY_MS)

    def _collapse_if_still_far(self):
        if (not self.auto_hide) or self._anim_inflight:
            return
        if self._cursor_in_rect(self.geometry(), pad=6):
            self._schedule_collapse_if_far()
            return
        self.collapse_to_edge_animated()

    def _poll_mouse(self):
        if (not self.auto_hide) or self._anim_inflight:
            return

        if self.collapsed:
            if self._hover_region_rect().contains(QCursor.pos()):
                self.expand_animated()
            return

        if not self._cursor_in_rect(self.geometry(), pad=6):
            if not self._collapse_timer.isActive():
                self._collapse_timer.start(self.AUTOHIDE_DELAY_MS)
        else:
            self._collapse_timer.stop()

    def _compute_mood_phrase(self) -> str:
        cpu = self.last_cpu if self.last_cpu is not None else 0.0
        gpu = self.last_gpu if self.last_gpu is not None else 0.0
        ram = self.last_ram if self.last_ram is not None else 0.0

        dr = self.last_disk_r if self.last_disk_r is not None else 0.0
        dw = self.last_disk_w if self.last_disk_w is not None else 0.0
        disk_mb_s = dr + dw
        disk_norm = min(100.0, (disk_mb_s / 200.0) * 100.0)  # 200MB/s ~= 100%

        up = self.last_up if self.last_up is not None else 0.0
        down = self.last_down if self.last_down is not None else 0.0
        net = up + down
        net_norm = min(100.0, (net / max(1e-6, self.NET_SAT_MB_S)) * 100.0)

        score = 0.28 * cpu + 0.28 * gpu + 0.22 * ram + 0.10 * disk_norm + 0.12 * net_norm
        score = max(0.0, min(100.0, score))
        return self.phrases.get("mood", score, MOOD_BUCKETS)

    def collapse_to_edge_animated(self):
        if self.collapsed:
            return

        self.expanded_geo = self.geometry()
        self.dock_side = self._decide_nearest_side(self.expanded_geo)

        mood = self._compute_mood_phrase()

        self._anim_target_collapsed = True
        self.collapsed = True
        self.panel.set_collapsed_mode(True, mood)

        collapsed_h = self._calc_collapsed_height_for_text(mood)
        collapsed_w = self._calc_collapsed_width_for_text(mood)

        start_geo = self.geometry()
        end_geo = self._collapsed_rect_for(self.expanded_geo, self.dock_side, collapsed_w, collapsed_h)

        start_op = float(self.windowOpacity() if self.windowOpacity() > 0 else self.EXPANDED_OPACITY)
        end_op = float(self.COLLAPSED_OPACITY)

        self._start_anim(start_geo, end_geo, start_op, end_op, target_collapsed=True)
        self._persist_pos()

    def expand_animated(self):
        if not self.collapsed:
            return

        if not self.expanded_geo:
            s = self._screen()
            self.expanded_geo = QRect(s.right() - 270, s.top() + 40, 270, 178)

        self._anim_target_collapsed = False
        self.collapsed = False
        self.panel.set_collapsed_mode(False)

        s = self._screen()
        g = self.expanded_geo
        end_geo = QRect(
            clamp(g.x(), s.left(), s.right() - g.width()),
            clamp(g.y(), s.top(), s.bottom() - g.height()),
            g.width(),
            g.height()
        )

        start_geo = self.geometry()
        start_op = float(self.windowOpacity() if self.windowOpacity() > 0 else self.COLLAPSED_OPACITY)
        end_op = float(self.EXPANDED_OPACITY)

        self._start_anim(start_geo, end_geo, start_op, end_op, target_collapsed=False)
        self._persist_pos()

    def _maybe_autocollapse_on_start(self):
        self._schedule_collapse_if_far()

    def _tray_icon(self) -> QIcon:
        icon = QIcon.fromTheme("computer")
        if icon.isNull():
            icon = QIcon.fromTheme("applications-system")
        if icon.isNull():
            icon = self.style().standardIcon(self.style().SP_TitleBarMenuButton)
        return icon

    def _init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._tray_icon())
        self.tray.setToolTip("MonitorOverlay")

        menu = QMenu()

        self.act_show = QAction("显示/隐藏 (Ctrl+Alt+H)", self)
        self.act_show.triggered.connect(self.toggle_visible)
        menu.addAction(self.act_show)

        self.act_click = QAction("点击穿透 (Ctrl+Alt+T)", self, checkable=True)
        self.act_click.triggered.connect(self.toggle_click_through)
        menu.addAction(self.act_click)

        self.act_autohide = QAction("自动隐藏/呼出", self, checkable=True)
        self.act_autohide.triggered.connect(self.toggle_autohide)
        menu.addAction(self.act_autohide)

        menu.addSeparator()

        theme_menu = QMenu("主题", menu)
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)

        self.act_theme_mica = QAction(THEMES["mica"]["name"], self, checkable=True)
        self.act_theme_neon = QAction(THEMES["neon"]["name"], self, checkable=True)
        self.act_theme_cyber = QAction(THEMES["cyber"]["name"], self, checkable=True)

        for act in (self.act_theme_mica, self.act_theme_neon, self.act_theme_cyber):
            self.theme_group.addAction(act)
            theme_menu.addAction(act)

        self.act_theme_mica.triggered.connect(lambda: self.set_theme("mica"))
        self.act_theme_neon.triggered.connect(lambda: self.set_theme("neon"))
        self.act_theme_cyber.triggered.connect(lambda: self.set_theme("cyber"))
        menu.addMenu(theme_menu)

        opacity_menu = QMenu("背景透明度", menu)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(30, 95)
        slider.setValue(clamp(int(self.opacity_pct), 30, 95))
        slider.setFixedWidth(180)
        slider.valueChanged.connect(self.set_opacity_pct)

        slider_widget = QWidget()
        lay = QHBoxLayout(slider_widget)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(slider)

        wa = QWidgetAction(opacity_menu)
        wa.setDefaultWidget(slider_widget)
        opacity_menu.addAction(wa)
        menu.addMenu(opacity_menu)

        menu.addSeparator()

        self.act_startup = QAction("开机自启", self, checkable=True)
        self.act_startup.triggered.connect(self.toggle_startup)
        menu.addAction(self.act_startup)

        menu.addSeparator()

        quit_act = QAction("退出", self)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self._sync_tray_checks()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_visible()

    def _sync_tray_checks(self):
        try:
            self.act_click.setChecked(self.click_through)
        except Exception:
            pass
        try:
            self.act_autohide.setChecked(self.auto_hide)
        except Exception:
            pass
        try:
            self.act_startup.setChecked(is_startup_enabled())
        except Exception:
            pass
        try:
            {"mica": self.act_theme_mica, "neon": self.act_theme_neon, "cyber": self.act_theme_cyber}[self.theme_key].setChecked(True)
        except Exception:
            pass

    def _init_hotkeys(self):
        self.hk_toggle_click = QShortcut(QKeySequence("Ctrl+Alt+T"), self)
        self.hk_toggle_click.activated.connect(self.toggle_click_through)

        self.hk_toggle_show = QShortcut(QKeySequence("Ctrl+Alt+H"), self)
        self.hk_toggle_show.activated.connect(self.toggle_visible)

    def toggle_visible(self):
        self.setVisible(not self.isVisible())

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self.settings.setValue("click_through", bool(self.click_through))
        set_click_through(self, self.click_through)
        self._sync_tray_checks()

    def toggle_autohide(self):
        self.auto_hide = not self.auto_hide
        self.settings.setValue("auto_hide", bool(self.auto_hide))
        self._sync_tray_checks()
        if not self.auto_hide and self._is_effectively_collapsed():
            self.expand_animated()
        if self.auto_hide:
            self._schedule_collapse_if_far()

    def toggle_startup(self):
        set_startup_enabled(not is_startup_enabled())
        self._sync_tray_checks()

    def set_theme(self, key: str):
        if key not in THEMES:
            return
        self.theme_key = key
        self.apply_theme()
        self._sync_tray_checks()

    def set_opacity_pct(self, v: int):
        self.opacity_pct = clamp(int(v), 30, 95)
        self.apply_theme()

    # ---------- refresh ----------
    def refresh(self):
        lhm = self.lhm.read()
        psu = self.psr.read()

        cpu_usage = psu.get("cpu_usage")
        if cpu_usage is None:
            try:
                cpu_usage = float(psutil.cpu_percent(interval=None))
            except Exception:
                cpu_usage = 0.0

        gpu_usage = lhm.get("gpu_load")

        ram_usage = psu.get("ram_usage")
        if ram_usage is None:
            try:
                ram_usage = float(psutil.virtual_memory().percent)
            except Exception:
                ram_usage = None

        disk_r, disk_w = self.disk_rate.read()

        up = psu.get("net_up_mb_s")
        down = psu.get("net_down_mb_s")

        self.last_cpu = cpu_usage
        self.last_gpu = gpu_usage
        self.last_ram = ram_usage
        self.last_disk_r = disk_r
        self.last_disk_w = disk_w
        self.last_up = up
        self.last_down = down

        cpu_txt = self.phrases.get("cpu", cpu_usage, CPU_BUCKETS)
        gpu_txt = self.phrases.get("gpu", gpu_usage, GPU_BUCKETS)
        ram_txt = self.phrases.get("ram", ram_usage, RAM_BUCKETS)

        # ✅ DISK 趣味文案（跨档稳定）：基于读写合计 MB/s
        disk_total = None if (disk_r is None or disk_w is None) else float(disk_r + disk_w)
        disk_txt = self.phrases.get("disk_io", disk_total, DISK_IO_BUCKETS)

        # ✅ DISK：超短读/写速度（MB/s），读/写
        disk_str = f"{fmt_rate_short(disk_r)}/{fmt_rate_short(disk_w)}"

        line1 = f"CPU   {fmt_pct(cpu_usage)}  {cpu_txt}"
        line2 = f"GPU   {fmt_pct(gpu_usage)}  {gpu_txt}"
        line3 = f"RAM   {fmt_pct(ram_usage)}  {ram_txt}"
        line4 = f"DISK  {disk_str}  {disk_txt}"
        line5 = f"网络  ↑{fmt_rate_mb_s(up)}  ↓{fmt_rate_mb_s(down)}"

        if not self._is_effectively_collapsed():
            self.panel.set_lines(line1, line2, line3, line4, line5)
        else:
            self.panel.set_collapsed_mode(True, self._compute_mood_phrase())


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = Overlay()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
