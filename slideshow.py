#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
slideshow.py - image + video slideshow for Linux (Wayland and X11).

USAGE
    slideshow.py [FOLDER_OR_FILE] [options]

    With no path a folder chooser opens. A file path opens that file's folder
    and starts on that file. Sub-folders are always read recursively.

    -i, --interval SEC        seconds per image while the slideshow is playing (5)
    --play                    start playing instead of paused
    --shuffle                 force shuffle      (default: shuffle only if the folder
    --no-shuffle              force natural sort  has media in sub-folders)
    --no-rotate               start with rotate-to-fit off
    --windowed                do not start fullscreen
    --include-hidden          include dot-files and dot-folders
    --icon-size SIZE          set toolbar icon button size in pixels (default: 64)
    --icon-padding PAD        set icon button padding in pixels (0 and negative values
                              are allowed; default: automatic, 1/5 of the icon size)
    --controls [visible|hidden] initial state of on-screen controls (default: visible)
    --controls-opacity OPACITY controls background opacity from 0.0 to 1.0 (default: 0.0)
    -a1, --action-1 CMD       Command for key 1 (supports {path}, which is shell-quoted
                              automatically - do not add quotes around it)
    ...
    -a0, --action-10 CMD      Command for key 0 (supports {path}, as above)

KEYS / MOUSE
    Left / Right        previous / next
    Up / Down / wheel   zoom in / out
    Home / End          first / last
    Space               next
    P                   play / pause slideshow
    V                   video play / pause
    M                   mute / unmute
    PgUp / PgDn         volume up / down
    S                   toggle shuffle / sorted
    A                   auto-rotate to fit on/off
    1~0                 run custom shell commands (configured via CLI or COMMAND_MAPPINGS)
    Delete              move current file to trash
    O / Ctrl+O          open folder
    F / F11             fullscreen
    Esc / Q             quit
    H                   toggle keep controls visible (pin)
    I                   toggle file info overlay
    Drag                cycles through items (drag left = next); when zoomed in,
                        drag pans instead. Top bar: click = jump, drag = scrub.
                        Video seek bar: bottom edge of the video (rotates with it).

DEPENDENCIES
    PySide6 (Qt 6.5+, with the Qt Multimedia FFmpeg backend - the pip wheel has it)
    Optional: Pillow (+ pillow-heif / pillow-jxl-plugin) as a fallback decoder for
    HEIC / AVIF / JPEG XL when Qt has no plugin for them.
    Arch:   pacman -S pyside6 qt6-multimedia-ffmpeg qt6-wayland qt6-svg \
                       qt6-imageformats python-pillow
"""
from __future__ import annotations

import argparse
import math
import os
import random
import re
import shlex
import subprocess
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import (QEvent, QFile, QPointF, QProcess, QRectF, QSize, Qt,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage,
                           QImageReader, QMovie, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFileDialog,
                               QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QMessageBox, QSizePolicy, QSlider,
                               QVBoxLayout, QWidget)

# Multimedia, SVG and Pillow are all optional: without them the app loses video / SVG /
# HEIC-AVIF-JXL fallback support instead of failing to start.
try:
    from PySide6.QtMultimedia import (QAudioOutput, QMediaDevices, QMediaPlayer,
                                      QVideoSink)
    HAVE_MULTIMEDIA = True
except Exception:  # pragma: no cover - videos are then reported as unplayable
    HAVE_MULTIMEDIA = False

try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover
    QSvgRenderer = None

# Optional fallback decoders (HEIC / AVIF / JXL / odd formats Qt has no plugin for)
try:
    from PIL import Image as _PILImage
    from PIL import ImageOps as _PILImageOps
    # Disable Pillow's decompression-bomb guard; very large photos are expected here.
    _PILImage.MAX_IMAGE_PIXELS = None
    HAVE_PIL = True
    # Each plugin is optional; if installed it teaches Pillow HEIC / AVIF / JPEG XL.
    for _mod, _fn in (("pillow_heif", "register_heif_opener"),
                      ("pillow_avif", None), ("pillow_jxl", None)):
        try:
            _m = __import__(_mod)
            if _fn:
                getattr(_m, _fn)()
        except Exception:
            pass
except Exception:  # pragma: no cover
    HAVE_PIL = False

try:
    QImageReader.setAllocationLimit(0)  # Qt 6 refuses images >256 MB by default
except Exception:
    pass

# Matched case-insensitively by extension only (see media_kind). Whether a file actually
# decodes depends on the installed Qt / Pillow plugins.
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".gif", ".webp", ".avif",
    ".avifs", ".heic", ".heif", ".jxl", ".bmp", ".dib", ".tif", ".tiff", ".ico",
    ".svg", ".jp2", ".j2k", ".jpx", ".qoi", ".tga", ".pbm", ".pgm", ".ppm",
}
VIDEO_EXTS = {
    ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv", ".f4v",
    ".mpg", ".mpeg", ".mpe", ".m2ts", ".mts", ".3gp", ".3g2", ".ogv", ".vob",
    ".divx", ".asf", ".mxf",
}

# UI colours: accent is used for progress / seek bars and checked buttons.
ACCENT = QColor(108, 184, 255)
WHITE = QColor(255, 255, 255)

# --------------------------------------------------------------------------- #
# USER CONFIGURATION: Default Custom Command Mappings for Keys 1 ~ 0
# Can be overridden via command-line flags (-a1 ... -a0 / --action-1 ... --action-10)
# --------------------------------------------------------------------------- #
# Empty string = key unassigned. Commands run through the shell and block until they
# finish; {path} is replaced by the current file, shell-quoted automatically (spaces,
# quotes and $ are safe) - do NOT add your own quotes, e.g. 'mv {path} ~/keep/'. The
# template goes through str.format, so literal braces must be doubled ({{ }}). While
# unmapped, key 5 deletes the file and key 0 resets zoom.
COMMAND_MAPPINGS = {
    Qt.Key_1: "",
    Qt.Key_2: "",
    Qt.Key_3: "",
    Qt.Key_4: "",
    Qt.Key_5: "",
    Qt.Key_6: "",
    Qt.Key_7: "",
    Qt.Key_8: "",
    Qt.Key_9: "",
    Qt.Key_0: "",
}

# --------------------------------------------------------------------------- #
# USER CONFIGURATION: Control Icon Order
# Re-order, add, or remove elements in this list to change the toolbar layout.
# Use "sep" to insert a vertical separator line between groups.
# --------------------------------------------------------------------------- #
# Names: prev next play shuffle rotate info pin trash folder quit; 'sep' = divider.
# 'play' is defined but currently disabled (P still toggles the slideshow).
CONTROL_ORDER = [
    "prev", "next", "sep", #"play",
    "shuffle", "rotate", "info", "pin",
    "trash", "folder", "quit"
]


# -> 'image' | 'video' | None, decided purely by file extension.
def media_kind(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


# --------------------------------------------------------------------------- #
# file discovery + natural sort
# --------------------------------------------------------------------------- #
# Capturing group so re.split keeps the digit runs: 'img10.jpg' -> ['img', '10',
# '.jpg'].
_NUM = re.compile(r"(\d+)")


def natural_key(s: str):
    """'img2' < 'img10' (case-insensitive). Uniform tuples so str/int never mix."""
    return [(0, int(t), "") if t.isdecimal() else (1, 0, t.casefold())
            for t in _NUM.split(s) if t]


# Sort key: folder path components first, then the filename, each naturally sorted.
def sort_key(path: str, root: str):
    rel = os.path.relpath(path, root)
    d, f = os.path.split(rel)
    dparts = tuple(natural_key(x) for x in d.split(os.sep)) if d else ()
    return (dparts, natural_key(f))      # files of a folder before its sub-folders


def scan_media(root: str, include_hidden: bool = False):
    """-> (naturally sorted media files, True if any sub-folder holds media)."""
    files, sub_media, seen = [], False, set()
    # Recursive walk; symlinks are followed but guarded against cycles via realpath.
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:                      # symlink loop
            dirnames[:] = []
            continue
        seen.add(real)
        # Prune hidden folders in place so os.walk never descends into them.
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        found = False
        for name in filenames:
            if not include_hidden and name.startswith("."):
                continue
            if media_kind(name):
                files.append(os.path.join(dirpath, name))
                found = True
        # Media found below the root (not directly in it) is what turns on default
        # shuffling.
        if found and os.path.normpath(dirpath) != os.path.normpath(root):
            sub_media = True
    # Natural sort (1, 2, 10) - this is also the 'unshuffled' order.
    files.sort(key=lambda p: sort_key(p, root))
    return files, sub_media


# Same rules as scan_media()'s sub_media flag, but stops at the first hit instead of
# listing the whole tree. Used to decide whether a forced --shuffle makes sense.
def folder_has_sub_media(root: str, include_hidden: bool = False) -> bool:
    seen = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if os.path.normpath(dirpath) == os.path.normpath(root):
            continue
        if any(media_kind(n) and (include_hidden or not n.startswith("."))
               for n in filenames):
            return True
    return False


# --------------------------------------------------------------------------- #
# image decoding (runs in worker threads)
# --------------------------------------------------------------------------- #
# Result passed from decode thread to GUI thread. full = original pixels; reduced =
# screen-sized copy used for painting when zoomed out; error = message if decoding
# failed.
class ImgData:
    __slots__ = ("full", "reduced", "animated", "error")

    def __init__(self, full=None, reduced=None, animated=False, error=None):
        self.full, self.reduced, self.animated, self.error = full, reduced, animated, error

    # Approximate bytes held, used for the cache budget.
    @property
    def nbytes(self):
        n = 0
        for im in (self.full, self.reduced):
            if im is not None:
                n += im.sizeInBytes()
        return n


# Fallback for formats Qt can't read (HEIC, AVIF, JXL...). exif_transpose applies EXIF
# rotation like Qt's setAutoTransform; .copy() detaches the QImage from the temporary
# bytes buffer.
def _pil_to_qimage(path: str) -> QImage:
    if not HAVE_PIL:
        raise RuntimeError("Unsupported image format (install Pillow, pillow-heif "
                           "or pillow-jxl-plugin for HEIC/AVIF/JXL)")
    with _PILImage.open(path) as im:
        im.load()
        im = _PILImageOps.exif_transpose(im).convert("RGBA")
        w, h = im.size
        return QImage(im.tobytes("raw", "RGBA"), w, h, w * 4,
                      QImage.Format.Format_RGBA8888).copy()


# Runs in worker threads (QImage is thread-safe; no widgets or QPixmaps here). Order:
# SVG rasterised -> Qt image reader (EXIF-rotated) -> Pillow fallback.
def decode_image(path: str, target_dim: int) -> ImgData:
    img, animated = QImage(), False
    if path.lower().endswith(".svg") and QSvgRenderer is not None:
        r = QSvgRenderer(path)
        if r.isValid():
            sz = r.defaultSize()
            if sz.isEmpty():
                sz = QSize(1024, 1024)
            # Rasterise the SVG so its longest side equals target_dim.
            k = target_dim / max(sz.width(), sz.height())
            sz = QSize(max(1, round(sz.width() * k)), max(1, round(sz.height() * k)))
            img = QImage(sz, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0)
            p = QPainter(img)
            r.render(p)
            p.end()
    if img.isNull():
        reader = QImageReader(path)
        reader.setAutoTransform(True)         # EXIF orientation
        reader.setAutoDetectImageFormat(True)
        if reader.canRead():
            # >1 frame = animated GIF/WebP. What is decoded here is only frame 1; the
            # Viewer starts a QMovie for playback.
            animated = reader.imageCount() > 1
            img = reader.read()
    if img.isNull():
        img, animated = _pil_to_qimage(path), False
    # Normalise to the pixel format Qt paints fastest (premultiplied if it has alpha).
    fmt = (QImage.Format.Format_ARGB32_Premultiplied if img.hasAlphaChannel()
           else QImage.Format.Format_RGB32)
    if img.format() != fmt:
        img = img.convertToFormat(fmt)
    # Keep a screen-sized downscale when the image is far larger than needed.
    reduced = None
    if max(img.width(), img.height()) > target_dim * 1.25:
        reduced = img.scaled(target_dim, target_dim, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return ImgData(img, reduced, animated)


# --------------------------------------------------------------------------- #
# audio device detection
# --------------------------------------------------------------------------- #
# NB: Qt's device enumeration can crash when no sound server exists, so the Viewer only
# calls this in-process after a child-process probe succeeded.
def pick_audio_device():
    """First usable real output (default preferred). None => no sound available
    (VNC without sound, no PipeWire/Pulse, only a null/dummy sink...)."""
    if not HAVE_MULTIMEDIA:
        return None

    # Reject PipeWire/Pulse null sinks, which exist even with no real audio (VNC,
    # headless).
    def usable(d):
        ident = bytes(d.id()).decode(errors="ignore").lower()
        desc = d.description().lower()
        return not (d.isNull() or "auto_null" in ident or "dummy output" in desc)
    try:
        default = QMediaDevices.defaultAudioOutput()
        if usable(default):
            return default
        for d in QMediaDevices.audioOutputs():
            if usable(d):
                return d
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# vector icons (no dependency on icon themes / emoji fonts)
# --------------------------------------------------------------------------- #
# Draws icon `name` with QPainter primitives on a 0..1 grid inside rect r (P() maps grid
# -> pixels), so no icon theme or emoji font is needed. With with_shadow, each shape is
# drawn first as a dark offset copy, then in colour.
def draw_icon(p: QPainter, name: str, r: QRectF, col: QColor, with_shadow: bool = True):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # If shadow is requested, draw the shape once in semi-transparent black offset by (1, 1)
    passes = [(QPointF(1.0, 1.0), QColor(0, 0, 0, 180)), (QPointF(0.0, 0.0), col)] if with_shadow else [(QPointF(0.0, 0.0), col)]

    for offset, cur_col in passes:
        r_off = r.translated(offset)

        def P(x, y):
            return QPointF(r_off.left() + x * r_off.width(), r_off.top() + y * r_off.height())

        def poly(*pts):
            return QPolygonF([P(*q) for q in pts])

        # Stroke width scales with icon size.
        pen = QPen(cur_col, max(1.7, r_off.width() * 0.085))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        # fill(): solid shapes. stroke(): outlines. Icons use one or both.
        fill = lambda: (p.setPen(Qt.PenStyle.NoPen), p.setBrush(cur_col))
        stroke = lambda: (p.setPen(pen), p.setBrush(Qt.BrushStyle.NoBrush))

        if name == "prev":
            fill()
            p.drawRect(QRectF(P(.2, .22), P(.29, .78)))
            p.drawPolygon(poly((.8, .2), (.8, .8), (.36, .5)))
        elif name == "next":
            fill()
            p.drawRect(QRectF(P(.71, .22), P(.8, .78)))
            p.drawPolygon(poly((.2, .2), (.2, .8), (.64, .5)))
        elif name == "play":
            fill()
            p.drawPolygon(poly((.3, .2), (.3, .8), (.8, .5)))
        elif name == "pause":
            fill()
            p.drawRoundedRect(QRectF(P(.27, .22), P(.43, .78)), 1.5, 1.5)
            p.drawRoundedRect(QRectF(P(.57, .22), P(.73, .78)), 1.5, 1.5)
        elif name == "trash":
            stroke()
            p.drawLine(P(.2, .3), P(.8, .3))
            p.drawPolyline(poly((.4, .3), (.4, .2), (.6, .2), (.6, .3)))
            body = QPainterPath()
            body.moveTo(P(.27, .38)); body.lineTo(P(.32, .82))
            body.lineTo(P(.68, .82)); body.lineTo(P(.73, .38))
            p.drawPath(body)
            p.drawLine(P(.43, .47), P(.44, .73))
            p.drawLine(P(.57, .47), P(.56, .73))
        elif name == "shuffle":
            stroke()
            p.drawPolyline(poly((.14, .32), (.36, .32), (.6, .68), (.7, .68)))
            p.drawPolyline(poly((.14, .68), (.36, .68), (.6, .32), (.7, .32)))
            fill()
            p.drawPolygon(poly((.7, .56), (.7, .8), (.88, .68)))
            p.drawPolygon(poly((.7, .2), (.7, .44), (.88, .32)))
        elif name == "rotate":
            stroke()
            rect = QRectF(P(.22, .22), P(.78, .78))
            path = QPainterPath()
            path.arcMoveTo(rect, 40)
            path.arcTo(rect, 40, 270)
            p.drawPath(path)
            fill()
            p.drawPolygon(poly((.787, .6245), (.744, .791), (.616, .638)))
        elif name == "folder":
            stroke()
            path = QPainterPath()
            path.moveTo(P(.14, .3)); path.lineTo(P(.4, .3)); path.lineTo(P(.48, .4))
            path.lineTo(P(.86, .4)); path.lineTo(P(.86, .78)); path.lineTo(P(.14, .78))
            path.closeSubpath()
            p.drawPath(path)
        elif name == "info":
            stroke()
            p.drawEllipse(P(.5, .5), .32, .32)
            fill()
            p.drawRect(QRectF(P(.46, .36), P(.54, .42)))
            p.drawRect(QRectF(P(.46, .48), P(.54, .72)))
        elif name == "pin":
            stroke()
            p.drawLine(P(.5, .2), P(.5, .55))
            p.drawEllipse(P(.5, .35), .18, .18)
            fill()
            p.drawPolygon(poly((.38, .55), (.62, .55), (.55, .82), (.45, .82)))
        elif name == "quit":
            stroke()
            p.drawLine(P(.28, .28), P(.72, .72))
            p.drawLine(P(.72, .28), P(.28, .72))
        elif name in ("speaker", "speaker_muted"):
            fill()
            p.drawPolygon(poly((.12, .4), (.28, .4), (.46, .24), (.46, .76), (.28, .6), (.12, .6)))
            stroke()
            if name == "speaker":
                for rad in (.17, .3):
                    rr = QRectF(P(.46 - rad, .5 - rad), P(.46 + rad, .5 + rad))
                    path = QPainterPath()
                    path.arcMoveTo(rr, -40)
                    path.arcTo(rr, -40, 80)
                    p.drawPath(path)
            else:
                p.drawLine(P(.6, .38), P(.84, .62))
                p.drawLine(P(.84, .38), P(.6, .62))
    p.restore()


# Custom-painted button. padding=None = automatic (20% of size, min 4px). An explicit
# padding is used as-is: 0 gives a square hover highlight, negative values make the icon
# larger than the button (clipped at the button edge). Checkable buttons are drawn in
# the accent colour while on.
class IconButton(QAbstractButton):
    def __init__(self, icon: str, tip: str, size: int = 40, padding: int | None = None, parent=None, checkable=False):
        super().__init__(parent)
        self.icon_name = icon
        self.icon_size = size
        self.custom_padding = padding
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # keep arrow keys on the viewer
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_icon(self, name: str):
        if name != self.icon_name:
            self.icon_name = name
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = 90 if self.isDown() else 45 if self.underMouse() else 0
        pad = self.custom_padding if self.custom_padding is not None else max(4, int(self.icon_size * 0.2))

        if a:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, a))
            if pad <= 0:
                p.drawRect(QRectF(self.rect()))
            else:
                p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 9, 9)

        col = ACCENT if self.isChecked() else QColor(255, 255, 255, 235)
        draw_icon(p, self.icon_name, QRectF(self.rect()).adjusted(pad, pad, -pad, -pad), col, with_shadow=True)


# Thin vertical divider between button groups.
def _sep(height: int = 26):
    f = QFrame()
    f.setFixedSize(1, height)
    f.setStyleSheet("background: rgba(255,255,255,60);")
    return f


# Soft shadow keeps labels readable over bright images now that the panel background can
# be fully transparent (default opacity 0).
def _apply_drop_shadow(widget: QWidget, radius: float = 6.0, offset_y: float = 1.0):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(radius)
    effect.setColor(QColor(0, 0, 0, 220))
    effect.setOffset(1.0, offset_y)
    widget.setGraphicsEffect(effect)


class Panel(QWidget):
    """Bottom control panel. The video group only shows while a video is current."""

    def __init__(self, parent, icon_size: int = 40, icon_padding: int | None = None, opacity: float = 0.8):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Panel background opacity 0..1 -> 0..255.
        alpha = max(0, min(255, int(opacity * 255)))

        self.setStyleSheet(f"""
            #panel {{ background: rgba(18,18,18,{alpha}); border-radius: 14px; }}
            QLabel {{ color: rgba(255,255,255,235); }}
            QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,70); border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: #6cb8ff; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: white; width: 12px; margin: -5px 0; border-radius: 6px; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 8)
        outer.setSpacing(2)
        self.info = QLabel("")
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Ignored: a long file path must not stretch the panel; the text is elided
        # instead (_elide).
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        f = QFont(self.info.font())
        f.setPointSizeF(max(8.0, f.pointSizeF() * 0.9))
        self.info.setFont(f)
        _apply_drop_shadow(self.info)
        outer.addWidget(self.info)
        self._info_text = ""

        row = QHBoxLayout()
        row.setSpacing(4)
        outer.addLayout(row)

        self.b_prev = IconButton("prev", "Previous  (Left)", icon_size, icon_padding)
        self.b_play = IconButton("play", "Play / pause slideshow  (Space)", icon_size, icon_padding)
        self.b_next = IconButton("next", "Next  (Right)", icon_size, icon_padding)
        self.b_shuffle = IconButton("shuffle", "Toggle shuffle / sorted mode  (S)", icon_size, icon_padding, checkable=True)
        self.b_rot = IconButton("rotate", "Rotate to best fit  (A)", icon_size, icon_padding, checkable=True)
        self.b_info = IconButton("info", "Toggle file info  (I)", icon_size, icon_padding, checkable=True)
        self.b_pin = IconButton("pin", "Keep controls visible / pin  (H)", icon_size, icon_padding, checkable=True)
        self.b_del = IconButton("trash", "Delete current file  (Delete)", icon_size, icon_padding)
        self.b_folder = IconButton("folder", "Open folder in a new slideshow  (O)", icon_size, icon_padding)
        self.b_quit = IconButton("quit", "Quit slideshow  (Q)", icon_size, icon_padding)

        sep_h = max(16, int(icon_size * 0.65))

        # name -> widget lookup for CONTROL_ORDER. Widgets not listed there are created
        # but never added to the layout, so they never appear.
        widgets_map = {
            "prev": self.b_prev,
            "play": self.b_play,
            "next": self.b_next,
            "shuffle": self.b_shuffle,
            "rotate": self.b_rot,
            "info": self.b_info,
            "pin": self.b_pin,
            "trash": self.b_del,
            "folder": self.b_folder,
            "quit": self.b_quit,
        }

        # Build the toolbar in the configured order.
        for item in CONTROL_ORDER:
            if item == "sep":
                row.addWidget(_sep(sep_h))
            elif item in widgets_map:
                row.addWidget(widgets_map[item])

        # Video-only controls (play/pause, time, mute, volume), revealed by set_mode().
        self.vgroup = QWidget()
        vl = QHBoxLayout(self.vgroup)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(4)
        self.b_vplay = IconButton("pause", "Play / pause video  (V)", icon_size, icon_padding)
        self.time = QLabel("0:00 / 0:00")
        self.time.setMinimumWidth(92)
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _apply_drop_shadow(self.time)
        self.b_mute = IconButton("speaker_muted", "Mute / unmute  (M)", icon_size, icon_padding)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.setToolTip("Volume  (PgUp / PgDn)")
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        _apply_drop_shadow(self.volume)
        self._vsep = _sep(sep_h)
        for w in (self._vsep, self.b_vplay, self.time, self.b_mute, self.volume):
            vl.addWidget(w)
        row.addWidget(self.vgroup)
        self.vgroup.hide()

    def set_info(self, text: str):
        self._info_text = text
        self._elide()

    def _elide(self):
        fm = QFontMetrics(self.info.font())
        self.info.setText(fm.elidedText(self._info_text, Qt.TextElideMode.ElideMiddle,
                                        max(80, self.width() - 28)))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._elide()

    def set_time(self, pos_ms: int, dur_ms: int):
        def f(ms):
            s = max(0, int(ms // 1000))
            return f"{s // 60}:{s % 60:02d}"
        self.time.setText(f"{f(pos_ms)} / {f(dur_ms)}")

    # video: show the video controls. audio: also show mute + volume (hidden when there
    # is no sound device).
    def set_mode(self, video: bool, audio: bool):
        self.vgroup.setVisible(video)
        self.b_mute.setVisible(audio)
        self.volume.setVisible(audio)
        self.layout().activate()
        self.adjustSize()


class TopBar(QWidget):
    """Slideshow progress bar. Click = jump, drag = scrub through the slideshow."""
    jumped = Signal(int)
    scrubbed = Signal(int)
    released = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(16)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.index, self.count, self.expanded, self._down = 0, 0, False, False

    def set_state(self, index: int, count: int):
        self.index, self.count = index, count
        self.update()

    def set_expanded(self, on: bool):
        if on != self.expanded:
            self.expanded = on
            self.update()

    # x pixel -> slideshow index.
    def _idx(self, x: float) -> int:
        if self.count <= 1:
            return 0
        return round(min(1.0, max(0.0, x / max(1, self.width()))) * (self.count - 1))

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        frac = 1.0 if self.count <= 1 else self.index / (self.count - 1)
        # Expanded (controls showing / dragging): thick bar with a knob. Collapsed: 3px
        # progress hairline.
        if self.expanded or self._down:
            p.fillRect(0, 0, w, 10, QColor(0, 0, 0, 165))
            p.fillRect(0, 0, int(w * frac), 10, ACCENT)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(WHITE)
            p.drawEllipse(QPointF(min(max(w * frac, 7), w - 7), 5), 7, 7)
        else:
            p.fillRect(0, 0, w, 3, QColor(255, 255, 255, 50))
            p.fillRect(0, 0, int(w * frac), 3, ACCENT)

    # Press jumps immediately; moving while held scrubs (the Viewer debounces the
    # decode).
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._down = True
            self.jumped.emit(self._idx(e.position().x()))
            self.update()

    def mouseMoveEvent(self, e):
        if self._down:
            self.scrubbed.emit(self._idx(e.position().x()))

    def mouseReleaseEvent(self, e):
        if self._down:
            self._down = False
            self.released.emit()
            self.update()


# --------------------------------------------------------------------------- #
# the viewer
# --------------------------------------------------------------------------- #
# Main window: owns the media list, paints the image / video frame itself and handles
# input. The top bar and bottom panel are child overlay widgets.
class Viewer(QWidget):
    STEP_PX = 60                      # drag distance per cycled item
    MAX_ZOOM = 32.0  # max zoom, as a multiple of fit-to-screen
    CACHE_BUDGET = 600 * 1024 * 1024  # decoded-image cache cap (bytes)
    BAR_H = 28  # video seek bar height (px)
    imageDone = Signal(str, object)  # worker -> GUI thread: (path, ImgData | None)

    def __init__(self, root, files, args, shuffled, start_path=None, has_sub_media=False):
        super().__init__()
        self.args, self.root = args, root
        self.has_sub_media = has_sub_media
        self.base_items = list(files)           # natural order
        self.items = list(files)
        self.index = 0
        self.shuffled = False
        self.playing = bool(args.play)
        self.interval_ms = int(max(0.5, args.interval) * 1000)
        self.auto_rotate = not args.no_rotate
        self.muted = True                       # always muted at the start of a session
        self.audio_ok, self.audio_out = False, None
        self.windowed_start = args.windowed
        self.show_file_info = False

        # media / view state
        self.kind = None                        # 'image' | 'video' | None
        self._draw_img = None                   # QImage currently painted
        self._draw_red = None                   # optional reduced-size copy for speed
        self._msize = None
        self._has_alpha = False
        self._message = None
        self.movie = None
        # zoom: multiple of fit. pan: offset in px. rot: 0 or 90 (auto-rotate). fit:
        # base scale that fits the window.
        self.zoom, self.pan, self.rot, self.fit = 1.0, QPointF(0, 0), 0, 1.0
        # True while zooming/panning: skips smooth scaling for speed.
        self._fast = False
        # video
        self.player = self.vsink = None
        # Latest video frame / 'new frame waiting' / 'first frame has arrived' flags.
        self._vframe, self._vdirty, self._vready = None, False, False
        self.v_pos = self.v_dur = 0
        self._video_path = None
        # loading
        # LRU cache of decoded images (path -> ImgData), capped by CACHE_BUDGET.
        self._cache = OrderedDict()
        self._cache_bytes = 0
        # inflight: paths being decoded. pending: path the viewer is waiting to show.
        # wanted: paths still worth decoding (current +/- 1).
        self._inflight, self._pending, self._wanted = set(), None, set()
        # Two threads: the current image and a neighbour decode in parallel.
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="decode")
        # interaction
        # _drag: None | 'cycle' | 'pan' | 'seek'. _acc: horizontal px accumulated
        # towards the next cycle step.
        self._drag, self._last, self._acc = None, QPointF(), 0.0

        # '--controls hidden' starts hidden but pinned: once mouse movement reveals them
        # they stay until unpinned (H / pin button).
        initial_controls = getattr(args, 'controls', 'visible')
        self._controls_visible = (initial_controls != 'hidden')
        self._pinned = (initial_controls == 'hidden')

        self._last_gpos = None
        self._toast = ""

        self.setWindowTitle("Slideshow")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(480, 320)
        self.setStyleSheet("Viewer { background: black; }")

        self.topbar = TopBar(self)
        self.panel = Panel(self, icon_size=args.icon_size, icon_padding=args.icon_padding, opacity=args.controls_opacity)
        self.panel.b_rot.setChecked(self.auto_rotate)
        self.panel.b_info.setChecked(self.show_file_info)
        self.panel.b_pin.setChecked(self._pinned)

        # Timers: slideshow advance | debounced navigation | auto-hide controls | end
        # fast-paint mode | clear toast.
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.go_next)
        self._nav_timer = QTimer(self, singleShot=True)
        self._nav_timer.timeout.connect(self._present)
        self._hide_timer = QTimer(self, singleShot=True)
        self._hide_timer.timeout.connect(self._hide_controls)
        self._fast_timer = QTimer(self, singleShot=True)
        self._fast_timer.timeout.connect(self._end_fast)
        self._toast_timer = QTimer(self, singleShot=True)
        self._toast_timer.timeout.connect(self._clear_toast)

        # Wire up the buttons.
        pn = self.panel
        pn.b_prev.clicked.connect(self.go_prev)
        pn.b_next.clicked.connect(self.go_next)
        pn.b_play.clicked.connect(self.toggle_play)
        pn.b_shuffle.clicked.connect(self.toggle_shuffle)
        pn.b_rot.clicked.connect(self.toggle_rotate)
        pn.b_info.clicked.connect(self.toggle_file_info)
        pn.b_pin.clicked.connect(self.toggle_pin)
        pn.b_del.clicked.connect(self.delete_current)
        pn.b_folder.clicked.connect(self.open_folder)
        pn.b_quit.clicked.connect(self.close)
        pn.b_vplay.clicked.connect(self.toggle_video)
        pn.b_mute.clicked.connect(self.toggle_mute)
        pn.volume.valueChanged.connect(self._on_volume_slider)
        # Top bar: click jumps now; drag scrubs (debounced); release applies any pending
        # jump.
        self.topbar.jumped.connect(lambda i: self.go(i, immediate=True))
        self.topbar.scrubbed.connect(lambda i: self.go(i, immediate=False))
        self.topbar.released.connect(self._flush_nav)
        self.imageDone.connect(self._on_image_done)

        # audio: Qt's device enumeration can crash the whole process when no sound
        # server is reachable (VNC, containers), so it is probed in a throwaway
        # subprocess first; Qt audio APIs are only touched in-process if that passed.
        self._audio_safe = False
        self._devices = None
        self._probe = None
        self._start_audio_probe()

        # initial order
        if shuffled:
            self._set_shuffled()
        elif start_path in self.items:
            self.index = self.items.index(start_path)

        # App-wide filter so mouse movement over child widgets also reveals the
        # controls.
        QApplication.instance().installEventFilter(self)
        self._update_nav_ui()
        self._update_ui()

        # Apply the initial --controls state.
        if initial_controls == 'hidden':
            self.panel.hide()
            self.topbar.set_expanded(False)
            self._controls_visible = False
            self.setCursor(Qt.CursorShape.BlankCursor)
        else:
            self._hide_timer.start(3000)

    # ------------------------------------------------------------ ordering --
    def _set_shuffled(self):
        self.items = list(self.base_items)
        random.shuffle(self.items)
        self.index = 0
        self.shuffled = True

    # `checked` is the desired state. Turning shuffle off keeps your place in sorted
    # order when sub-folders exist; in a flat folder it restarts at the first file.
    def toggle_shuffle(self, checked: bool):
        if checked == self.shuffled:
            return
        if checked:
            self._set_shuffled()
            self._after_reorder("Shuffled")
        else:
            cur = self.items[self.index] if self.items else None
            self.items = list(self.base_items)
            if not self.has_sub_media:
                self.index = 0
            elif cur in self.items:
                self.index = self.items.index(cur)
            else:
                self.index = 0
            self.shuffled = False
            self._after_reorder("Natural order")

    # List order changed: retarget the decode window, refresh the UI, preload
    # neighbours.
    def _after_reorder(self, msg):
        self._wanted = self._neighbourhood()
        self._update_nav_ui()
        self._update_ui()
        self._preload_neighbours()
        self.toast(msg)

    # ----------------------------------------------------------- navigation --
    # Jump to index i (wraps around). immediate=False debounces 45 ms so fast
    # drags/scrubs only decode where you stop.
    def go(self, i: int, immediate=True):
        if not self.items:
            return
        self.index = i % len(self.items)
        self._update_nav_ui()
        if immediate:
            self._present()
        else:
            self._nav_timer.start(45)           # coalesce rapid scrubbing

    # Apply a debounced jump right now (called on mouse release).
    def _flush_nav(self):
        if self._nav_timer.isActive():
            self._present()

    def go_next(self):
        if len(self.items) > 1:
            self.go(self.index + 1)
        # Single-item list: nothing to advance to, just re-arm the timer.
        elif self.items and self.playing and self.kind == "image":
            self.timer.start(self.interval_ms)

    def go_prev(self):
        if len(self.items) > 1:
            self.go(self.index - 1)

    # Current +/- 1 (wrapping): the files worth decoding; _worker skips anything else.
    def _neighbourhood(self):
        n = len(self.items)
        if not n:
            return set()
        return {self.items[self.index], self.items[(self.index + 1) % n],
                self.items[(self.index - 1) % n]}

    # Display items[index]: stop old media, reset zoom/pan, then start the video or
    # request the image (shown by _on_image_done once decoded).
    def _present(self):
        self._nav_timer.stop()
        self.timer.stop()
        self._stop_movie()
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        if not self.items:
            self._stop_video()
            self.kind, self._draw_img, self._pending = None, None, None
            self._message = "No images or videos found.\nPress O to choose a folder."
            self._update_ui()
            self.update()
            return
        path = self.items[self.index]
        self._wanted = self._neighbourhood()
        self._pending = None
        self._message = None
        if media_kind(path) == "video":
            self._start_video(path)
        else:
            self._stop_video()
            self.kind = "image"
            # Clear cache entry for the current file to force re-decoding updated files from disk
            old = self._cache.pop(path, None)
            if old is not None:
                self._cache_bytes -= old.nbytes

            data = self._cache.get(path)
            if data is not None:
                self._cache.move_to_end(path)
                self._show_image(path, data)
            else:
                self._pending = path
                self._request(path)             # neighbours are preloaded once this shows
        self._update_ui()
        if self._pending is None:
            self._preload_neighbours()
        self.update()

    # ------------------------------------------------------ image pipeline --
    # Decode size: 2x the screen's longest side (device pixels, min 1024) so zooming
    # stays sharp.
    def _target_dim(self):
        scr = self.screen().geometry() if self.screen() else self.rect()
        return max(1024, int(2 * max(scr.width(), scr.height()) * self.devicePixelRatioF()))

    # Queue a background decode (deduplicated through _inflight).
    def _request(self, path):
        if path in self._inflight:
            return
        self._inflight.add(path)
        self.pool.submit(self._worker, path, self._target_dim())

    # Pool thread: never touch widgets here; results return via the imageDone signal.
    def _worker(self, path, target):
        try:
            if path not in self._wanted:        # user moved on; skip the work
                self.imageDone.emit(path, None)
                return
            try:
                data = decode_image(path, target)
            except Exception as exc:
                data = ImgData(error=str(exc) or type(exc).__name__)
            self.imageDone.emit(path, data)
        except RuntimeError:                    # window closed while decoding
            pass

    # Warm the cache for next/previous images (videos stream, so they are skipped).
    def _preload_neighbours(self):
        n = len(self.items)
        if n < 2:
            return
        for k in (1, -1):
            p = self.items[(self.index + k) % n]
            if media_kind(p) == "image" and p not in self._cache:
                self._request(p)

    # Insert as most recent, evict oldest until under budget (always keeps at least 3).
    def _cache_put(self, path, data):
        old = self._cache.pop(path, None)
        if old is not None:
            self._cache_bytes -= old.nbytes
        self._cache[path] = data
        self._cache_bytes += data.nbytes
        while self._cache_bytes > self.CACHE_BUDGET and len(self._cache) > 3:
            _k, v = self._cache.popitem(last=False)
            self._cache_bytes -= v.nbytes

    # GUI thread. data None = job skipped because the user moved on (retry if it is
    # still wanted). Display only if it is still the current item.
    def _on_image_done(self, path, data):
        self._inflight.discard(path)
        if data is None:
            if path == self._pending:
                self._request(path)
            return
        if data.error is None:
            self._cache_put(path, data)
        if (path == self._pending and self.kind == "image" and self.items
                and self.items[self.index] == path):
            self._pending = None
            if data.error:
                self._show_error(path, data.error)
            else:
                self._show_image(path, data)
            self._preload_neighbours()

    def _show_image(self, path, data):
        self._message = None
        self._set_image(data.full, data.reduced)
        if data.animated:
            self._start_movie(path)
        self._after_shown()

    def _set_image(self, img, reduced=None):
        self._draw_img, self._draw_red = img, reduced
        self._has_alpha = img.hasAlphaChannel()
        self._msize = img.size()
        self._relayout()

    def _show_error(self, path, msg):
        self._draw_img, self._draw_red, self._msize = None, None, None
        self._message = f"{os.path.basename(path)}\n{msg}"
        self.update()
        if self.playing and self.items:         # don't get stuck on a broken file
            idx = self.index
            QTimer.singleShot(1500, lambda: self.index == idx and self.playing
                              and self.go_next())

    def _after_shown(self):
        if self.kind == "image" and self.playing:
            self.timer.start(self.interval_ms)
        self._update_ui()
        self.update()

    # animated GIF / WebP
    def _start_movie(self, path):
        # QMovie plays the animation; its frames replace _draw_img via _on_movie_frame.
        m = QMovie(path)
        if not m.isValid():
            return
        m.frameChanged.connect(self._on_movie_frame)
        self.movie = m
        m.start()

    def _stop_movie(self):
        if self.movie is not None:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None

    def _on_movie_frame(self, _n):
        if self.movie is None or self.kind != "image":
            return
        img = self.movie.currentImage()
        if img.isNull():
            return
        old = self._msize
        self._draw_img, self._draw_red, self._msize = img, None, img.size()
        self._has_alpha = img.hasAlphaChannel()
        if old != self._msize:
            self._relayout()
        else:
            self.update()

    # ---------------------------------------------------------------- video --
    # One QMediaPlayer is reused for every video. Frames go to a QVideoSink and are
    # painted by us (not a QVideoWidget), so zoom, pan and 90-degree rotation work
    # exactly as for images.
    def _ensure_player(self):
        if self.player is not None:
            return
        self.player = QMediaPlayer(self)
        self.vsink = QVideoSink(self)
        self.player.setVideoSink(self.vsink)
        self.vsink.videoFrameChanged.connect(self._on_frame)
        self.player.positionChanged.connect(self._on_vpos)
        self.player.durationChanged.connect(self._on_vdur)
        self.player.mediaStatusChanged.connect(self._on_vstatus)
        self.player.errorOccurred.connect(self._on_verror)
        self.player.playbackStateChanged.connect(self._on_vstate)
        self._apply_audio()

    # Re-runs this script with --probe-audio in a child process (see __init__). A 10 s
    # timeout kills a hung probe.
    def _start_audio_probe(self):
        if not HAVE_MULTIMEDIA:
            return
        p = QProcess(self)
        p.finished.connect(self._on_probe_done)
        p.errorOccurred.connect(lambda _e: None)
        p.start(sys.executable, [os.path.abspath(__file__), "--probe-audio"])
        self._probe = p

        def _kill():
            if self._probe is not None and self._probe.state() != QProcess.ProcessState.NotRunning:
                self._probe.kill()
        QTimer.singleShot(10000, _kill)

    # Audio is safe only if the child exited normally and printed OK; then also watch
    # for hot-plugged devices.
    def _on_probe_done(self, code, status):
        out = bytes(self._probe.readAllStandardOutput()).decode(errors="ignore").strip()
        ok = status == QProcess.ExitStatus.NormalExit and code == 0 and out == "OK"
        self._audio_safe = ok
        if ok:
            self._devices = QMediaDevices(self)
            self._devices.audioOutputsChanged.connect(self._apply_audio)
        self._apply_audio()

    # (Re)bind the player to a usable output, or detach audio entirely. audio_ok decides
    # whether the mute/volume controls are shown.
    def _apply_audio(self, *_):
        dev = pick_audio_device() if self._audio_safe else None
        self.audio_ok = dev is not None
        if dev is None:
            if self.player is not None:
                try:
                    self.player.setAudioOutput(None)
                except Exception:
                    pass
            if self.audio_out is not None:
                self.audio_out.deleteLater()
                self.audio_out = None
        else:
            if self.audio_out is None:
                self.audio_out = QAudioOutput(self)
                self.audio_out.setMuted(self.muted)
                self.audio_out.setVolume(self._linear_volume())
            self.audio_out.setDevice(dev)
            if self.player is not None:
                self.player.setAudioOutput(self.audio_out)
        if hasattr(self, "panel"):
            self._update_ui()

    def _linear_volume(self):
        return (self.panel.volume.value() / 100.0) ** 2   # perceptual-ish curve

    def _start_video(self, path):
        self._pending = None
        if not HAVE_MULTIMEDIA:
            self.kind = "image"
            self._show_error(path, "Qt Multimedia is not available")
            return
        self._ensure_player()
        self.kind = "video"
        self._video_path = path
        # Reset frame state; the layout is sized when the first frame arrives
        # (_on_frame).
        self._vframe, self._vdirty, self._vready = None, False, False
        self.v_pos = self.v_dur = 0
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        # paused slideshow: loop the clip; playing slideshow: play once, then advance
        self.player.setLoops(1 if self.playing else -1)
        self.player.play()

    def _stop_video(self):
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        self._vframe, self._vdirty, self._vready = None, False, False

    def _on_frame(self, frame):
        if self.kind != "video" or not frame.isValid():
            return
        self._vframe, self._vdirty = frame, True
        if not self._vready:                    # first frame: size the layout now
            self._sync_video_frame(relayout_update=True)
            if self._draw_img is not None and self._vframe is not None:
                self._vready = True
                self._message = None
                self._update_ui()
        self.update()

    # Convert the newest frame to a QImage once per repaint (not once per frame signal)
    # and re-layout if the video size changed.
    def _sync_video_frame(self, relayout_update=False):
        if not (self._vdirty and self._vframe is not None):
            return
        self._vdirty = False
        img = self._vframe.toImage()
        if img.isNull():
            return
        old = self._msize
        self._draw_img, self._draw_red, self._has_alpha = img, None, False
        self._msize = img.size()
        if old != self._msize:
            self._relayout(update=relayout_update)

    def _on_vpos(self, pos):
        self.v_pos = pos
        self.panel.set_time(pos, self.v_dur)
        if self.kind == "video":
            self.update()

    def _on_vdur(self, dur):
        self.v_dur = dur
        self.panel.set_time(self.v_pos, dur)

    # Playing slideshow: advance when the video ends (it plays once). Paused: the clip
    # loops instead.
    def _on_vstatus(self, status):
        if (status == QMediaPlayer.MediaStatus.EndOfMedia and self.kind == "video"
                and self.playing):
            self.go_next()

    def _on_vstate(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.panel.b_vplay.set_icon("pause" if playing else "play")

    def _on_verror(self, _err, msg):
        if self.kind == "video" and self._video_path:
            self._show_error(self._video_path, msg or "Cannot play this video")

    def toggle_video(self):
        if self.kind != "video" or self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    # ---------------------------------------------------------------- sound --
    def toggle_mute(self):
        self.muted = not self.muted
        if self.audio_out is not None:
            self.audio_out.setMuted(self.muted)
        self._update_ui()
        if self.audio_ok and self.kind == "video":
            self.toast("Muted" if self.muted else "Sound on")

    def _on_volume_slider(self, v):
        if self.audio_out is not None:
            self.audio_out.setVolume(self._linear_volume())
        if self.muted and self.audio_ok:        # touching the volume unmutes
            self.muted = False
            if self.audio_out is not None:
                self.audio_out.setMuted(False)
            self._update_ui()

    def _change_volume(self, delta):
        if self.kind == "video" and self.audio_ok:
            self.panel.volume.setValue(self.panel.volume.value() + delta)
            self.toast(f"Volume {self.panel.volume.value()}%")

    # ------------------------------------------------------------ slideshow --
    def toggle_play(self):
        self.set_playing(not self.playing)

    # On: arm the image timer (videos advance on end-of-media). Off: stop the timer and
    # make videos loop.
    def set_playing(self, on: bool):
        self.playing = on
        if on:
            if self.kind == "image" and self._draw_img is not None:
                self.timer.start(self.interval_ms)
        else:
            self.timer.stop()
        if self.kind == "video" and self.player is not None:
            self.player.setLoops(1 if on else -1)
        self._update_ui()
        self.toast("Slideshow playing" if on else "Slideshow paused")

    def toggle_rotate(self):
        self.auto_rotate = not self.auto_rotate
        self.panel.b_rot.setChecked(self.auto_rotate)
        self.pan = QPointF(0, 0)
        self._relayout()
        self.toast("Rotate to fit: " + ("on" if self.auto_rotate else "off"))

    def toggle_file_info(self):
        self.show_file_info = not self.show_file_info
        self.panel.b_info.setChecked(self.show_file_info)
        self.update()
        self.toast("File info overlay: " + ("on" if self.show_file_info else "off"))

    # checked=None (key H) flips the state; a bool (button) sets it.
    def toggle_pin(self, checked: bool = None):
        if checked is None:
            self._pinned = not self._pinned
        else:
            self._pinned = checked
        self.panel.b_pin.setChecked(self._pinned)
        self._show_controls()
        self.toast("Controls pinned" if self._pinned else "Controls auto-hide")

    # --------------------------------------------------------------- delete --
    # Release the file first (stop movie/video), try the trash, else ask before deleting
    # permanently. Then drop it from both lists and the cache and show the next item.
    def delete_current(self):
        if not self.items:
            return
        path = self.items[self.index]
        self._stop_movie()
        self._stop_video()
        ok = False
        try:
            r = QFile.moveToTrash(path)
            ok = r[0] if isinstance(r, tuple) else bool(r)
        except Exception:
            ok = False
        if not ok:
            ans = QMessageBox.question(
                self, "Delete permanently?",
                f"Could not move to the trash:\n{os.path.basename(path)}\n\n"
                "Delete it permanently?", QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.Yes:
                try:
                    os.remove(path)
                    ok = True
                except OSError as exc:
                    self.toast(f"Delete failed: {exc.strerror}", 2500)
        if not ok:
            self._present()
            return
        self.base_items = [p for p in self.base_items if p != path]
        del self.items[self.index]
        old = self._cache.pop(path, None)
        if old is not None:
            self._cache_bytes -= old.nbytes
        if self.index >= len(self.items):
            self.index = 0
        self._update_nav_ui()
        self._present()
        self.toast("Moved to trash: " + os.path.basename(path), 2000)

    # --------------------------------------------------------------- folder --
    # argv for the new slideshow process: this instance's launch options carried over.
    # Windowed and rotate follow the current state; a forced --shuffle is dropped when
    # the new folder has no media in sub-folders (it then uses its normal sorted
    # default), while --no-shuffle always carries over. Options are passed as
    # --name=value so values that start with '-' (negative padding) parse correctly.
    def _child_argv(self, folder):
        a = self.args
        argv = [os.path.abspath(__file__), folder, f"--interval={a.interval}"]
        if a.play:
            argv.append("--play")
        if a.shuffle is False:
            argv.append("--no-shuffle")
        elif a.shuffle and folder_has_sub_media(folder, a.include_hidden):
            argv.append("--shuffle")
        if self.windowed_start:
            argv.append("--windowed")
        if not self.auto_rotate:
            argv.append("--no-rotate")
        if a.include_hidden:
            argv.append("--include-hidden")
        argv.append(f"--icon-size={a.icon_size}")
        if a.icon_padding is not None:
            argv.append(f"--icon-padding={a.icon_padding}")
        argv.append(f"--controls={a.controls}")
        argv.append(f"--controls-opacity={a.controls_opacity}")
        for n in range(1, 11):                  # -a1 .. -a0 command-line actions
            cmd = getattr(a, f"action_{n}", "")
            if cmd:
                argv.append(f"--action-{n}={cmd}")
        argv.extend(getattr(a, "qt_args", []))
        return argv

    # The dialog starts in the current file's folder. The new slideshow is a separate
    # detached process; this one stays underneath, paused.
    def open_folder(self):
        start = os.path.dirname(self.items[self.index]) if self.items else self.root
        was_playing = self.playing
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder for a new slideshow", start,
            QFileDialog.Option.ShowDirsOnly)
        if not folder:
            return
        QProcess.startDetached(sys.executable, self._child_argv(folder))
        if was_playing:                         # leave this one paused underneath
            self.set_playing(False)
        if self.player is not None:
            self.player.pause()

    # ------------------------------------------------------- layout / paint --
    # Compute rot and fit. fit scales the whole image into the window. With auto-rotate,
    # a 90-degree turn is used only if it scales >5% larger (avoids flip-flopping on
    # near-square images).
    def _relayout(self, update=True):
        W, H, ms = self.width(), self.height(), self._msize
        if ms is None or ms.isEmpty() or W <= 0 or H <= 0:
            self.rot, self.fit = 0, 1.0
        else:
            mw, mh = ms.width(), ms.height()
            s0, s1 = min(W / mw, H / mh), min(W / mh, H / mw)
            self.rot = 90 if (self.auto_rotate and s1 > s0 * 1.05) else 0
            self.fit = s1 if self.rot else s0
        self._clamp_pan()
        if update:
            self.update()

    # Keep the image covering the window: pan is limited to half the overflow per axis
    # (0 when it fits). Width/height swap when rotated.
    def _clamp_pan(self):
        ms = self._msize
        if ms is None or ms.isEmpty():
            return
        s = self.fit * self.zoom
        dw, dh = ms.width() * s, ms.height() * s
        if self.rot % 180:
            dw, dh = dh, dw
        mx, my = max(0.0, (dw - self.width()) / 2), max(0.0, (dh - self.height()) / 2)
        self.pan = QPointF(min(mx, max(-mx, self.pan.x())), min(my, max(-my, self.pan.y())))

    # Enter 'fast' paint mode; it ends 160 ms after the last input and repaints at full
    # quality.
    def _interactive(self):
        self._fast = True
        self._fast_timer.start(160)

    def _end_fast(self):
        self._fast = False
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_overlays()
        self._relayout()

    def _place_overlays(self):
        W, H = self.width(), self.height()
        self.topbar.setGeometry(0, 0, W, 16)
        self.panel.adjustSize()
        # Always center the control panel accurately using exact window width
        self.panel.move((W - self.panel.width()) // 2,
                        H - self.panel.height() - 14)
        self.topbar.raise_()
        self.panel.raise_()

    # Paint order: black -> media (+ video seek bar) -> error message -> file info ->
    # toast.
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self.kind == "video":
            self._sync_video_frame()
        if self._draw_img is not None and not self._draw_img.isNull():
            self._paint_media(p)
            if self.kind == "video" and self._vready:
                self._paint_seekbar(p)
        elif self._message:
            p.setPen(QColor(220, 220, 220))
            f = QFont(self.font())
            f.setPointSize(16)
            p.setFont(f)
            p.drawText(self.rect().adjusted(40, 40, -40, -40),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)

        if self.show_file_info and self.items:
            self._paint_file_info(p)

        if self._toast:
            f = QFont(self.font())
            f.setPointSize(14)
            p.setFont(f)
            fm = QFontMetrics(f)
            tw, th = fm.horizontalAdvance(self._toast) + 36, fm.height() + 14
            r = QRectF((self.width() - tw) / 2, 30, tw, th)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 175))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(WHITE)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._toast)

    def _paint_file_info(self, p: QPainter):
        path = self.items[self.index]
        res_str = f"{self._msize.width()} × {self._msize.height()}" if self._msize else "Unknown"
        info_str = f"Path: {path}\nResolution: {res_str}"

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPointSize(12)
        p.setFont(f)
        fm = QFontMetrics(f)

        lines = info_str.split('\n')
        max_w = max(fm.horizontalAdvance(l) for l in lines)
        tw = max_w + 36
        th = (fm.height() * len(lines)) + 20

        r = QRectF(20, 24, tw, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 190))
        p.drawRoundedRect(r, 8, 8)

        p.setPen(WHITE)
        text_rect = r.adjusted(18, 10, -18, -10)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, info_str)
        p.restore()

    # Draw in image coordinates: move to window centre + pan, rotate, scale by fit*zoom.
    def _paint_media(self, p: QPainter):
        ms = self._msize
        mw, mh = ms.width(), ms.height()
        s = self.fit * self.zoom
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self._fast)
        p.save()
        p.translate(self.width() / 2 + self.pan.x(), self.height() / 2 + self.pan.y())
        p.rotate(self.rot)
        p.scale(s, s)
        target = QRectF(-mw / 2, -mh / 2, mw, mh)
        if self._has_alpha:                     # checkerboard behind transparency
            path = QPainterPath()
            path.addRect(target)
            mapped = p.transform().map(path)
            p.save()
            p.resetTransform()
            p.setClipPath(mapped)
            p.fillRect(self.rect(), QBrush(_checker()))
            p.restore()
        # Use the screen-sized copy while the on-screen scale doesn't exceed its
        # resolution.
        src = self._draw_img
        if self._draw_red is not None and s <= self._draw_red.width() / mw:
            src = self._draw_red
        p.drawImage(target, src)
        p.restore()

    # ---- video seek bar: lives in the video's own (rotated) frame ----------
    # Window coords -> the video's own centred, unrotated coords, so the seek bar can be
    # hit-tested as a plain horizontal bar even when the video is rotated.
    def _to_local(self, pos: QPointF) -> QPointF:
        d = pos - QPointF(self.width() / 2, self.height() / 2)
        a = math.radians(-self.rot)
        c, s = math.cos(a), math.sin(a)
        return QPointF(d.x() * c - d.y() * s, d.x() * s + d.y() * c)

    # -> (bar rect, visible video rect) in video coords, or None if no video is ready or
    # it is too small. The bar hugs the bottom of the visible part of the video (so it
    # follows zoom/pan) and sits above the control panel when they overlap (unrotated
    # only).
    def _seekbar_geometry(self):
        if self.kind != "video" or not self._vready or self._msize is None:
            return None
        W, H = self.width(), self.height()
        mw, mh = self._msize.width(), self._msize.height()
        s = self.fit * self.zoom
        a = math.radians(-self.rot)
        c, sn = math.cos(a), math.sin(a)
        px, py = self.pan.x() * c - self.pan.y() * sn, self.pan.x() * sn + self.pan.y() * c
        vr = QRectF(px - mw * s / 2, py - mh * s / 2, mw * s, mh * s)
        hx, hy = (W / 2, H / 2) if self.rot % 180 == 0 else (H / 2, W / 2)
        vis = vr.intersected(QRectF(-hx, -hy, 2 * hx, 2 * hy))
        if vis.width() < 80 or vis.height() < self.BAR_H + 8:
            return None
        bottom = vis.bottom()
        if self.rot == 0 and self._controls_visible and self.panel.isVisible():
            pr = QRectF(self.panel.geometry()).translated(-W / 2, -H / 2)
            if vis.intersects(pr):
                bottom = min(bottom, pr.top() - 6)
        rect = QRectF(vis.left() + 10, bottom - self.BAR_H - 6, vis.width() - 20, self.BAR_H)
        return rect, vis

    def _paint_seekbar(self, p: QPainter):
        g = self._seekbar_geometry()
        if g is None:
            return
        rect, vis = g
        frac = min(1.0, max(0.0, self.v_pos / self.v_dur)) if self.v_dur > 0 else 0.0
        p.save()
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self.rot)                      # bar turns with the video
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # Controls showing (or dragging): full bar with knob. Otherwise just a 3px
        # progress line.
        if self._controls_visible or self._drag == "seek":
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(rect, 8, 8)
            tr = QRectF(rect.left() + 12, rect.center().y() - 3, rect.width() - 24, 6)
            p.setBrush(QColor(255, 255, 255, 70))
            p.drawRoundedRect(tr, 3, 3)
            p.setBrush(ACCENT)
            p.drawRoundedRect(QRectF(tr.left(), tr.top(), tr.width() * frac, 6), 3, 3)
            p.setBrush(WHITE)
            p.drawEllipse(QPointF(tr.left() + tr.width() * frac, tr.center().y()), 8, 8)
        else:
            y = vis.bottom() - 3
            p.setBrush(QColor(255, 255, 255, 60))
            p.drawRect(QRectF(vis.left(), y, vis.width(), 3))
            p.setBrush(ACCENT)
            p.drawRect(QRectF(vis.left(), y, vis.width() * frac, 3))
        p.restore()

    # Hit area is 4px taller than the bar on each side.
    def _seek_hit(self, pos: QPointF) -> bool:
        g = self._seekbar_geometry()
        return bool(g) and g[0].adjusted(0, -4, 0, 4).contains(self._to_local(pos))

    # x position -> fraction of duration. The 12px inset matches the track drawn in
    # _paint_seekbar.
    def _seek_to(self, pos: QPointF):
        g = self._seekbar_geometry()
        if not g or self.player is None or self.v_dur <= 0:
            return
        rect = g[0]
        left, width = rect.left() + 12, rect.width() - 24
        frac = min(1.0, max(0.0, (self._to_local(pos).x() - left) / width))
        self.v_pos = int(frac * self.v_dur)
        self.player.setPosition(self.v_pos)
        self.panel.set_time(self.v_pos, self.v_dur)
        self.update()

    # ------------------------------------------------------------- zooming --
    # Multiply zoom (clamped 1..MAX_ZOOM) around `anchor`.
    def _zoom_at(self, factor: float, anchor: QPointF):
        if self._draw_img is None:
            return
        new = min(self.MAX_ZOOM, max(1.0, self.zoom * factor))
        f = new / self.zoom
        if abs(f - 1.0) < 1e-9:
            return
        a = anchor - QPointF(self.width() / 2, self.height() / 2)
        self.pan = self.pan * f + a * (1 - f)   # keep the point under the cursor fixed
        self.zoom = new
        if new <= 1.0001:
            self.pan = QPointF(0, 0)
        self._clamp_pan()
        self._interactive()
        self.update()

    def zoom_by(self, factor):
        self._zoom_at(factor, QPointF(self.width() / 2, self.height() / 2))

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy:
            self._zoom_at(1.15 ** (dy / 120.0), e.position())

    # --------------------------------------------------------------- mouse --
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._show_controls()
        pos = e.position()
        # Drag priority: seek bar > pan (when zoomed in) > cycle items.
        if self.kind == "video" and self._seek_hit(pos):
            self._drag = "seek"
            self._seek_to(pos)
            return
        self._drag = "pan" if self.zoom > 1.001 else "cycle"
        self._last, self._acc = pos, 0.0
        if self._drag == "pan":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag == "seek":
            self._seek_to(pos)
        elif self._drag == "pan":
            self.pan += pos - self._last
            self._last = pos
            self._clamp_pan()
            self._interactive()
            self.update()
        elif self._drag == "cycle":
            self._acc += pos.x() - self._last.x()
            self._last = pos
            while self._acc <= -self.STEP_PX:   # drag left  -> next
                self._acc += self.STEP_PX
                self.go(self.index + 1, immediate=False)
            while self._acc >= self.STEP_PX:    # drag right -> previous
                self._acc -= self.STEP_PX
                self.go(self.index - 1, immediate=False)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag == "cycle":
            self._flush_nav()
        if self._drag == "pan":
            self.unsetCursor()
        if self._drag == "seek":
            self.update()
        self._drag = None

    # Double-click while zoomed in resets to fit.
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.zoom > 1.001:
            self.zoom, self.pan = 1.0, QPointF(0, 0)
            self._relayout()

    # ------------------------------------------------------------ controls --
    # Reveal controls on mouse movement anywhere in this window, including over child
    # widgets. Returns False so events are never swallowed.
    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.MouseMove and isinstance(obj, QWidget)
                and obj.window() is self):
            gp = ev.globalPosition()
            if gp != self._last_gpos:
                self._last_gpos = gp
                self._show_controls()
        return False

    # Show the controls and restart the 3 s auto-hide countdown.
    def _show_controls(self):
        if not self._controls_visible:
            self._controls_visible = True
            self.panel.show()
            self.panel.raise_()
            self.topbar.set_expanded(True)
            self.unsetCursor()
            self.update()
        self._hide_timer.start(3000)

    # Stay visible while pinned or in use (re-checked every 30 s); otherwise hide the
    # panel, collapse the top bar and blank the cursor in fullscreen.
    def _hide_controls(self):
        busy = self.panel.underMouse() or self.topbar._down or self._drag is not None
        if self._pinned or busy:
            self._hide_timer.start(30000)
            return
        self._controls_visible = False
        self.panel.hide()
        self.topbar.set_expanded(False)
        if self.isFullScreen():
            self.setCursor(Qt.CursorShape.BlankCursor)
        self.update()

    def _update_nav_ui(self):
        n = len(self.items)
        self.topbar.set_state(self.index, n)
        if n:
            rel = os.path.relpath(self.items[self.index], self.root)
            self.panel.set_info(f"{self.index + 1} / {n}   {rel}")
        else:
            self.panel.set_info("no media")

    # Sync button icons/checks with state; mute and volume appear only when a sound
    # device exists.
    def _update_ui(self):
        pn = self.panel
        pn.b_play.set_icon("pause" if self.playing else "play")
        pn.b_shuffle.setChecked(self.shuffled)
        pn.b_mute.set_icon("speaker_muted" if self.muted else "speaker")
        pn.b_info.setChecked(self.show_file_info)
        pn.b_pin.setChecked(self._pinned)
        pn.set_mode(self.kind == "video", self.audio_ok)
        self._place_overlays()

    # Transient centred message at the top of the window.
    def toast(self, text, ms=1400):
        self._toast = text
        self._toast_timer.start(ms)
        self.update()

    def _clear_toast(self):
        self._toast = ""
        self.update()

    # ----------------------------------------------------------------- keys --
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        K = Qt.Key
        self._show_controls()

        # Check if key is configured for a custom command
        if k in COMMAND_MAPPINGS and COMMAND_MAPPINGS[k]:
            cmd_template = COMMAND_MAPPINGS[k]
            current_path = self.items[self.index] if self.items else ""
            # {path} is shell-quoted here, so the template must not quote it itself.
            cmd = cmd_template.format(path=shlex.quote(current_path))
            try:
                # Wait for the command to complete so changes are available immediately
                subprocess.run(cmd, shell=True, check=True)
                self.toast(f"Ran command for key {e.text()}")
                # Reload/refresh the current item representation from disk
                self._present()
            except subprocess.CalledProcessError as ex:
                self.toast(f"Command failed (exit {ex.returncode})", 2500)
            except Exception as ex:
                self.toast(f"Command error: {ex}", 2500)
            return

        # Built-in keys. NB: Right and Space are two separate `if`s, so Right also
        # reaches the final `else` (harmless default handling). While unmapped, 5
        # deletes and 0 resets zoom.
        if k == K.Key_Right:
            self.go_next()
        if k == K.Key_Space:
            self.go_next()
        elif k == K.Key_Left:
            self.go_prev()
        elif k in (K.Key_Up, K.Key_Plus, K.Key_Equal):
            self.zoom_by(1.25)
        elif k in (K.Key_Down, K.Key_Minus):
            self.zoom_by(1 / 1.25)
        elif k == K.Key_P:
            self.toggle_play()
        elif k == K.Key_V:
            self.toggle_video()
        elif k == K.Key_M:
            self.toggle_mute()
        elif k == K.Key_PageUp:
            self._change_volume(+5)
        elif k == K.Key_PageDown:
            self._change_volume(-5)
        elif k == K.Key_5:
            self.delete_current()
        elif k == K.Key_Delete:
            self.delete_current()
        elif k == K.Key_S:
            new_state = not self.shuffled
            self.panel.b_shuffle.setChecked(new_state)
            self.toggle_shuffle(new_state)
        elif k == K.Key_A:
            self.toggle_rotate()
        elif k == K.Key_I:
            self.toggle_file_info()
        elif k == K.Key_H:
            self.toggle_pin()
        elif k == K.Key_0:
            if not COMMAND_MAPPINGS.get(K.Key_0):
                self.zoom, self.pan = 1.0, QPointF(0, 0)
                self._relayout()
        elif k == K.Key_O:
            self.open_folder()
        elif k in (K.Key_F, K.Key_F11):
            self.toggle_fullscreen()
        elif k == K.Key_Escape:
            self.close()
        elif k == K.Key_Q:
            self.close()
        elif k == K.Key_Home and self.items:
            self.go(0)
        elif k == K.Key_End and self.items:
            self.go(len(self.items) - 1)
        else:
            super().keyPressEvent(e)

    # Stop timers and media and abandon queued decodes so the process can exit.
    def closeEvent(self, ev):
        self.timer.stop()
        self._stop_movie()
        if self.player is not None:
            self.player.stop()
            self.player.setVideoSink(None)
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(ev)


# Lazily built 32px checkerboard tile drawn behind transparent images.
_CHECKER = None


def _checker() -> QPixmap:
    global _CHECKER
    if _CHECKER is None:
        pm = QPixmap(32, 32)
        pm.fill(QColor(92, 92, 92))
        p = QPainter(pm)
        p.fillRect(0, 0, 16, 16, QColor(124, 124, 124))
        p.fillRect(16, 16, 16, 16, QColor(124, 124, 124))
        p.end()
        _CHECKER = pm
    return _CHECKER


# --------------------------------------------------------------------------- #
def parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Image + video slideshow (Wayland / X11). Run with no path to pick a folder.")
    ap.add_argument("path", nargs="?", help="folder (or a file inside the folder to start on)")
    ap.add_argument("-i", "--interval", type=float, default=5.0, help="seconds per image (5)")
    ap.add_argument("--play", action="store_true", help="start playing instead of paused")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--shuffle", dest="shuffle", action="store_true", default=None)
    g.add_argument("--no-shuffle", dest="shuffle", action="store_false")
    ap.add_argument("--no-rotate", action="store_true", help="start with rotate-to-fit off")
    ap.add_argument("--windowed", action="store_true", help="do not start fullscreen")
    ap.add_argument("--include-hidden", action="store_true")
    ap.add_argument("--icon-size", type=int, default=64, help="set toolbar icon button size in pixels (64)")
    # Padding: unset (None) = automatic, 20% of the icon size; any int, including 0 and
    # negative values, is used as given.
    ap.add_argument("--icon-padding", type=int, default=None, help="set icon button padding in pixels (0 and negative allowed; default: automatic, 1/5 of the icon size)")
    ap.add_argument("--controls", choices=["visible", "hidden"], default="visible", help="initial state of on-screen controls")
    ap.add_argument("--controls-opacity", type=float, default=0.0, help="controls background opacity from 0.0 to 1.0 (default: 0.0)")

    # Command-line flags for keys 1 through 0 (-a1 / --action-1 through -a0 / --action-10)
    ap.add_argument("-a1", "--action-1", default="", help="Command to run when pressing key 1")
    ap.add_argument("-a2", "--action-2", default="", help="Command to run when pressing key 2")
    ap.add_argument("-a3", "--action-3", default="", help="Command to run when pressing key 3")
    ap.add_argument("-a4", "--action-4", default="", help="Command to run when pressing key 4")
    ap.add_argument("-a5", "--action-5", default="", help="Command to run when pressing key 5")
    ap.add_argument("-a6", "--action-6", default="", help="Command to run when pressing key 6")
    ap.add_argument("-a7", "--action-7", default="", help="Command to run when pressing key 7")
    ap.add_argument("-a8", "--action-8", default="", help="Command to run when pressing key 8")
    ap.add_argument("-a9", "--action-9", default="", help="Command to run when pressing key 9")
    ap.add_argument("-a0", "--action-10", "--action-0", default="", help="Command to run when pressing key 0")

    return ap.parse_known_args(argv)


def pick_folder(start=None):
    if not start or not os.path.isdir(start):
        pics = os.path.expanduser("~/Pictures")
        start = pics if os.path.isdir(pics) else os.path.expanduser("~")
    return QFileDialog.getExistingDirectory(None, "Choose a folder for the slideshow",
                                            start, QFileDialog.Option.ShowDirsOnly)


def probe_audio_main():
    """Child-process mode: print OK if a real sound output exists. May segfault
    when there is no sound server - the parent treats that as 'no sound'."""
    from PySide6.QtCore import QCoreApplication
    _app = QCoreApplication([])
    print("OK" if pick_audio_device() is not None else "NONE", flush=True)
    return 0


def main(argv=None):
    # Internal: child-process mode used by Viewer._start_audio_probe.
    if "--probe-audio" in (sys.argv[1:] if argv is None else argv):
        return probe_audio_main()
    args, qt_args = parse_args(sys.argv[1:] if argv is None else argv)

    # Populate COMMAND_MAPPINGS from command-line arguments if specified
    if args.action_1: COMMAND_MAPPINGS[Qt.Key_1] = args.action_1
    if args.action_2: COMMAND_MAPPINGS[Qt.Key_2] = args.action_2
    if args.action_3: COMMAND_MAPPINGS[Qt.Key_3] = args.action_3
    if args.action_4: COMMAND_MAPPINGS[Qt.Key_4] = args.action_4
    if args.action_5: COMMAND_MAPPINGS[Qt.Key_5] = args.action_5
    if args.action_6: COMMAND_MAPPINGS[Qt.Key_6] = args.action_6
    if args.action_7: COMMAND_MAPPINGS[Qt.Key_7] = args.action_7
    if args.action_8: COMMAND_MAPPINGS[Qt.Key_8] = args.action_8
    if args.action_9: COMMAND_MAPPINGS[Qt.Key_9] = args.action_9
    if args.action_10: COMMAND_MAPPINGS[Qt.Key_0] = args.action_10

    # Unrecognised args (e.g. Qt's -platform) are passed through to Qt, and kept on
    # args so the folder button can hand them to the new slideshow too.
    args.qt_args = qt_args
    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("slideshow")
    app.setDesktopFileName("slideshow")

    # A file argument opens its folder and starts on that file; no argument opens the
    # folder picker.
    path = args.path
    start_file = None
    if path and os.path.isfile(path):
        start_file, path = os.path.abspath(path), os.path.dirname(os.path.abspath(path))
    if not path:
        path = pick_folder()
        if not path:
            return 0
    root = os.path.abspath(path)
    if not os.path.isdir(root):
        print(f"slideshow: not a folder: {root}", file=sys.stderr)
        return 2

    # Default shuffle only when sub-folders contain media; --shuffle / --no-shuffle
    # override.
    files, has_sub_media = scan_media(root, args.include_hidden)
    shuffled = args.shuffle if args.shuffle is not None else has_sub_media

    view = Viewer(root, files, args, shuffled, start_file, has_sub_media)
    if args.windowed:
        view.resize(1280, 800)
        view.show()
    else:
        view.showFullScreen()
    view.setFocus()
    # Defer the first display until the event loop runs, when window size and screen are
    # known.
    QTimer.singleShot(0, view._present)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())