"""Load and draw Windows Media Player skins (.wmz / .wms).

A skin is a zip (.wmz) holding a .wms layout, images and JScript files. This
module turns the layout into a tree of Element objects that know their
geometry, images and state, can paint themselves with QPainter, and can say
which element is under a point. Script execution and live property bindings
live elsewhere; here, values that come from scripts are just left blank.
"""
import os
import re
import tempfile
import zipfile

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import (QBitmap, QColor, QFont, QImage, QMovie, QPainter,
                           QPixmap, QRegion)

from .wms import Node, decode, parse

NAMED_COLORS = {"none": None, "transparent": None}
MAGENTA = QColor("#FF00FF")


def parse_color(value):
    """WMS colours: '#RRGGBB', 'RRGGBB', or names Qt knows ('black', ...)."""
    if not value:
        return None
    v = value.strip().lower()
    if v in NAMED_COLORS:
        return NAMED_COLORS[v]
    if re.fullmatch(r"[0-9a-f]{6}", v):
        v = "#" + v
    c = QColor(v)
    return c if c.isValid() else None


def as_int(value, default=0):
    """Numbers in WMS can be plain ints or script expressions (handled later)."""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def truthy(value, default=True):
    if value is None:
        return default
    # WMP treated anything other than "false" as true (Gorillaz has "ture").
    return str(value).strip().lower() not in ("false", "0", "no")


def is_dynamic(value):
    v = str(value or "").strip().lower()
    return v.startswith(("wmpprop:", "jscript:", "wmpenabled:", "wmpdisabled:", "wmpdown:"))


# ----------------------------------------------------------------- skin files ---
class Skin:
    """A skin on disk: its parsed layout and a cache of its images."""

    def __init__(self, path):
        self.path = path
        if zipfile.is_zipfile(path):
            self.dir = tempfile.mkdtemp(prefix="halcyon-skin-")
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    # Keep extraction inside self.dir whatever the archive says.
                    target = os.path.realpath(os.path.join(self.dir, info.filename))
                    if target.startswith(os.path.realpath(self.dir) + os.sep):
                        z.extract(info, self.dir)
        else:
            self.dir = path if os.path.isdir(path) else os.path.dirname(path)
        self._files = {}
        for base, _, files in os.walk(self.dir):
            for f in files:
                rel = os.path.relpath(os.path.join(base, f), self.dir)
                self._files[rel.replace("\\", "/").lower()] = os.path.join(base, f)
                self._files.setdefault(f.lower(), os.path.join(base, f))
        wms = [p for k, p in self._files.items() if k.endswith(".wms")]
        if not wms:
            raise ValueError(f"No .wms layout found in {path}")
        with open(wms[0], "rb") as f:
            self.root = parse(decode(f.read()))
        self.theme = next(self.root.find_all("THEME"), self.root)
        self.views = list(self.root.find_all("VIEW"))
        self._images = {}

    @property
    def name(self):
        return self.theme.get("title") or self.theme.get("id") or os.path.splitext(os.path.basename(self.path))[0]

    def file(self, name):
        if not name:
            return None
        key = name.replace("\\", "/").strip().lower()
        return self._files.get(key) or self._files.get(os.path.basename(key))

    def read_text(self, name):
        p = self.file(name)
        if not p:
            return None
        with open(p, "rb") as f:
            return decode(f.read())

    def start_view(self):
        """The view WMP opens first: the theme's currentViewID, else the
        first view that isn't a skin-chooser preview."""
        want = (self.theme.get("currentviewid") or "").lower()
        for v in self.views:
            if want and (v.get("id") or "").lower() == want:
                return v
        # Until scripts run (they open the real window from a hidden control
        # view), prefer a view named "main…", else the richest real view.
        real = [v for v in self.views if "preview" not in (v.get("id") or "").lower()]
        for v in real:
            if (v.get("id") or "").lower().startswith("main"):
                return v
        def weight(v):
            sized = bool(v.get("width") or v.get("backgroundimage"))
            return (sized, sum(1 for _ in v.find_all("BUTTON")) + sum(1 for _ in v.find_all("BUTTONELEMENT"))
                    + sum(1 for _ in v.find_all("SUBVIEW")))
        if real:
            return max(real, key=weight)
        return self.views[0] if self.views else None

    def image(self, name, transparent=None):
        """QImage for a skin file with `transparent` colour knocked out."""
        if not name or is_dynamic(name):
            return None
        key = (name.lower(), transparent.rgb() if transparent else None)
        if key in self._images:
            return self._images[key]
        p = self.file(name)
        img = None
        if p:
            img = QImage(p)
            if img.isNull():
                img = None
            else:
                img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
                if transparent is not None:
                    mask = img.createMaskFromColor(transparent.rgb() | 0xFF000000, Qt.MaskOutColor)
                    img.setAlphaChannel(mask)
        self._images[key] = img
        return img

    def movie(self, name):
        """Animated GIFs (Gorillaz ears, eyes...)."""
        p = self.file(name)
        if p and p.lower().endswith(".gif"):
            m = QMovie(p)
            if m.isValid() and m.frameCount() != 1:
                return m
        return None


# --------------------------------------------------------------- elements ---
class Element:
    """One drawable/clickable thing from the layout."""

    clickable = False

    def __init__(self, skin, node, parent=None):
        self.skin = skin
        self.node = node
        self.parent = parent
        self.children = []
        self.id = node.get("id") or ""
        self.hover = False
        self.down = False
        self.enabled = truthy(node.get("enabled"), True) if not is_dynamic(node.get("enabled")) else True
        self.visible = truthy(node.get("visible"), True) if not is_dynamic(node.get("visible")) else True
        self.z = as_int(node.get("zindex"), 0)
        self.left = as_int(node.get("left"), 0)
        self.top = as_int(node.get("top"), 0)
        self.width = as_int(node.get("width"), -1)
        self.height = as_int(node.get("height"), -1)
        self.alpha = as_int(node.get("alphablend"), 255)
        self.tooltip = node.get("uptooltip") or node.get("tooltip") or ""
        self.transparency = parse_color(node.get("transparencycolor"))
        self.clipping = parse_color(node.get("clippingcolor"))
        self.movies = []

    # -- geometry
    def natural_size(self):
        return QSize(0, 0)

    def size(self):
        nat = self.natural_size()
        return QSize(self.width if self.width >= 0 else nat.width(),
                     self.height if self.height >= 0 else nat.height())

    def rect(self):
        """Rectangle in the parent's coordinates."""
        s = self.size()
        return QRect(self.left, self.top, s.width(), s.height())

    def origin(self):
        p = QPoint(self.left, self.top)
        e = self.parent
        while e is not None and not isinstance(e, ViewElement):
            p += QPoint(e.left, e.top)
            e = e.parent
        return p

    def resize(self, w=None, h=None):
        """Change size like WMP: children re-anchor by their alignment
        (left/right/center/stretch, top/bottom/center/stretch)."""
        old = self.size()
        if w is not None:
            self.width = int(w)
        if h is not None:
            self.height = int(h)
        new = self.size()
        # Children re-anchor only when an element that already had a size changes
        # size. An element getting its first size (a container whose width is
        # "jscript:view.width", evaluated after load) goes from 0: its children
        # were laid out for the final size already, so stretching them by the
        # whole width overshoots (NVIDIA's display panel ran past the window).
        dw = new.width() - old.width() if old.width() > 0 else 0
        dh = new.height() - old.height() if old.height() > 0 else 0
        if dw or dh:
            for c in self.children:
                c.anchor(dw, dh)

    def anchor(self, dw, dh):
        ha = (self.node.get("horizontalalignment") or "left").lower()
        va = (self.node.get("verticalalignment") or "top").lower()
        nw = nh = None
        if dw:
            if ha == "right":
                self.left += dw
            elif ha == "center":
                self.left += dw // 2
            elif ha == "stretch":
                nw = self.size().width() + dw
        if dh:
            if va == "bottom":
                self.top += dh
            elif va == "center":
                self.top += dh // 2
            elif va == "stretch":
                nh = self.size().height() + dh
        if nw is not None or nh is not None:
            self.resize(nw, nh)

    def sorted_children(self):
        return sorted(self.children, key=lambda c: c.z)   # stable: file order within a z

    # -- painting
    def paint(self, p: QPainter):
        if not self.visible:
            return
        p.save()
        p.translate(self.left, self.top)
        if self.alpha < 255:
            p.setOpacity(p.opacity() * max(0, self.alpha) / 255)
        kids = self.sorted_children()
        if self.clips_children():
            # Subviews are windows in WMP: children never draw outside them.
            # (Energy Blue's clock is digit strips slid behind 8px windows.)
            s = self.size()
            p.setClipRect(QRect(0, 0, s.width(), s.height()), Qt.IntersectClip)
        # Children with a lower zIndex than their parent sit *behind* it (the
        # Xbox skin's intro animation shows through its screen cover).
        for c in kids:
            if c.z < self.z:
                c.paint(p)
        self.paint_self(p)
        for c in kids:
            if c.z >= self.z:
                c.paint(p)
        p.restore()

    def paint_self(self, p):
        pass

    def clips_children(self):
        return False

    def draw_image(self, p, name, at=QPoint(0, 0), tiled=False, size=None, transparent=None):
        if transparent is None:
            # A subview's or button's own clippingColor cuts its own images; the
            # VIEW's clippingColor is applied to the whole composite instead.
            transparent = self.transparency or (None if isinstance(self, ViewElement) else self.clipping)
        if transparent is None and not isinstance(self, ViewElement):
            # Pure magenta was skins' universal "see-through" colour; WMP keyed it
            # out even when a skin forgot to say so (Halo 2's buttons).
            transparent = MAGENTA
        img = self.skin.image(name, transparent)
        if img is None:
            return False
        if tiled and size is not None:
            p.drawTiledPixmap(QRect(at, size), QPixmap.fromImage(img))
        else:
            p.drawImage(at, img)
        return True

    # -- hit testing (coordinates relative to this element's parent)
    def element_at(self, pt):
        if not self.visible:
            return None
        local = pt - QPoint(self.left, self.top)
        if self.clips_children():
            s = self.size()
            if not (0 <= local.x() < s.width() and 0 <= local.y() < s.height()):
                return None
        for c in reversed(self.sorted_children()):
            hit = c.element_at(local)
            if hit is not None:
                return hit
        if self.clickable and self.enabled and self.hit(local):
            return self
        return None

    def hit(self, local):
        s = self.size()
        return 0 <= local.x() < s.width() and 0 <= local.y() < s.height()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class ContainerElement(Element):
    """VIEW and SUBVIEW: a background image plus children."""

    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        self.background = node.get("backgroundimage")
        self.bgcolor = parse_color(node.get("backgroundcolor"))
        self.tiled = truthy(node.get("backgroundtiled"), False)
        m = skin.movie(self.background) if self.background else None
        if m:
            self.movies.append(m)
        self._movie = m

    def natural_size(self):
        img = self.skin.image(self.background)
        return img.size() if img is not None else QSize(0, 0)

    def clips_children(self):
        # Only when the subview has a real size (some are zero-size groupers).
        s = self.size()
        return s.width() > 0 and s.height() > 0

    def paint_self(self, p):
        s = self.size()
        if self.bgcolor is not None:
            p.fillRect(QRect(QPoint(0, 0), s), self.bgcolor)
        if self._movie is not None:
            p.drawPixmap(0, 0, self._masked(self._movie.currentPixmap()))
        elif self.background:
            self.draw_image(p, self.background, tiled=self.tiled, size=s)

    def _masked(self, pix):
        key = self.transparency or self.clipping
        if key is None or pix.isNull():
            return pix
        img = pix.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
        img.setAlphaChannel(img.createMaskFromColor(key.rgb() | 0xFF000000, Qt.MaskOutColor))
        return QPixmap.fromImage(img)


class ViewElement(ContainerElement):
    def __init__(self, skin, node):
        super().__init__(skin, node, None)
        self.left = self.top = 0
        self.title = node.get("title") or skin.name
        self.titlebar = truthy(node.get("titlebar"), True)
        self.clipping_image = node.get("clippingimage")

    def render(self):
        """Compose the whole view into an image with the window's shape cut
        out. Like WMP, any pixel still in the view's clipping colour after
        composing (background *and* children) is outside the window, and a
        clippingImage, when given, decides on its own."""
        size = self.size()
        img = QImage(size, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        self.paint(p)
        # In WMP the VIDEO control was a real child window, so it sat above every
        # drawn element whatever its zIndex (Energy Blue's video view needs this).
        # The same goes for list controls (PLAYLIST, LISTBOX...).
        for el in self.walk():
            if not isinstance(el, AreaElement) or el.kind not in WINDOWED_TAGS:
                continue
            if not el.size().isValid() or el.size().isEmpty():
                continue
            e, shown = el, True
            while e is not None:
                shown = shown and e.visible
                e = e.parent
            if not shown:
                continue
            rect = QRect(el.origin(), el.size())
            if el.kind == "VIDEO":
                if VIDEO_SOURCE is not None:
                    VIDEO_SOURCE.paint(p, rect)
            else:
                p.save()
                p.setClipRect(rect)
                if el.bgcolor is not None:
                    p.fillRect(rect, el.bgcolor)
                p.translate(rect.topLeft())
                el.paint_list(p)
                p.restore()
        p.end()
        keep = None
        if self.clipping is not None:
            # As a bitmap, MaskInColor marks the *other* pixels: the ones to keep.
            mask = img.createMaskFromColor(self.clipping.rgb() | 0xFF000000, Qt.MaskInColor)
            keep = QRegion(QBitmap.fromImage(mask))
        if self.clipping_image:
            clip = self.skin.image(self.clipping_image)
            if clip is not None:
                key = (self.clipping or QColor("#FF00FF")).rgb() | 0xFF000000
                r = QRegion(QBitmap.fromImage(clip.createMaskFromColor(key, Qt.MaskInColor)))
                keep = r if keep is None else keep.intersected(r)
        if keep is not None:
            out = QImage(size, QImage.Format_ARGB32_Premultiplied)
            out.fill(Qt.transparent)
            p = QPainter(out)
            p.setClipRegion(keep)
            p.drawImage(0, 0, img)
            p.end()
            img = out
        return img

    def window_mask(self, rendered=None):
        """Region of the view that is part of the window (for input and shape)."""
        img = rendered if rendered is not None else self.render()
        return QRegion(QBitmap.fromImage(img.createAlphaMask()))


class ButtonElement(Element):
    """BUTTON and the predefined PLAYBUTTON, STOPBUTTON, ... ."""

    clickable = True

    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        self.images = {k: node.get(k + "image") for k in ("", "hover", "down", "disabled", "hoverdown")}
        self.images[""] = node.get("image")
        self.sticky = truthy(node.get("sticky"), False)
        self.action = node.get("onclick") or ""

    def natural_size(self):
        img = self.skin.image(self.images[""])
        return img.size() if img is not None else QSize(16, 16)

    def current_image(self):
        if not self.enabled and self.images["disabled"]:
            return self.images["disabled"]
        if self.down and self.hover and self.images["hoverdown"]:
            return self.images["hoverdown"]
        if self.down and self.images["down"]:
            return self.images["down"]
        if self.hover and self.images["hover"]:
            return self.images["hover"]
        return self.images[""]

    def paint_self(self, p):
        if not self.draw_image(p, self.current_image()):
            self.draw_image(p, self.images[""])

    def hit(self, local):
        if not super().hit(local):
            return False
        # Clicks only count on the visible part of the button.
        img = self.skin.image(self.images[""], self.transparency or self.clipping)
        if img is not None and img.valid(local) and img.hasAlphaChannel():
            return img.pixelColor(local).alpha() > 0
        return True


class ButtonGroupElement(Element):
    """A BUTTONGROUP draws one picture; a mapping image says which pixel
    belongs to which BUTTONELEMENT (by colour)."""

    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        self.image_name = node.get("image")
        self.mapping = node.get("mappingimage")
        self.state_images = {k: node.get(k + "image") for k in ("hover", "down", "disabled", "hoverdown")}
        self._regions = {}

    def natural_size(self):
        for n in (self.image_name, self.mapping):
            img = self.skin.image(n)
            if img is not None:
                return img.size()
        return QSize(0, 0)

    def region_for(self, color):
        key = color.rgb()
        if key not in self._regions:
            img = self.skin.image(self.mapping)
            r = QRegion()
            if img is not None:
                # As a bitmap, MaskOutColor marks exactly the pixels *of* this colour.
                m = img.createMaskFromColor(color.rgb() | 0xFF000000, Qt.MaskOutColor)
                r = QRegion(QBitmap.fromImage(m))
            self._regions[key] = r
        return self._regions[key]

    def paint_self(self, p):
        if self.image_name:
            self.draw_image(p, self.image_name)
        for el in self.children:
            if not isinstance(el, ButtonElementInGroup) or not el.visible or el.color is None:
                continue
            state = el.state_image()
            if state:
                p.save()
                p.setClipRegion(self.region_for(el.color))
                self.draw_image(p, state)
                p.restore()

    def element_at(self, pt):
        if not self.visible:
            return None
        local = pt - QPoint(self.left, self.top)
        img = self.skin.image(self.mapping)
        if img is None or not img.valid(local):
            return None
        rgb = img.pixelColor(local).rgb() & 0xFFFFFF
        for el in self.children:
            if isinstance(el, ButtonElementInGroup) and el.visible and el.enabled \
                    and el.color is not None and (el.color.rgb() & 0xFFFFFF) == rgb:
                return el
        return None


class ButtonElementInGroup(Element):
    clickable = True

    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        self.color = parse_color(node.get("mappingcolor"))
        self.action = node.get("onclick") or ""
        self.sticky = truthy(node.get("sticky"), False)
        self.own = {k: node.get(k + "image") for k in ("hover", "down", "disabled", "hoverdown")}

    def state_image(self):
        g = self.parent.state_images if isinstance(self.parent, ButtonGroupElement) else {}
        pick = lambda k: self.own.get(k) or g.get(k)
        if not self.enabled:
            return pick("disabled")
        if self.down and self.hover and pick("hoverdown"):
            return pick("hoverdown")
        if self.down:
            return pick("down")
        if self.hover:
            return pick("hover")
        return None

    def paint(self, p):
        pass   # drawn by the group, clipped to this element's mapped region


class TextElement(Element):
    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        v = node.get("value") or ""
        self.text = "" if is_dynamic(v) else v
        self.binding = v if is_dynamic(v) else None
        self.color = parse_color(node.get("foregroundcolor")) or QColor("black")
        self.bgcolor = parse_color(node.get("backgroundcolor"))
        self.font = QFont(node.get("fontface") or node.get("font") or "Tahoma")
        self.font.setPointSizeF(max(1.0, float(as_int(node.get("fontsize"), 8))))
        style = (node.get("fontstyle") or "").lower()
        self.font.setBold("bold" in style)
        self.font.setItalic("italic" in style)
        self.font.setUnderline("underline" in style)
        self.align = {"center": Qt.AlignHCenter, "right": Qt.AlignRight}.get(
            (node.get("justification") or "left").lower(), Qt.AlignLeft)
        self.wrap = truthy(node.get("wordwrap"), False)
        self.clickable = bool(node.get("onclick"))
        self.action = node.get("onclick") or ""

    def natural_size(self):
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font)
        return QSize(fm.horizontalAdvance(self.text or " ") + 2, fm.height())

    def paint_self(self, p):
        s = self.size()
        r = QRect(QPoint(0, 0), s)
        if self.bgcolor is not None:
            p.fillRect(r, self.bgcolor)
        p.setFont(self.font)
        p.setPen(self.color)
        flags = self.align | Qt.AlignVCenter | (Qt.TextWordWrap if self.wrap else 0)
        p.drawText(r, flags, p.fontMetrics().elidedText(self.text, Qt.ElideRight, s.width())
                   if not self.wrap else self.text)


class SliderElement(Element):
    """SLIDER / PROGRESSBAR: background, filled part and a thumb."""

    clickable = True

    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        self.bg = node.get("backgroundimage")
        self.fg = node.get("foregroundimage")
        self.thumb = node.get("thumbimage")
        self.vertical = (node.get("direction") or "horizontal").lower() == "vertical"
        self.minimum = float(as_int(node.get("min"), 0))
        self.maximum = float(as_int(node.get("max"), 100))
        v = node.get("value")
        self.value = float(as_int(v, 0)) if not is_dynamic(v) else self.minimum
        self.binding = v if is_dynamic(v) else None
        self.action = node.get("onclick") or ""

    def natural_size(self):
        img = self.skin.image(self.bg) or self.skin.image(self.fg)
        return img.size() if img is not None else QSize(100, 12)

    def fraction(self):
        span = self.maximum - self.minimum
        return 0.0 if span <= 0 else max(0.0, min(1.0, (self.value - self.minimum) / span))

    def paint_self(self, p):
        s = self.size()
        if self.bg:
            self.draw_image(p, self.bg, tiled=False)
        f = self.fraction()
        if self.fg:
            img = self.skin.image(self.fg, self.transparency)
            if img is not None:
                if self.vertical:
                    h = int(img.height() * f)
                    p.drawImage(QRect(0, img.height() - h, img.width(), h), img, QRect(0, img.height() - h, img.width(), h))
                else:
                    w = int(img.width() * f)
                    p.drawImage(QRect(0, 0, w, img.height()), img, QRect(0, 0, w, img.height()))
        if self.thumb:
            img = self.skin.image(self.thumb, self.transparency)
            if img is not None:
                if self.vertical:
                    y = int((s.height() - img.height()) * (1 - f))
                    p.drawImage(QPoint((s.width() - img.width()) // 2, y), img)
                else:
                    x = int((s.width() - img.width()) * f)
                    p.drawImage(QPoint(x, (s.height() - img.height()) // 2), img)


class CustomSliderElement(SliderElement):
    """CUSTOMSLIDER: `image` holds a strip of frames; the value picks one."""

    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        self.strip = node.get("image")
        self.position_image = node.get("positionimage")

    def natural_size(self):
        img = self.skin.image(self.position_image) or self.skin.image(self.strip)
        return img.size() if img is not None else QSize(32, 32)

    def paint_self(self, p):
        img = self.skin.image(self.strip, self.transparency)
        if img is None:
            return
        s = self.size()
        if s.width() <= 0 or s.height() <= 0:
            return
        # Frames are laid out along the long side of the strip.
        horizontal = img.width() >= img.height() and img.width() > s.width()
        frames = max(1, (img.width() // s.width()) if horizontal else (img.height() // max(1, s.height())))
        i = min(frames - 1, int(self.fraction() * (frames - 1) + 0.5))
        src = QRect(i * s.width(), 0, s.width(), s.height()) if horizontal else QRect(0, i * s.height(), s.width(), s.height())
        p.drawImage(QRect(QPoint(0, 0), s), img, src)


VIDEO_SOURCE = None    # set by the app: object with paint(painter, rect)
AREA_PAINTERS = {}     # tag -> callable(element, painter) for EFFECTS, PLAYLIST...


PLAYLIST_SOURCE = None  # set by the app: callable -> [(title, is_current)]
LIST_TAGS = {"PLAYLIST", "CURRENTPLAYLIST", "LISTBOX"}
WINDOWED_TAGS = LIST_TAGS | {"VIDEO"}   # real child windows in WMP: always on top


class AreaElement(Element):
    """VIDEO, EFFECTS (visualizations), PLAYLIST...: filled by the app."""

    def __init__(self, skin, node, parent=None):
        super().__init__(skin, node, parent)
        self.bgcolor = parse_color(node.get("backgroundcolor"))
        self.kind = node.tag
        self.clickable = self.kind in LIST_TAGS
        self.scroll = 0
        # List styling, in WMP's attribute names.
        # WMP's defaults: black text, now-playing item in blue.
        self.fg = parse_color(node.get("foregroundcolor")) or QColor("#000000")
        self.playing_fg = parse_color(node.get("itemplayingcolor")) or QColor("#0050c8")
        self.items = []          # LISTBOX: filled by the skin's script (addItem)
        self.selected = -1
        self.playing_bg = parse_color(node.get("itemplayingbackgroundcolor"))
        self.list_font = QFont(node.get("fontface") or "Tahoma")
        self.list_font.setPointSizeF(max(6.0, float(as_int(node.get("fontsize"), 8))))

    def row_height(self):
        from PySide6.QtGui import QFontMetrics
        return QFontMetrics(self.list_font).height() + 3

    def row_at(self, local_y):
        return self.scroll + max(0, local_y) // max(1, self.row_height())

    def paint_list(self, p):
        if self.kind == "LISTBOX":
            rows = [(str(t), i == self.selected) for i, t in enumerate(self.items)]
        else:
            rows = PLAYLIST_SOURCE() if PLAYLIST_SOURCE else []
        s = self.size()
        rh = self.row_height()
        p.setFont(self.list_font)
        visible = max(1, s.height() // rh)
        self.scroll = max(0, min(self.scroll, max(0, len(rows) - visible)))
        for i, (title, current) in enumerate(rows[self.scroll:self.scroll + visible]):
            r = QRect(2, i * rh, s.width() - 4, rh)
            if current and self.playing_bg is not None:
                p.fillRect(r, self.playing_bg)
            p.setPen(self.playing_fg if current else self.fg)
            label = f"{self.scroll + i + 1}. {title}" if self.kind != "LISTBOX" else title
            p.drawText(r.adjusted(2, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(label, Qt.ElideRight, r.width() - 4))

    def paint_self(self, p):
        r = QRect(QPoint(0, 0), self.size())
        if self.bgcolor is not None:
            p.fillRect(r, self.bgcolor)
        if self.kind == "VIDEO" or self.kind in LIST_TAGS:
            return        # windowed controls: drawn on top by ViewElement.render()
        if self.kind in AREA_PAINTERS and not r.isEmpty():
            AREA_PAINTERS[self.kind](self, p)


PREDEFINED_BUTTONS = {
    "PLAYBUTTON": "player.controls.play()", "PAUSEBUTTON": "player.controls.pause()",
    "STOPBUTTON": "player.controls.stop()", "NEXTBUTTON": "player.controls.next()",
    "PREVBUTTON": "player.controls.previous()", "MUTEBUTTON": "player.settings.mute = !player.settings.mute",
    "REWBUTTON": "player.controls.fastReverse()", "FFWDBUTTON": "player.controls.fastForward()",
}
PREDEFINED_ELEMENTS = {
    "PLAYELEMENT": "player.controls.play()", "PAUSEELEMENT": "player.controls.pause()",
    "STOPELEMENT": "player.controls.stop()", "NEXTELEMENT": "player.controls.next()",
    "PREVELEMENT": "player.controls.previous()", "MUTEELEMENT": "player.settings.mute = !player.settings.mute",
    "REWELEMENT": "player.controls.fastReverse()", "FFWDELEMENT": "player.controls.fastForward()",
}
AREA_TAGS = {"VIDEO", "EFFECTS", "PLAYLIST", "LISTBOX", "EDITBOX", "BARS", "CURRENTPLAYLIST", "VIDEOSETTINGS",
             "EQUALIZERSETTINGS", "SKINLIST", "MEDIASTUDIO", "PLAYLISTLIST"}
SKIP_TAGS = {"PLAYER", "THEME", "CONTROLS", "SETTINGS", "MEDIACENTER", "ACCELERATOR"}


def build(skin, node, parent=None):
    """Element tree for a VIEW node."""
    tag = node.tag
    if tag == "VIEW":
        el = ViewElement(skin, node)
    elif tag == "SUBVIEW":
        el = ContainerElement(skin, node, parent)
    elif tag == "BUTTON" or tag in PREDEFINED_BUTTONS:
        el = ButtonElement(skin, node, parent)
        if tag in PREDEFINED_BUTTONS and not el.action:
            el.action = PREDEFINED_BUTTONS[tag]
    elif tag == "BUTTONGROUP":
        el = ButtonGroupElement(skin, node, parent)
    elif tag == "BUTTONELEMENT" or tag in PREDEFINED_ELEMENTS:
        el = ButtonElementInGroup(skin, node, parent)
        if tag in PREDEFINED_ELEMENTS and not el.action:
            el.action = PREDEFINED_ELEMENTS[tag]
    elif tag == "TEXT":
        el = TextElement(skin, node, parent)
    elif tag in ("SLIDER", "PROGRESSBAR"):
        el = SliderElement(skin, node, parent)
    elif tag == "CUSTOMSLIDER":
        el = CustomSliderElement(skin, node, parent)
    elif tag in AREA_TAGS:
        el = AreaElement(skin, node, parent)
    else:
        return None
    # Like WMP, an element without its own transparencyColor uses its parent's
    # (Halo 2's transport buttons rely on this).
    if el.transparency is None and parent is not None and not isinstance(parent, ViewElement):
        el.transparency = parent.transparency
    for child in node.children:
        if child.tag in SKIP_TAGS:
            continue
        c = build(skin, child, el)
        if c is not None:
            el.children.append(c)
    return el
