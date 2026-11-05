#!/usr/bin/env python3
"""Draw the images of ACATE from content the AI has decided.

The AI writes what an image says (names, times, notes) into a JSON file; this tool lays it out as
ACATE_Spec_Review_Images prescribes. For a time-lapse it also measures every frame and marks the frames to look at.
It reads no project data by itself and decides nothing.

Usage:
    python ACATE_Script_Review_Images.py <kind> <input.json> <output.png> [--tiles <folder>]
    python ACATE_Script_Review_Images.py measure <input.json> <measurement.json>
    python ACATE_Script_Review_Images.py kinds
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

# ---- colours (ACATE_Spec_Review_Images §1)
RED, BROWN, ENDS, BLUE = "#af3c40", "#be6f3c", "#8178aa", "#4f88a5"
MOVE_BG, STAY_BG = "#fff9f5", "#f5fcf9"
INK, MUTED, LINE, PAGE, CARD = "#1f1911", "#585149", "#e6dcd0", "#f6f3ef", "#fefbf7"

# ---- type: one size for all content, set apart by weight and colour; a smaller one only where room is short
TITLE, TEXT, CAPTION = 46, 28, 24
LEAD = 14                        # gap between wrapped lines
W, X0, X1, TOP = 3200, 64, 3136, 176
PAD, GAP = 32, 28                # inside cards; between cards
OUTPUT_W = 2400                  # the width of the file; pages are laid out at W
# the fonts of each system: (regular file, its index, bold file, its index)
FONTS = [
    (r"C:\Windows\Fonts\msyh.ttc", 0, r"C:\Windows\Fonts\msyhbd.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0, "/System/Library/Fonts/Hiragino Sans GB.ttc", 1),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0, "/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2, "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 2, "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 2),
    ("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", 2, "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc", 2),
]


class ImageInputError(Exception):
    """The input does not describe an image this tool can draw within the layout."""


# words and curved or slanted shapes are drawn LARGE times the layout, then reduced
SCALE, LARGE, SMOOTH = 1, 4, 4
_fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
_large: dict[int, ImageFont.FreeTypeFont] = {}
_probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))


def pick_fonts() -> tuple[str, int, str, int]:
    """ACATE_FONT and ACATE_FONT_BOLD first, then the first system's fonts that are there."""
    regular, bold = os.environ.get("ACATE_FONT"), os.environ.get("ACATE_FONT_BOLD")
    if regular:
        return regular, 0, bold or regular, 0
    for choice in FONTS:
        if Path(choice[0]).exists() and Path(choice[2]).exists():
            return choice
    raise ImageInputError("未找到中文字体，请用环境变量 ACATE_FONT、ACATE_FONT_BOLD 指定")


def font(size: int = TEXT, bold: bool = False) -> ImageFont.FreeTypeFont:
    if (size, bold) not in _fonts:
        regular, regular_index, heavy, heavy_index = pick_fonts()
        _fonts[size, bold] = ImageFont.truetype(heavy if bold else regular, size, index=heavy_index if bold else regular_index)
    return _fonts[size, bold]


def _enlarged(f: ImageFont.FreeTypeFont) -> ImageFont.FreeTypeFont:
    if id(f) not in _large:
        _large[id(f)] = f.font_variant(size=f.size * LARGE)
    return _large[id(f)]


def width(text: str, f: ImageFont.FreeTypeFont) -> float:
    return _probe.textlength(text, font=_enlarged(f)) / LARGE


HALO = 3


class SmoothDraw(ImageDraw.ImageDraw):
    """ImageDraw on a canvas SCALE times the layout: it takes layout coordinates and gives smooth edges. PIL draws
    circles and slanted lines without antialiasing and fits words to the pixel grid, which shows as jagged edges; each
    of these is drawn on a mask LARGE times the layout and reduced to the canvas. Level and upright lines and rectangles
    stay as they are, sharp on the pixels."""

    def __init__(self, img: Image.Image):
        super().__init__(img)
        self._canvas = img

    def _stamp(self, box: Sequence[float], colour: Any, paint: Callable[[ImageDraw.ImageDraw, float, float], None]) -> None:
        x0, y0 = max(0, math.floor(box[0] * SCALE) - 2), max(0, math.floor(box[1] * SCALE) - 2)
        x1 = min(self._canvas.width, math.ceil(box[2] * SCALE) + 2)
        y1 = min(self._canvas.height, math.ceil(box[3] * SCALE) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        mask = Image.new("L", ((x1 - x0) * SMOOTH, (y1 - y0) * SMOOTH), 0)
        paint(ImageDraw.Draw(mask), x0, y0)
        self._canvas.paste(colour, (x0, y0, x1, y1), mask.reduce(SMOOTH))

    @staticmethod
    def _on_mask(p: Sequence[float], x0: float, y0: float) -> tuple[float, float]:
        return (p[0] * SCALE - x0) * SMOOTH, (p[1] * SCALE - y0) * SMOOTH

    @staticmethod
    def _points(xy: Any) -> list[tuple[float, float]]:
        if len(xy) and not isinstance(xy[0], (int, float)):
            return [(float(p[0]), float(p[1])) for p in xy]
        return [(float(xy[i]), float(xy[i + 1])) for i in range(0, len(xy), 2)]

    @staticmethod
    def _bounds(pts: Sequence[tuple[float, float]], margin: float) -> tuple[float, float, float, float]:
        return (min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin,
                max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin)

    def line(self, xy: Any, fill: Any = None, width: int = 0, joint: str | None = None) -> None:
        pts = self._points(xy)
        if joint is None and all(a[0] == b[0] or a[1] == b[1] for a, b in zip(pts, pts[1:])):
            super().line([(p[0] * SCALE, p[1] * SCALE) for p in pts], fill=fill, width=round((width or 1) * SCALE))
            return
        self._stamp(self._bounds(pts, width), fill, lambda m, x0, y0: m.line(
            [self._on_mask(p, x0, y0) for p in pts], fill=255, width=max(1, round(width * LARGE)), joint=joint))

    def rectangle(self, xy: Any, fill: Any = None, outline: Any = None, width: int = 1) -> None:
        a, b = self._points(xy)
        super().rectangle((a[0] * SCALE, a[1] * SCALE, b[0] * SCALE + SCALE - 1, b[1] * SCALE + SCALE - 1), fill=fill,
                          outline=outline, width=round(width * SCALE))

    def ellipse(self, xy: Any, fill: Any = None, outline: Any = None, width: int = 1) -> None:
        a, b = self._points(xy)
        box = lambda x0, y0: (*self._on_mask(a, x0, y0), *self._on_mask(b, x0, y0))
        if fill is not None:
            self._stamp((a[0], a[1], b[0], b[1]), fill, lambda m, x0, y0: m.ellipse(box(x0, y0), fill=255))
        if outline is not None:
            self._stamp((a[0], a[1], b[0], b[1]), outline, lambda m, x0, y0: m.ellipse(
                box(x0, y0), outline=255, width=max(1, round(width * LARGE))))

    def polygon(self, xy: Any, fill: Any = None, outline: Any = None, width: int = 1) -> None:
        pts = self._points(xy)
        scaled = lambda x0, y0: [self._on_mask(p, x0, y0) for p in pts]
        if fill is not None:
            self._stamp(self._bounds(pts, 1), fill, lambda m, x0, y0: m.polygon(scaled(x0, y0), fill=255))
        if outline is not None:
            self._stamp(self._bounds(pts, width), outline, lambda m, x0, y0: m.polygon(
                scaled(x0, y0), outline=255, width=max(1, round(width * LARGE))))

    def text(self, xy: Any, text: str, fill: Any = None, font: ImageFont.FreeTypeFont | None = None,
             halo: str | None = None, **_: Any) -> None:
        """halo: an edge of that colour around the words, for words drawn over dots."""
        large = _enlarged(font)
        left, top, right, bottom = large.getbbox(text)
        if right <= left or bottom <= top:
            return
        x, y = xy
        box = (x + left / LARGE, y + top / LARGE, x + right / LARGE, y + bottom / LARGE)
        if halo:
            edge = (box[0] - HALO, box[1] - HALO, box[2] + HALO, box[3] + HALO)
            self._stamp(edge, halo, lambda m, x0, y0: m.text(self._on_mask((x, y), x0, y0), text, fill=255, font=large,
                                                             stroke_width=round(HALO * LARGE)))
        self._stamp(box, fill, lambda m, x0, y0: m.text(self._on_mask((x, y), x0, y0), text, fill=255, font=large))


def span(start: str, end: str) -> str:
    """A time range, with room around the dash."""
    return f"{start} – {end}"


def duration(seconds: int) -> str:
    """Hours, minutes and seconds, zero seconds left out."""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    parts = ([f"{h} 时"] if h else []) + ([f"{m} 分"] if m or (h and s) else []) + ([f"{s} 秒"] if s else [])
    return " ".join(parts) if parts else "0 秒"


# ---- wrapping: numbers, times and Latin names stay whole; no line starts with closing punctuation
NO_LINE_START = set("，。、；：？！）」』》〉】%,.;:?!)")
TOKEN = re.compile(r"[A-Za-z0-9:._%/+\-–]+|.", re.S)


def wrap(text: str, f: ImageFont.FreeTypeFont, limit: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for token in (t for whole in TOKEN.findall(text) for t in ([whole] if width(whole, f) <= limit else whole)):
        if not current or width(current + token, f) <= limit:
            current += token
        elif token[0] in NO_LINE_START:
            lines.append(current[:-1])
            current = current[-1] + token
        else:
            lines.append(current.rstrip())
            current = token.lstrip()
    if current:
        lines.append(current)
    return lines


def text_height(lines: int, size: int = TEXT) -> int:
    return lines * size + (lines - 1) * LEAD if lines else 0


def note_height(notes: Iterable[str | None], limit: float) -> int:
    """The room for the note under a frame: every row keeps what the longest note needs."""
    return text_height(max((len(wrap(n, font(TEXT), limit)) for n in notes if n), default=1))



def paragraph(d: ImageDraw.ImageDraw, x: float, y: float, text: str, colour: str, limit: float,
              size: int = TEXT, bold: bool = False) -> float:
    """Wrapped text; returns the y below the last line."""
    for line in wrap(text, font(size, bold), limit):
        d.text((x, y), line, fill=colour, font=font(size, bold))
        y += size + LEAD
    return y - LEAD


# ---- runs: pieces of text in several weights and colours on one line
Run = tuple[str, str, ImageFont.FreeTypeFont]


def runs_width(runs: Sequence[Run]) -> float:
    return sum(width(t, f) for t, _, f in runs)


def draw_runs(d: ImageDraw.ImageDraw, x: float, y: float, runs: Sequence[Run]) -> float:
    ascent = max((f.getmetrics()[0] for _, _, f in runs), default=0)
    for text, colour, f in runs:
        d.text((x, y + ascent - f.getmetrics()[0]), text, fill=colour, font=f)
        x += width(text, f)
    return x


def plain(text: str, colour: str | None = None, bold: bool = False, size: int = TEXT) -> Run:
    return text, colour or INK, font(size, bold)


# ---- place names: one colour, never wrapped; a name that does not fit drops to the caption size
def name_runs(name: str, size: int = TEXT, bold: bool = True) -> list[Run]:
    return [(name, INK, font(size, bold))]


def fitted_name(name: str, limit: float, what: str) -> list[Run]:
    for size in (TEXT, CAPTION):
        runs = name_runs(name, size)
        if runs_width(runs) <= limit:
            return runs
    raise ImageInputError(f"{what}: name {name!r} does not fit {limit:.0f} px")


# ---- page parts
def blank(w: float, h: float, colour: str) -> Image.Image:
    """A canvas for w by h of the layout."""
    return Image.new("RGB", (int(w) * SCALE, int(h) * SCALE), colour)


def put(img: Image.Image, part: Image.Image, x: float, y: float) -> None:
    """Paste at a layout position."""
    img.paste(part, (int(x) * SCALE, int(y) * SCALE))


def new_page(height: float) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = blank(W, math.ceil(height), PAGE)
    return img, SmoothDraw(img)


def header(d: ImageDraw.ImageDraw, title: str, summary: str | None = None, page: str | None = None) -> None:
    d.text((X0, 40), title, fill=INK, font=font(TITLE, True))
    if page:
        d.text((X1 - width(page, font(TEXT, True)), 50), page, fill=MUTED, font=font(TEXT, True))
    if summary:
        d.text((X0, 112), summary, fill=MUTED, font=font(TEXT))


def card(d: ImageDraw.ImageDraw, box: Sequence[float], fill: str | None = None) -> None:
    d.rectangle(box, fill=fill or CARD, outline=LINE, width=2)


def place_thumbnail(img: Image.Image, d: ImageDraw.ImageDraw, box: Sequence[float], path: str | None,
                    video: bool = False) -> None:
    """A proxy filling the box (centre crop); without a proxy the box is the line colour."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    try:
        if not path:
            raise FileNotFoundError(path)
        with Image.open(path) as src:
            src = src.convert("RGB")
            w, h = (x1 - x0) * SCALE, (y1 - y0) * SCALE
            s = max(w / src.width, h / src.height)
            src = src.resize((max(1, round(src.width * s)), max(1, round(src.height * s))), Image.LANCZOS)
            ox, oy = (src.width - w) // 2, (src.height - h) // 2
            put(img, src.crop((ox, oy, ox + w, oy + h)), x0, y0)
    except OSError:
        d.rectangle((x0, y0, x1, y1), fill=LINE)
    if video:
        d.polygon([(x0 + 12, y1 - 36), (x0 + 12, y1 - 12), (x0 + 33, y1 - 24)], fill=MUTED)


# ---- maps: Web Mercator, so that basemap tiles line up with the drawing
EARTH = 40_075_016.686            # metres round the equator
MAX_SCALE = 2 ** 28               # px per world width: about 0.15 m a pixel at the equator


def mercator(lat: float, lon: float) -> tuple[float, float]:
    """Latitude and longitude as Web Mercator world coordinates, both 0..1."""
    s = math.sin(math.radians(max(min(lat, 85.0511), -85.0511)))
    return (lon + 180) / 360, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


class MapFrame:
    """Latitude and longitude into a box: Web Mercator, fitted to the points with a margin, centred."""

    def __init__(self, box: Sequence[float], points: Sequence[Sequence[float]], margin: float = 72):
        self.box = box
        world = [mercator(*p[:2]) for p in points]
        xs, ys = [w[0] for w in world], [w[1] for w in world]
        x0, y0, x1, y1 = box
        self.scale = min((x1 - x0 - 2 * margin) / max(max(xs) - min(xs), 1e-12),
                         (y1 - y0 - 2 * margin) / max(max(ys) - min(ys), 1e-12), MAX_SCALE)
        self.cx, self.cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        self.mx, self.my = (x0 + x1) / 2, (y0 + y1) / 2

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        x, y = mercator(lat, lon)
        return self.mx + (x - self.cx) * self.scale, self.my + (y - self.cy) * self.scale

    def world(self, px: float, py: float) -> tuple[float, float]:
        return self.cx + (px - self.mx) / self.scale, self.cy + (py - self.my) / self.scale

    def latlon(self, px: float, py: float) -> tuple[float, float]:
        x, y = self.world(px, py)
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y)))), x * 360 - 180

    def metres_per_px(self) -> float:
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * self.cy))))
        return math.cos(math.radians(lat)) * EARTH / self.scale


# ---- basemap tiles: fetched only when a cache folder is given, at most one request
# every 3 seconds to a host, cached for reuse; a host that refuses (403) or asks to slow down (429, 503, 509) is not
# asked again in this run, and its tiles are left as placeholders
TILE_SOURCES = {
    "osm": ("tile.openstreetmap.org", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19, "© OpenStreetMap"),
    "satellite": ("server.arcgisonline.com",
                  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                  18, "© Esri World Imagery"),
}
USER_AGENT = "ACATE-Review-Images/1.0.1 (personal travel archive; research use)"
TILE_INTERVAL = 3.0
ZOOM_FALLBACK = 6                # zoom levels a map may step down where the imagery stops short
TILE_CACHE: Path | None = None
WARNINGS: list[str] = []
MAP_EXTENTS: list[dict[str, Any]] = []   # what each map cell shows, so that a place on the image can be read off
_last_request: dict[str, float] = {}
_refused: set[str] = set()


def fetch_tile(source: str, z: int, x: int, y: int) -> Image.Image | None:
    """One 256 px tile from the cache, or from the host when a cache folder is set; None when it cannot be had."""
    host, url, _, _ = TILE_SOURCES[source]
    if TILE_CACHE is None:
        return None
    path = TILE_CACHE / source / str(z) / str(x) / f"{y}.png"
    if path.exists():
        with Image.open(path) as cached:
            return cached.convert("RGB")
    if host in _refused:
        return None
    wait = _last_request.get(host, 0.0) + TILE_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(url.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in (403, 429, 503, 509):
            _refused.add(host)
            WARNINGS.append(f"{host} answered {exc.code}; its tiles are left as placeholders in this run")
        else:
            WARNINGS.append(f"{host} tile {z}/{x}/{y}: HTTP {exc.code}")
        return None
    except OSError as exc:
        WARNINGS.append(f"{host} tile {z}/{x}/{y}: {exc}")
        return None
    finally:
        _last_request[host] = time.monotonic()
    try:
        tile = Image.open(io.BytesIO(data)).convert("RGB")
    except OSError:
        WARNINGS.append(f"{host} tile {z}/{x}/{y}: not an image")
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tile.save(path)
    return tile


def no_imagery(tile: Image.Image) -> bool:
    """Esri answers with a grey tile saying that it has no imagery where the zoom goes beyond what it holds: a single
    grey fills it, which no photograph of the ground does."""
    pixels = tile.width * tile.height
    count, colour = max(tile.getcolors(pixels))
    return count >= 0.8 * pixels and len(set(colour)) == 1


def tile_mosaic(frame: MapFrame, size: tuple[int, int], source: str, z: int,
                fill: str) -> tuple[Image.Image | None, tuple[float, float, float, float], int]:
    """The tiles at zoom z under a map and the map's box on them, with the number of tiles that hold no imagery; no
    image when not a single tile could be used."""
    n = 2 ** z
    wx0, wy0 = frame.world(frame.box[0], frame.box[1])
    wx1, wy1 = frame.world(frame.box[0] + size[0], frame.box[1] + size[1])
    tx0, ty0 = math.floor(wx0 * n), max(0, math.floor(wy0 * n))
    tx1, ty1 = math.floor(wx1 * n), min(n - 1, math.floor(wy1 * n))
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), fill)
    got = empty = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            tile = fetch_tile(source, z, tx % n, ty)
            if tile is not None and source == "satellite" and no_imagery(tile):
                empty += 1
            elif tile is not None:
                mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
                got += 1
    crop = ((wx0 * n - tx0) * 256, (wy0 * n - ty0) * 256, (wx1 * n - tx0) * 256, (wy1 * n - ty0) * 256)
    return (mosaic if got else None), crop, empty


def basemap(frame: MapFrame, size: tuple[int, int], source: str, fill: str) -> Image.Image | None:
    """The tiles under a map of the given size, cut and scaled to it; None when no tile could be had. Where the imagery
    stops short of the zoom wanted, the map comes from a lower zoom. A street map is turned to quiet greys so that the
    marks keep their colours; a satellite image keeps its own."""
    if source not in TILE_SOURCES:
        raise ImageInputError(f"unknown basemap {source!r}; known: {', '.join(TILE_SOURCES)}")
    wanted = max(0, min(TILE_SOURCES[source][2], math.ceil(math.log2(max(frame.scale / 256, 1)))))
    for z in range(wanted, max(wanted - ZOOM_FALLBACK, 0) - 1, -1):
        mosaic, crop, empty = tile_mosaic(frame, size, source, z, fill)
        if not empty:
            break
    if empty:
        WARNINGS.append(f"{source}: {empty} tiles at zoom {z} hold no imagery")
    if mosaic is None:
        return None
    if z < wanted:
        WARNINGS.append(f"{source}: no imagery at zoom {wanted} here; drawn from zoom {z}")
    image = mosaic.resize((size[0] * SCALE, size[1] * SCALE), Image.LANCZOS, box=crop)
    if source == "osm":
        image = Image.blend(image.convert("L").convert("RGB"), Image.new("RGB", image.size, PAGE), 0.55)
    return image


def patch(img: Image.Image, box: Sequence[float]) -> None:
    """A card-coloured patch at 85 % behind words and the scale bar drawn over a basemap."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle(tuple(v * SCALE for v in box), fill=CARD + "d9")
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


def dashed(d: ImageDraw.ImageDraw, pts: Sequence[tuple[float, float]], colour: str, w: int, on: float = 14,
           off: float = 10) -> None:
    """The dash keeps its pace along the whole line, so that the short segments of a dense route do not turn solid."""
    walked = 0.0
    for a, b in zip(pts, pts[1:]):
        length = math.dist(a, b)
        t = 0.0
        while t < length:
            phase = (walked + t) % (on + off)
            if phase >= on:
                t += on + off - phase
                continue
            e = min(t + on - phase, length)
            d.line([(a[0] + (b[0] - a[0]) * t / length, a[1] + (b[1] - a[1]) * t / length),
                    (a[0] + (b[0] - a[0]) * e / length, a[1] + (b[1] - a[1]) * e / length)], fill=colour, width=w)
            t = e + 1e-6
        walked += length


def dot(d: ImageDraw.ImageDraw, p: tuple[float, float], r: float, colour: str, ring: float = 0) -> None:
    if ring:
        d.ellipse((p[0] - r - ring, p[1] - r - ring, p[0] + r + ring, p[1] + r + ring), fill=CARD)
    d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=colour)


SCALE_STEPS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10_000, 20_000, 50_000, 100_000, 200_000,
               500_000, 1_000_000]
ScalePlace = tuple[tuple[float, float, float, float], float, str]


def scale_place(frame: MapFrame, marks: Sequence[Sequence[float]]) -> ScalePlace:
    """Where the scale bar goes: its box, length in px and label. A round length near a fifth of the map width, about
    30 px from the edges, in the first corner clear of the lines, dots and words in marks (lower left, lower right,
    upper left, upper right); with no corner clear the same at the next shorter round length; with none clear either,
    the place touching the fewest."""
    x0, y0, x1, y1 = frame.box
    per_px = frame.metres_per_px()
    target = (x1 - x0) / 5 * per_px
    places: list[ScalePlace] = []
    for metres in ([n for n in SCALE_STEPS if n <= target] or SCALE_STEPS[:1])[:-3:-1]:
        px, label = metres / per_px, f"{metres // 1000} km" if metres >= 1000 else f"{metres} m"
        w = 16 + max(px, width(label, font(CAPTION)))
        places += [((x, y, x + w, y + 48), px, label) for y in (y1 - 70, y0 + 22) for x in (x0 + 22, x1 - 22 - w)]
    return min(places, key=lambda p: sum(not (p[0][2] < t[0] or t[2] < p[0][0] or p[0][3] < t[1] or t[3] < p[0][1])
                                         for t in marks))


def scale_bar(d: ImageDraw.ImageDraw, place: ScalePlace, img: Image.Image | None = None,
              ground: str | None = None) -> None:
    """The scale bar in its place: over a basemap (img given) on a card-coloured patch, on a plain map on a patch of
    the map's own colour."""
    box, px, label = place
    if img is not None:
        patch(img, box)
        d = SmoothDraw(img)
    elif ground:
        d.rectangle(box, fill=ground)
    d.text((box[0] + 8, box[1] + 6), label, fill=MUTED, font=font(CAPTION))
    d.line((box[0] + 8, box[3] - 8, box[0] + 8 + px, box[3] - 8), fill=MUTED, width=3)


def legend(d: ImageDraw.ImageDraw, x: float, y: float, items: Sequence[tuple[str, str, str]]) -> float:
    """items: (symbol, colour, text); symbol 'line' | 'dash' | 'box'. Symbols about 45 px, text in the secondary colour."""
    f = font(TEXT)
    for symbol, colour, text in items:
        mid = y + TEXT / 2 + 4
        if symbol == "line":
            d.line((x, mid, x + 45, mid), fill=colour, width=4)
        elif symbol == "dash":
            dashed(d, [(x, mid), (x + 45, mid)], colour, 4, 10, 7)
        else:
            d.rectangle((x, mid - 14, x + 45, mid + 13), fill=colour, outline=LINE, width=1)
        x += 45 + 14
        d.text((x, y), text, fill=MUTED, font=f)
        x += width(text, f) + 44
    return x


# ======================================================================================================== §3.1 当天总图
DAY_COLUMNS = [("段", 90), ("名称", 900), ("时刻", 330), ("时长", 240), ("素材", 0)]
DAY_X1, SIDE_X0 = 2240, 2280
DAY_ROW_H = 72


def draw_day_overview(data: dict[str, Any]) -> Image.Image:
    """One day (§3.1): one row per segment, the day's map, the points to check and notes."""
    rows = data["rows"]
    xs = [X0]
    for _, w in DAY_COLUMNS[:-1]:
        xs.append(xs[-1] + w)
    bold, regular = font(TEXT, True), font(TEXT)
    table_h = 64 + DAY_ROW_H * len(rows)
    contact = data.get("contact_sheet") or []
    if len(contact) > 25:
        raise ImageInputError(f"a contact sheet holds 25 files, not {len(contact)}: put the rest on the next page")
    contact_h = contact_sheet_height(contact) if contact else 0
    side_h, draw_side = layout_day_side(data)
    img, d = new_page(TOP + max(table_h + (GAP + contact_h if contact else 0), side_h) + 48)
    pending = sum(1 for r in rows if r.get("pending"))
    header(d, data["title"], f"{len(rows)} 段　待确认 {pending} 件", data.get("page"))
    card(d, (X0, TOP, DAY_X1, TOP + table_h))
    for (label, _), x in zip(DAY_COLUMNS, xs):
        d.text((x + 24, TOP + 16), label, fill=MUTED, font=bold)
    yy = TOP + 64
    for r in rows:
        pend = r.get("pending")
        d.line((X0, yy, DAY_X1, yy), fill=LINE, width=1)
        mid = yy + DAY_ROW_H / 2 - TEXT / 2 - 6
        d.text((xs[0] + 24, mid), str(r["number"]), fill=RED if pend else INK, font=bold)
        if r["kind"] == "stay":
            runs = fitted_name(r["name"], DAY_COLUMNS[1][1] - 48, f"segment {r['number']}")
        else:
            mode, _, service = r["mode"].partition(" ")
            runs = [(mode, BROWN, bold)] + ([(f" {service}", INK, bold)] if service else [])
        draw_runs(d, xs[1] + 24, mid, runs)
        d.text((xs[2] + 24, mid), span(r["start"], r["end"]), fill=INK, font=bold)
        d.text((xs[3] + 24, mid), duration(r["seconds"]), fill=INK, font=regular)
        media = [plain(str(r.get("media_count", 0)))] + ([plain(f"　延时 {r['time_lapses']}", MUTED)] if r.get("time_lapses") else [])
        draw_runs(d, xs[4] + 24, mid, media)
        yy += DAY_ROW_H
    if contact:
        draw_contact_sheet(img, d, TOP + table_h + GAP, contact)
    draw_side(img, d)
    return img


def contact_caption(item: dict[str, Any]) -> str:
    return f"{item['file']}　{item['time']}"


def contact_row_height(items: Sequence[dict[str, Any]]) -> float:
    """A row of the contact sheet: the thumbnail and the longest caption of the sheet, which may wrap."""
    tw_ = (DAY_X1 - X0 - 2 * PAD) / 5 - 24
    lines = max((len(wrap(contact_caption(item), font(CAPTION), tw_)) for item in items), default=1)
    return tw_ * 9 / 16 + 12 + text_height(lines, CAPTION) + 28


def contact_sheet_height(items: Sequence[dict[str, Any]]) -> float:
    return 2 * PAD + math.ceil(len(items) / 5) * contact_row_height(items)


def draw_contact_sheet(img: Image.Image, d: ImageDraw.ImageDraw, y: float, items: Sequence[dict[str, Any]]) -> None:
    """A whole day in one place (§3.1): 5 × 5 cells in capture order, one media file per cell."""
    cell = (DAY_X1 - X0 - 2 * PAD) / 5
    tw_, th = cell - 24, (cell - 24) * 9 / 16
    card(d, (X0, y, DAY_X1, y + contact_sheet_height(items)))
    row = contact_row_height(items)
    for i, item in enumerate(items):
        cx, cy = X0 + PAD + (i % 5) * cell, y + PAD + (i // 5) * row
        place_thumbnail(img, d, (cx, cy, cx + tw_, cy + th), item.get("proxy"), item.get("video", False))
        paragraph(d, cx, cy + th + 12, contact_caption(item), MUTED, tw_, CAPTION)


def layout_day_side(data: dict[str, Any]) -> tuple[float, Callable[[Image.Image, ImageDraw.ImageDraw], None]]:
    """The right column of the day overview: the main map, an inset for each place where labels crowd (found here,
    again inside an inset that is still crowded), and one card with pending items, notices and notes. Returns its
    height and the function drawing it."""
    places, links = data.get("places", []), data.get("links", [])
    maps: list[dict[str, Any]] = []
    queue: list[tuple[dict[str, Any] | None, list[dict[str, Any]], int]] = [(None, list(places), 0)] if places else []
    while queue:
        inset, shown, depth = queue.pop(0)
        top = TOP if not maps else maps[-1]["box"][3] + 24
        box = (SIDE_X0, top, X1, top + (380 if inset is None else 400))
        children = find_insets(shown, box, inset is None, links) if depth < 3 else []
        maps.append({"inset": inset, "shown": shown, "children": children, "box": box})
        queue += [(c, [p for p in shown if p["number"] in c["numbers"]], depth + 1) for c in children]
    maps_bottom = maps[-1]["box"][3] if maps else TOP
    card_top = maps_bottom + GAP
    inner = X1 - SIDE_X0 - 2 * PAD
    pending, notes = data.get("pending", []), data.get("notes", [])
    regular = font(TEXT)
    blocks: list[tuple[str, list[str]]] = [(k, v) for k, v in (("待确认", pending), ("备注", notes)) if v]
    card_h = PAD
    for _, items in blocks:
        card_h += TEXT + 18 + sum(text_height(len(wrap(it, regular, inner))) + 12 for it in items) + 20
    card_h += PAD - 20
    height = (card_top + card_h if blocks else maps_bottom) - TOP

    def draw(img: Image.Image, d: ImageDraw.ImageDraw) -> None:
        for m in maps:
            draw_day_map(img, d, m["box"], m["shown"], links, m["children"], m["inset"])
        if not blocks:
            return
        card(d, (SIDE_X0, card_top, X1, card_top + card_h))
        x, y = SIDE_X0 + PAD, card_top + PAD
        for title, items in blocks:
            d.text((x, y), title, fill=RED if title == "待确认" else INK, font=font(TEXT, True))
            y += TEXT + 18
            for it in items:
                y = paragraph(d, x, y, it, INK if title == "待确认" else MUTED, inner) + 12
            y += 20

    return height, draw


def find_insets(places: Sequence[dict[str, Any]], box: Sequence[float], legend_row: bool,
                links: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """§3.1: points closer than 60 px on a map are grouped; each group is drawn again in an inset, the largest first,
    labelled with the segments in it."""
    if len(places) < 2:
        return []
    coords = [p["coord"] for p in places] + ([c for link in links for c in link["path"]] if legend_row else [])
    frame = MapFrame((box[0], box[1], box[2], box[3] - (56 if legend_row else 0)), coords)
    xy = [frame.xy(*p["coord"]) for p in places]
    group = list(range(len(places)))
    for i in range(len(places)):
        for j in range(i):
            if math.dist(xy[i], xy[j]) < 60 and group[i] != group[j]:
                old, new = group[i], group[j]
                group = [new if g == old else g for g in group]
    members: dict[int, list[int]] = {}
    for i, g in enumerate(group):
        members.setdefault(g, []).append(i)
    out = []
    # Every place in one group is worth an inset only where a route has stretched the map.
    limit = len(places) + 1 if legend_row and links else len(places)
    for crowded in sorted((m for m in members.values() if 1 < len(m) < limit), key=lambda m: (-len(m), m[0])):
        labels = sorted(n for i in crowded for n in places[i].get("labels", [places[i]["number"]]))
        out.append({"numbers": [places[i]["number"] for i in crowded],
                    "label": "段 " + "、".join(map(str, labels))})
    return out


def along(path: Sequence[tuple[float, float]], step: float = 8) -> list[tuple[float, float, float, float]]:
    """Small boxes along a line, so that labels keep off it."""
    boxes = []
    for a, b in zip(path, path[1:]):
        n = max(1, int(math.dist(a, b) / step))
        for i in range(n + 1):
            x, y = a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n
            boxes.append((x - 4, y - 4, x + 4, y + 4))
    return boxes


def place_label(d: ImageDraw.ImageDraw, near: Sequence[float], label: str, colour: str, area: Sequence[float],
                taken: list[tuple[float, float, float, float]], backed: Image.Image | None = None) -> None:
    """Write a label beside a point or a box (x0, y0, x1, y1): right, left, below, above, then the four corners, the
    first spot inside the area that touches no other label, point, line or box; over a basemap (backed given) it sits
    on a card-coloured patch."""
    f = font(CAPTION, True)
    w, h = width(label, f), CAPTION + 6
    x0, y0, x1, y1 = near
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    spots = [(x1 + 6, cy - h / 2 - 3), (x0 - 6 - w, cy - h / 2 - 3), (cx - w / 2, y1 + 4), (cx - w / 2, y0 - 4 - h),
             (x1 + 6, y0 - 4 - h), (x1 + 6, y1 + 4), (x0 - 6 - w, y0 - 4 - h), (x0 - 6 - w, y1 + 4)]
    fits = lambda r: area[0] + 4 <= r[0] and r[2] <= area[2] - 4 and area[1] + 4 <= r[1] and r[3] <= area[3] - 4
    free = lambda r: all(r[2] < t[0] or t[2] < r[0] or r[3] < t[1] or t[3] < r[1] for t in taken)
    boxes = [(x, y, x + w, y + h) for x, y in spots]
    box = next((b for b in boxes if fits(b) and free(b)), next((b for b in boxes if fits(b)), boxes[0]))
    taken.append(box)
    if backed is not None:
        bb = d.textbbox((box[0], box[1]), label, font=f)
        patch(backed, (bb[0] - 6, bb[1] - 4, bb[2] + 6, bb[3] + 4))
    d.text((box[0], box[1]), label, fill=colour, font=f)


def draw_day_map(img: Image.Image, d: ImageDraw.ImageDraw, box: Sequence[float], shown: Sequence[dict[str, Any]],
                 links: Sequence[dict[str, Any]], children: Sequence[dict[str, Any]], inset: dict[str, Any] | None) -> None:
    """On the street map: routes in brown (walking dashed, other transport solid, the pending one red), registered
    coordinates with their segment numbers, a scale bar, and a frame for each inset drawn from this map. The main map
    (inset None) also has a legend line; an inset has its name in the upper left. Routes are cut at the edge of the
    map."""
    main = inset is None
    content = (box[0], box[1], box[2], box[3] - (56 if main else 0))
    frame = MapFrame(content, [p["coord"] for p in shown] + ([c for link in links for c in link["path"]] if main else []))
    w, h = int(content[2] - content[0]), int(content[3] - content[1])
    layer = blank(w, h, PAGE)
    tiles = basemap(frame, (w, h), "osm", PAGE)
    if tiles is not None:
        layer.paste(tiles)
    backed = img if tiles is not None else None     # over a basemap, lines get a card-coloured edge, words a patch
    ld = SmoothDraw(layer)
    for link in links:
        path = [(x - content[0], y - content[1]) for x, y in (frame.xy(*c) for c in link["path"])]
        colour = RED if link.get("pending") else BROWN
        if backed is not None:
            ld.line(path, fill=CARD, width=8, joint="curve")
        (dashed(ld, path, colour, 4) if link.get("walk") else ld.line(path, fill=colour, width=4, joint="curve"))
    d.rectangle(box, fill=PAGE)
    put(img, layer, content[0], content[1])
    d.rectangle(box, outline=LINE, width=2)
    crowded = {n for c in children for n in c["numbers"]}
    taken: list[tuple[float, float, float, float]] = []
    for link in links:
        taken += along([frame.xy(*c) for c in link["path"]])
    if not main:
        taken.append((box[0], box[1], box[0] + 40 + width(inset["label"], font(CAPTION, True)), box[1] + 52))
    taken += [(q[0] - 13, q[1] - 13, q[0] + 13, q[1] + 13) for q in (frame.xy(*p["coord"]) for p in shown)]
    frames = []
    for c in children:
        pts = [frame.xy(*p["coord"]) for p in shown if p["number"] in c["numbers"]]
        fb = (min(q[0] for q in pts) - 18, min(q[1] for q in pts) - 18, max(q[0] for q in pts) + 18,
              max(q[1] for q in pts) + 18)
        taken.append(fb)
        frames.append((c, fb))
    mark = (content[2] - 24 - width(TILE_SOURCES["osm"][3], font(CAPTION)), content[3] - 46)
    if backed is not None:
        taken.append((mark[0] - 8, content[3] - 50, content[2] - 16, content[3] - 16))
    scale = scale_place(frame, taken)
    taken.append(scale[0])
    scale_bar(d, scale, backed, PAGE)
    if backed is not None:
        patch(img, taken[-2])
        d.text(mark, TILE_SOURCES["osm"][3], fill=MUTED, font=font(CAPTION))
    if not main:
        if backed is not None:
            patch(img, (box[0] + 12, box[1] + 12, box[0] + 28 + width(inset["label"], font(CAPTION, True)), box[1] + 48))
        d.text((box[0] + 20, box[1] + 16), inset["label"], fill=MUTED, font=font(CAPTION, True))
    for p in shown:
        small = p["number"] in crowded
        dot(d, frame.xy(*p["coord"]), 4 if small else 10, INK, 3 if not small else 2 if backed is not None else 0)
    for _, fb in frames:
        if backed is not None:
            d.rectangle((fb[0] - 2, fb[1] - 2, fb[2] + 2, fb[3] + 2), outline=CARD, width=6)
        d.rectangle(fb, outline=MUTED, width=2)
    for p in shown:
        if p["number"] not in crowded:
            q = frame.xy(*p["coord"])
            place_label(d, (q[0] - 10, q[1] - 10, q[0] + 10, q[1] + 10),
                        "、".join(str(n) for n in p.get("labels", [p["number"]])), INK, content, taken, backed)
    for c, fb in frames:
        place_label(d, fb, c["label"], MUTED, content, taken, backed)
    if main:
        items = [("dash", BROWN, "步行"), ("line", BROWN, "其它交通")]
        if any(link.get("pending") for link in links):
            items.append(("line", RED, "待确认"))
        legend(d, box[0] + 24, box[3] - 46, items)


# ======================================================================================================== §4.1 延时总图
# Proxies scaled to a long side of 1620 px in grey, phase correlation with parabolic sub-pixel peaks; every number
# shown is in original pixels.
MEASURE_SIDE, WARN_PX, STD_WARN, INVALID_PX = 1620, 5.0, 0.3, 100.0
END_FRAMES = 12                  # frames at each end looked at for flags


def load_grey(path: str | None) -> Any:
    """The proxy in grey at the measuring size; None for a frame with no readable proxy."""
    import cv2
    import numpy as np

    try:
        data = np.fromfile(str(path), dtype=np.uint8) if path else None
    except OSError:
        data = None
    a = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data is not None and data.size else None
    if a is None:
        return None
    s = MEASURE_SIDE / max(a.shape)
    if s != 1:
        a = cv2.resize(a, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return a.astype(np.float32)


def spectrum(a: Any) -> Any:
    import numpy as np

    return np.fft.fft2(a - a.mean())


def displacement(fa: Any, fb: Any) -> float:
    """Whole-image displacement between two frames given their spectra (the reference code's shift())."""
    import numpy as np

    f = fa * np.conj(fb)
    r = np.fft.ifft2(f / (np.abs(f) + 1e-9)).real
    y, x = np.unravel_index(np.argmax(r), r.shape)
    h, w = r.shape

    def para(m: float, c: float, p: float) -> float:
        k = m - 2 * c + p
        return 0.0 if k == 0 else 0.5 * (m - p) / k

    dy = para(r[(y - 1) % h, x], r[y, x], r[(y + 1) % h, x])
    dx = para(r[y, (x - 1) % w], r[y, x], r[y, (x + 1) % w])
    y = y - h if y > h // 2 else y
    x = x - w if x > w // 2 else x
    return float(np.hypot(x + dx, y + dy))


SPECTRA_HELD = 4


def measure_time_lapse(frames: Sequence[dict[str, Any]], original_long_side: int) -> dict[str, Any]:
    """frames: in capture order, each {"number", "proxy"}. Returns each frame's offset to its inner neighbour, the
    jitter measures, warnings, the end comparison and the frames flagged near the ends, in original pixels. A frame
    with no readable proxy is skipped and listed under "missing"."""
    import numpy as np

    k = original_long_side / MEASURE_SIDE
    n = len(frames)
    if n < 2:
        raise ImageInputError("a time-lapse needs at least two frames")
    half = n // 2
    readable: dict[int, bool] = {}
    held: dict[int, Any] = {}
    bright: dict[int, float] = {}

    def spectrum_of(i: int) -> Any:
        """A spectrum takes about 14 MB: only the last few stay in memory."""
        if i not in held and readable.get(i, True):
            a = load_grey(frames[i].get("proxy"))
            readable[i] = a is not None
            if a is not None:
                bright[i] = float(a.mean())
                held[i] = spectrum(a)
                while len(held) > SPECTRA_HELD:
                    del held[next(iter(held))]
        return held.get(i)

    def neighbour(i: int) -> int | None:
        step = 1 if i < half else -1
        j = i + step
        while 0 <= j < n and spectrum_of(j) is None:
            j += step
        return j if 0 <= j < n else None

    raw: list[float | None] = []
    compared: list[Any] = []
    for i in range(n):
        own = spectrum_of(i)
        j = neighbour(i) if own is not None else None
        raw.append(displacement(own, spectrum_of(j)) if j is not None else None)
        compared.append(frames[j]["number"] if j is not None else None)
    offsets = [round(v * k, 4) if v is not None else None for v in raw]
    sequence = [(offsets[i], frames[i]["number"]) for i in range(n)]
    valid = sorted(v for v, _ in sequence if v is not None and v <= INVALID_PX)
    kept = valid[:len(valid) - max(1, len(valid) // 100)]

    flags: list[list[Any]] = []
    for ends in (range(0, min(END_FRAMES, half)), range(n - 1, max(n - 1 - END_FRAMES, half - 1), -1)):
        for i in ends:
            step = 1 if i < half else -1
            inner = [raw[j] for j in (i + step, i + 2 * step) if 0 <= j < n and raw[j] is not None]
            if raw[i] is not None and raw[i] >= 1 and raw[i] >= 2 * max(inner, default=0):
                flags.append([frames[i]["number"], f"和 {compared[i]} 错开 {offsets[i]:.1f} 像素"])
                continue
            j = i + step
            if i in bright and j in bright and bright[j]:
                jump = (bright[i] - bright[j]) / bright[j]
                near = [abs(bright[a + step] - bright[a]) / bright[a] for a in (j, j + step, j + 2 * step)
                        if a in bright and a + step in bright and bright[a]]
                if abs(jump) >= 0.02 and abs(bright[i] - bright[j]) >= 1 and abs(jump) > 2 * max(near, default=0):
                    flags.append([frames[i]["number"], f"亮度变化 {jump * 100:+.1f}%"])

    first_last = None
    if readable.get(0) and readable.get(n - 1):
        first_last = round(displacement(spectrum_of(0), spectrum_of(n - 1)) * k, 4)
    return {"offsets": offsets, "compared": compared,
            "missing": [f["number"] for i, f in enumerate(frames) if not readable.get(i)],
            "median": round(float(np.median(kept)), 4) if kept else None,
            "std": round(float(np.std(kept)), 4) if kept else None,
            "peaks": [[num, v] for v, num in sorted(sequence, key=lambda t: -(t[0] or 0)) if v is not None][:4],
            "single_warnings": [[num, v] for v, num in sequence if v is not None and v > WARN_PX],
            "whole_warning": bool(kept) and float(np.std(kept)) >= STD_WARN,
            "first_last": first_last, "flags": flags}


def prepare_frames(data: dict[str, Any], m: dict[str, Any]) -> list[dict[str, Any]]:
    """The frames with their roles: what the AI excluded, the first and last frame left, and the flagged frames it has
    not settled yet."""
    excluded = {str(n) for n in data.get("exclude", [])}
    settled = excluded | {str(n) for n in data.get("reviewed", [])}
    flagged = {str(n) for n, _ in m.get("flags", [])} - settled
    frames = [dict(f) for f in data["frames"]]
    kept = [i for i, f in enumerate(frames) if str(f["number"]) not in excluded]
    if len(kept) < 2:
        raise ImageInputError("fewer than two frames are left in the time-lapse")
    for i, f in enumerate(frames):
        f["role"] = "edge" if i not in kept else "first" if i == kept[0] else "last" if i == kept[-1] else "tl"
        f["judge"] = str(f["number"]) in flagged
    return frames


def frame_state(f: dict[str, Any], warned: set[str], leading: bool) -> tuple[str, str, bool]:
    """§4.1: the state word under a frame, its colour and weight."""
    if f.get("judge"):
        return "待判断", RED, True
    if f["number"] in warned:
        return "告警", INK, True
    return {"edge": ("已排除", MUTED, False), "first": ("延时首帧", ENDS, True), "last": ("延时末帧", ENDS, True),
            "tl": ("延时帧", INK, False)}[f["role"]]


def time_lapse_rows(frames: Sequence[dict[str, Any]], warned: set[str]) -> list[tuple[str, list[int]]]:
    """§4.1: an opening row and a closing row of up to six frames (excluded frames, the first or last frame, every
    frame to judge, then time-lapse frames to fill), and a middle row around each single warning."""
    roles = [f["role"] for f in frames]
    first, last = roles.index("first"), roles.index("last")

    def end_row(must: list[int], fill: Sequence[int]) -> list[int]:
        chosen = list(must)
        for i in fill:
            if len(chosen) >= 6:
                break
            if i not in chosen:
                chosen.append(i)
        return sorted(chosen)

    judged = [i for i in range(first + 1, last) if frames[i].get("judge")]     # each goes with the nearer end
    opening = end_row([i for i in range(first + 1) if roles[i] != "edge" or frames[i].get("judge")
                       or i == first - 1] + [i for i in judged if i - first <= last - i], range(first + 1, len(frames)))
    closing = end_row([i for i in range(last, len(frames)) if roles[i] != "edge" or frames[i].get("judge")
                       or i == last + 1] + [i for i in judged if i - first > last - i], range(last - 1, -1, -1))
    rows = [("开头", opening)]
    shown = set(opening) | set(closing)
    spots = [i for i, f in enumerate(frames) if f["number"] in warned and i not in shown]
    around = sorted({k for i in spots for k in range(max(0, i - 2), min(len(frames), i + 3))})
    rows += [("中段", run) for run in _consecutive(around)]
    rows.append(("结尾", closing))
    return [(label if k == 0 else "", idx[k:k + 6]) for label, idx in rows for k in range(0, len(idx), 6)]


TL_FRAME_W, TL_FRAME_H, TL_GAP = 330, 220, 22


def draw_time_lapse(data: dict[str, Any]) -> Image.Image:
    """One time-lapse's summary image (§4.1). The frames are measured here unless a measurement is given."""
    m = data.get("measurement") or measure_time_lapse(data["frames"], data["original_long_side"])
    frames = prepare_frames(data, m)
    if len(m["offsets"]) != len(frames):
        raise ImageInputError(f"the measurement has {len(m['offsets'])} offsets for {len(frames)} frames")
    warned = {n for n, _ in m["single_warnings"]}
    rows = time_lapse_rows(frames, warned)
    notes = {i: frames[i].get("note") for _, idx in rows for i in idx}
    compared = m["compared"]
    for i in notes:
        if notes[i] is None and frames[i]["number"] in warned and compared[i] is not None and m["offsets"][i] is not None:
            notes[i] = f"和 {compared[i]} 错开 {m['offsets'][i]:.1f} 像素"
    row_h = TEXT + 16 + TL_FRAME_H + 14 + TEXT + 10 + note_height(notes.values(), TL_FRAME_W) + 10 + 16
    left_x1 = X0 + PAD + 6 * TL_FRAME_W + 5 * TL_GAP + PAD
    right_x0 = left_x1 + GAP
    inner = X1 - right_x0 - 2 * PAD
    card_lines = time_lapse_card(data, m, frames, inner)
    right_h = PAD + sum(h for _, h in card_lines) + PAD
    top_h = max(PAD + len(rows) * row_h + PAD - 16, right_h)
    plot_y = TOP + top_h + GAP
    img, d = new_page(plot_y + 500 + 48)
    header(d, f"延时 {data['sequence_id']}　{data['place_name']}", time_lapse_summary(data, frames), data.get("page"))

    card(d, (X0, TOP, left_x1, TOP + top_h))
    first_index = [f["role"] for f in frames].index("first")
    y = TOP + PAD
    for label, idx in rows:
        d.text((X0 + PAD, y), label, fill=INK, font=font(TEXT, True))
        x = X0 + PAD
        for i in idx:
            f = frames[i]
            top = y + TEXT + 16
            place_thumbnail(img, d, (x, top, x + TL_FRAME_W, top + TL_FRAME_H), f.get("proxy"))
            state, colour, bold = frame_state(f, warned, i <= first_index)
            draw_runs(d, x, top + TL_FRAME_H + 14, [plain(str(f["number"]), INK, True), plain("  "),
                                                     plain(state, colour, bold)])
            if notes[i]:
                paragraph(d, x, top + TL_FRAME_H + 14 + TEXT + 10, notes[i], MUTED, TL_FRAME_W)
            x += TL_FRAME_W + TL_GAP
        y += row_h

    card(d, (right_x0, TOP, X1, TOP + top_h))
    y = TOP + PAD
    for draw_line, h in card_lines:
        draw_line(d, right_x0 + PAD, y)
        y += h

    draw_jitter_plot(d, (X0, plot_y, X1, plot_y + 500), frames, m["offsets"], warned)
    return img


def time_lapse_summary(data: dict[str, Any], frames: Sequence[dict[str, Any]]) -> str:
    """§4.1: date, camera, focal length, interval and the frames kept, every pixel being of the original."""
    kept = [f for f in frames if f["role"] != "edge"]
    bits = [kept[0]["time"][:10], data.get("camera"), f"{data['focal_mm']} mm" if data.get("focal_mm") else None,
            f"拍摄间隔 {data['interval']} s" if data.get("interval") else None,
            f"{kept[0]['number']}～{kept[-1]['number']} 共 {len(kept)} 张",
            f"像素都按原片（长边 {data['original_long_side']}）"]
    return "　".join(b for b in bits if b)


def time_lapse_card(data: dict[str, Any], m: dict[str, Any], frames: Sequence[dict[str, Any]],
                    inner: float) -> list[tuple[Callable[[ImageDraw.ImageDraw, float, float], None], float]]:
    """The right card of §4.1 as (draw, height) lines: what to look at, the jitter measures, the warning sentence,
    the end comparison and the conclusion."""
    lines: list[tuple[Callable[[ImageDraw.ImageDraw, float, float], None], float]] = []

    def heading(text: str, colour: str) -> None:
        lines.append((lambda d, x, y: d.text((x, y), text, fill=colour, font=font(TEXT, True)), TEXT + 16))

    def para_(text: str, colour: str | None = None, bold: bool = False) -> None:
        n = len(wrap(text, font(TEXT, bold), inner))
        lines.append((lambda d, x, y: paragraph(d, x, y, text, colour or INK, inner, TEXT, bold), text_height(n) + 12))

    def space() -> None:
        lines.append((lambda d, x, y: None, 18))

    files = {f["number"]: f.get("file", f["number"]) for f in frames}
    open_flags = [(n, why) for n, why in m.get("flags", []) if any(str(f["number"]) == str(n) and f["judge"] for f in frames)]
    if open_flags:
        heading("待判断", RED)
        for n, why in open_flags:
            para_(f"{n}：{why}")
        space()
    heading("整段抖动", INK)
    para_(f"抖动中位数 {m['median']:.4f}　抖动标准差 {m['std']:.4f}" if m["median"] is not None and m["std"] is not None
          else "抖动中位数、抖动标准差：未测得")
    para_("抖动峰值" if m["peaks"] else "抖动峰值：未测得", MUTED)
    for number, value in m["peaks"]:
        para_(f"{files.get(number, number)}, {value:.4f}" if value is not None else f"{files.get(number, number)}, 读数失效")
    against = dict(zip((f["number"] for f in frames), m["compared"]))
    warnings = [f"{files.get(n, n)} 和 {files.get(against[n], against[n])} 错开 {v:.1f} 像素" for n, v in m["single_warnings"]]
    if warnings or m["whole_warning"]:
        sentence = ("告警：" + "、".join(warnings) if warnings else "") + \
                   (("；" if warnings else "") + "整段明显抖动（标准差 0.3 以上）" if m["whole_warning"] else "")
        para_(sentence, INK, True)
    if m.get("first_last") is not None:
        para_(f"首末比较：延时首帧和延时末帧差 {m['first_last']:.1f} 像素")
    if data.get("conclusion"):
        space()
        heading("结论", INK)
        for t in data["conclusion"]:
            para_(t)
    return lines


def draw_jitter_plot(d: ImageDraw.ImageDraw, box: Sequence[float], frames: Sequence[dict[str, Any]],
                     offsets: Sequence[float | None], warned: set[str]) -> None:
    """§4.1: each frame's offset to its neighbour against capture time, log scale 0.1–20 px, marks for the first and
    last frames, frames to judge, single warnings and clear jumps under the warning line."""
    card(d, box)
    x0, y0, x1, y1 = box[0] + 96, box[1] + 48, box[2] - 96, box[1] + 48 + 340
    times = [datetime.fromisoformat(f["time"]) for f in frames]
    t0, t1 = times[0], times[-1]
    span_s = max((t1 - t0).total_seconds(), 1.0)
    px = lambda t: x0 + (t - t0).total_seconds() / span_s * (x1 - x0)
    lo, hi = math.log10(0.1), math.log10(20)
    py = lambda v: y1 - (math.log10(min(max(v, 0.1), 20)) - lo) / (hi - lo) * (y1 - y0)
    cap = font(CAPTION)
    for v in (0.1, 1, 10):
        d.line((x0, py(v), x1, py(v)), fill=LINE, width=1)
        d.text((x0 - 16 - width(f"{v:g}", cap), py(v) - 16), f"{v:g}", fill=MUTED, font=cap)
    d.text((box[0] + 24, box[1] + 12), "像素", fill=MUTED, font=cap)
    def tick_count(step: int) -> int:
        day = t0.replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = (t0 - day).total_seconds() / 60, (t1 - day).total_seconds() / 60
        return math.floor(end / step) - math.ceil(start / step) + 1

    step = next((m for m in (1, 2, 5, 10, 15, 30, 60, 120, 240) if tick_count(m) <= 8), 240)
    tick = t0.replace(second=0, microsecond=0)
    tick -= timedelta(minutes=(tick.hour * 60 + tick.minute) % step)
    while tick <= t1:
        if tick >= t0:
            d.line((px(tick), y1, px(tick), y1 + 8), fill=MUTED, width=1)
            label = tick.strftime("%H:%M")
            d.text((px(tick) - width(label, cap) / 2, y1 + 14), label, fill=MUTED, font=cap)
        tick += timedelta(minutes=step)
    dashed(d, [(x0, py(WARN_PX)), (x1, py(WARN_PX))], MUTED, 1, 8, 6)
    d.text((x1 - 24, py(WARN_PX) - 32), "5", fill=MUTED, font=cap)
    pts = [(px(t), py(v)) if v is not None else None for t, v in zip(times, offsets)]
    for p in pts:
        if p:
            dot(d, p, 3, MUTED)
    for p, v in zip(pts, offsets):
        if p and v > 20:
            d.text((p[0] + 10, p[1] + 6), f"{v:.1f}", fill=MUTED, font=cap, halo=CARD)
    roles = [f["role"] for f in frames]
    first, last = roles.index("first"), roles.index("last")
    for i in range(first + 2, last - 1):
        v, near = offsets[i], [offsets[j] for j in (i - 2, i - 1, i + 1, i + 2) if offsets[j] is not None]
        if v is not None and 1.0 <= v <= WARN_PX and near and v >= 2 * max(near) and frames[i]["number"] not in warned:
            d.text((pts[i][0] + 10, pts[i][1] - 16), str(frames[i]["number"]), fill=MUTED, font=cap, halo=CARD)
    for i, f in enumerate(frames):
        if pts[i] and f["number"] in warned and not f.get("judge"):
            dot(d, pts[i], 6, INK, 2)
            d.text((pts[i][0] + 12, pts[i][1] - 16), str(f["number"]), fill=INK, font=font(CAPTION, True), halo=CARD)
    judged = [i for i, f in enumerate(frames) if f.get("judge") and pts[i]]
    for i in judged:
        dot(d, pts[i], 6, RED, 2)
    for run in _consecutive(judged):
        label = str(frames[run[0]]["number"]) + (f"～{frames[run[-1]]['number']}" if len(run) > 1 else "")
        w = width(label, font(CAPTION, True))
        at = pts[run[0]][0] - 12 - w if pts[run[0]][0] - 12 - w > x0 else pts[run[-1]][0] + 12
        d.text((at, pts[run[0]][1] - 16), label, fill=RED, font=font(CAPTION, True), halo=CARD)
    for end, outward in ((first, -1), (last, 1)):
        if not pts[end] or frames[end].get("judge"):
            continue
        dot(d, pts[end], 7, ENDS, 2)
        label = str(frames[end]["number"])
        w = width(label, font(CAPTION, True))
        lx = pts[end][0] + 14 if outward < 0 else pts[end][0] - 14 - w
        d.text((lx, pts[end][1] - 16), label, fill=ENDS, font=font(CAPTION, True), halo=CARD)


def _consecutive(indices: Sequence[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i in indices:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def small_map(img: Image.Image, d: ImageDraw.ImageDraw, box: Sequence[float], m: dict[str, Any],
              fill: str | None = None, zoom: float = 1.0) -> None:
    """A map cell (§1.3): OSM objects, phone fixes, the coordinate, a scale bar; everything is cut at the edge of the
    cell. zoom > 1 frames a smaller area around the same centre, zoom < 1 a larger one."""
    coords = [p["coord"] for p in m.get("points", [])] + [c for o in m.get("objects", []) for c in o] + list(m.get("phone", []))
    if not coords:
        raise ImageInputError("a map without anything to draw")
    w, h = int(box[2] - box[0]), int(box[3] - box[1])
    layer = blank(w, h, fill or PAGE)
    frame = MapFrame((0, 0, w, h), coords, margin=min(72, w / 2.5, h / 2.5))
    frame.scale = min(frame.scale * zoom, MAX_SCALE)
    north, west = frame.latlon(0, 0)
    south, east = frame.latlon(w, h)
    MAP_EXTENTS.append({"box": [round(v) for v in box], "north": round(north, 6), "south": round(south, 6),
                        "west": round(west, 6), "east": round(east, 6)})
    source = m.get("basemap")
    tiles = basemap(frame, (w, h), source, fill or PAGE) if source else None
    if tiles is not None:
        layer.paste(tiles)
    ld = SmoothDraw(layer)
    backed = layer if tiles is not None else None   # over a basemap, lines get a card-coloured halo, words a patch
    taken: list[tuple[float, float, float, float]] = []
    edges: list[tuple[float, float, float, float]] = []   # the scale bar keeps off these; words may stand next to them
    for o in m.get("objects", []):
        polygon = [frame.xy(*c) for c in o]
        if backed is not None:
            ld.polygon(polygon, outline=CARD, width=7)
        ld.polygon(polygon, outline=INK, width=3)
        edges += along(polygon + polygon[:1])
    for c in m.get("phone", []):
        q = frame.xy(*c)
        dot(ld, q, 5, BLUE, 2 if backed is not None else 0)
        edges.append((q[0] - 7, q[1] - 7, q[0] + 7, q[1] + 7))
    for p in m.get("points", []):
        q = frame.xy(*p["coord"])
        dot(ld, q, 10, INK, 3)
        taken.append((q[0] - 13, q[1] - 13, q[0] + 13, q[1] + 13))
    if backed is not None:
        mark, f = TILE_SOURCES[source][3], font(CAPTION)
        mx = w - 24 - width(mark, f)
        edges.append((mx - 8, h - 50, w - 16, h - 16))
    scale = scale_place(frame, taken + edges)
    taken.append(scale[0])
    if backed is not None:
        patch(layer, (mx - 8, h - 50, w - 16, h - 16))
        ld.text((mx, h - 46), mark, fill=MUTED, font=f)
    scale_bar(ld, scale, backed, fill or PAGE)
    put(img, layer, box[0], box[1])
    d.rectangle((box[0], box[1], box[0] + w, box[1] + h), outline=LINE, width=2)


# ======================================================================================================== §3.2 分界图
BOUNDARY_MID = 760
BOUNDARY_CARD, BOUNDARY_NAMES = 416, 60     # the names take the top row of the card, so that any name fits
BOUNDARY_LINES = 2                          # lines of description and file name the card holds
THUMB_H = 170


def file_offsets(f: dict[str, Any], boundary: str, nearest: bool) -> list[Run]:
    """§3.2: how far a file is from the boundary. A photo: one time. A video: its start and end, the end nearer the
    boundary bold (the end before the boundary, the start after it); a video running across the boundary sits before
    it and the part past the boundary, its end, is red. The nearest file of a side is in the main ink, the others in
    the secondary colour."""
    a = from_boundary(f["time"], boundary)
    colour = INK if nearest else MUTED
    if not f.get("end"):
        known = [plain(duration(abs(a)), colour, nearest)]
        return known + [plain("　结束未知", MUTED)] if f.get("video") else known
    e = from_boundary(f["end"], boundary)
    if e <= 0:
        return [plain(duration(-a), colour), plain("　"), plain(duration(-e), colour, True)]
    if a >= 0:
        return [plain(duration(a), colour, True), plain("　"), plain(duration(e), colour)]
    return [plain(duration(-a), colour), plain("　"), plain(duration(e), RED, True)]


def clock_seconds(t: str) -> int:
    parts = [int(x) for x in t.split(":")] + [0, 0]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def clock_of(t: str, show_offset: bool = False) -> str:
    """The clock a page shows. A full time's date is for the arithmetic only; its offset is written on a page with
    several offsets."""
    match = re.search(r"T(\d{2}:\d{2}(?::\d{2})?)", t)
    if match is None:
        return t
    offset = datetime.fromisoformat(t).strftime("%z") if show_offset else ""
    return f"{match.group(1)} UTC{offset[:3]}:{offset[3:5]}" if offset else match.group(1)


def several_offsets(times: Iterable[str]) -> bool:
    """Whether the full times of a page carry more than one UTC offset."""
    return len({datetime.fromisoformat(t).utcoffset() for t in times if "T" in t}) > 1


def offset_lines(runs: Sequence[Run], limit: float) -> list[list[Run]]:
    """§3.2: a video's two times side by side, on two lines when they do not fit."""
    if runs_width(runs) <= limit:
        return [list(runs)]
    return [[(text.strip("　"), colour, face)] for text, colour, face in runs if text.strip("　")]


def from_boundary(t: str, boundary: str) -> int:
    """Seconds from a boundary to a time, negative before it. Clock times are of one day and subtract directly; a row
    across midnight writes every time in full, with its date and UTC offset, and those subtract as instants."""
    if ("T" in t) != ("T" in boundary):
        raise ImageInputError(f"boundary {boundary}: {t} mixes a clock time with a full time")
    if "T" not in t:
        return clock_seconds(t) - clock_seconds(boundary)
    try:
        return int((datetime.fromisoformat(t) - datetime.fromisoformat(boundary)).total_seconds())
    except (TypeError, ValueError) as error:
        raise ImageInputError(f"boundary {boundary}: {t} needs a date and a UTC offset") from error


def side_runs(side: dict[str, Any] | None, limit: float, what: str) -> list[Run]:
    if side is None:
        return []
    if side["kind"] == "stay":
        return fitted_name(side["name"], limit, what)
    mode, _, service = side["mode"].partition(" ")
    return [plain(mode, BROWN, True)] + ([plain(f" {service}", INK, True)] if service else [])


def draw_boundaries(data: dict[str, Any]) -> Image.Image:
    """A page of up to five boundaries (§3.2): the boundary time in the middle, the segments on either side, the
    nearest three files of each of the two segments."""
    rows = data["rows"]
    if sum(1 for r in rows if r.get("after") is not None) > 5 or len(rows) > 6:
        raise ImageInputError("a boundary page holds at most five boundaries and the line ending the day")
    mid = W / 2
    half = (X1 - X0 - BOUNDARY_MID) / 2
    cell = (half - 24) / 3
    small = font(CAPTION)
    placed = [(r, f) for r in rows for side in ("files_before", "files_after") for f in r.get(side, [])]
    show_offset = several_offsets([r["time"] for r in rows] + [f[k] for _, f in placed for k in ("time", "end") if f.get(k)])

    def stamp_of(f: dict[str, Any]) -> str:
        start = clock_of(f["time"], show_offset)
        return span(start, clock_of(f["end"], show_offset)) if f.get("end") else start

    extra = [
        (len(wrap(f.get("desc", ""), small, cell - 24)) + len(wrap(f["name"], small, cell - 24))
         + len(wrap(stamp_of(f), small, cell - 24)) - 1 - BOUNDARY_LINES) * (CAPTION + LEAD)
        + (len(offset_lines(file_offsets(f, r["time"], True), cell - 24)) - 1) * (TEXT + 10)
        for r, f in placed
    ]
    card_h = BOUNDARY_CARD + max(0, max(extra, default=0))
    img, d = new_page(TOP + len(rows) * (card_h + 20) - 20 + 48)
    header(d, data["title"], data.get("summary"), data.get("page"))
    y = TOP
    for r in rows:
        box = (X0, y, X1, y + card_h)
        card(d, box)
        before_bg = STAY_BG if r["before"]["kind"] == "stay" else MOVE_BG
        after_bg = CARD if r.get("after") is None else STAY_BG if r["after"]["kind"] == "stay" else MOVE_BG
        d.rectangle((X0 + 2, y + 2, mid, y + card_h - 2), fill=before_bg)
        d.rectangle((mid, y + 2, X1 - 2, y + card_h - 2), fill=after_bg)
        # the middle: time and the two segments
        t_font = font(TITLE, True)
        cy = y + (BOUNDARY_NAMES + card_h) / 2
        top = cy - (TITLE + 22 + TEXT) / 2
        colour = RED if r.get("pending_time") else INK
        shown = clock_of(r["time"], show_offset)
        d.text((mid - width(shown, t_font) / 2, top - 6), shown, fill=colour, font=t_font)
        name_y = y + 16
        limit = (X1 - X0) / 2 - 48
        left = side_runs(r["before"], limit, f"boundary {r['time']}")
        draw_runs(d, mid - 24 - runs_width(left), name_y, left)
        right = side_runs(r.get("after"), limit, f"boundary {r['time']}")
        draw_runs(d, mid + 24, name_y, right)
        if r.get("pending"):
            note = plain("待确认", RED, True)
            draw_runs(d, mid - runs_width([note]) / 2, top + TITLE + 22, [note])
        # files: the nearest ones next to the middle
        for files, side in ((r.get("files_before", []), -1), (r.get("files_after", []), 1)):
            if len(files) > 3:
                raise ImageInputError(f"boundary {r['time']}: more than three files on one side")
            # before the boundary the nearest file is the one that ends last: a video reaches it with its end
            ordered = sorted(files, key=lambda f: from_boundary(f.get("end") or f["time"] if side < 0 else f["time"], r["time"]))
            slots = list(range(3 - len(ordered), 3)) if side < 0 else list(range(len(ordered)))
            near = ordered[-1] if side < 0 and ordered else ordered[0] if ordered else None
            for f, slot in zip(ordered, slots):
                x = (X0 + 24 + slot * cell) if side < 0 else (mid + BOUNDARY_MID / 2 + slot * cell)
                tw = cell - 24
                ty = y + BOUNDARY_NAMES
                place_thumbnail(img, d, (x, ty, x + tw, ty + THUMB_H), f.get("proxy"), bool(f.get("end") or f.get("video")))
                yy = ty + THUMB_H + 12
                for line in offset_lines(file_offsets(f, r["time"], f is near), tw):
                    draw_runs(d, x, yy, line)
                    yy += TEXT + 10
                yy = paragraph(d, x, yy, f.get("desc", ""), INK, tw, CAPTION) + 8
                yy = paragraph(d, x, yy, f["name"], MUTED, tw, CAPTION)
                paragraph(d, x, yy + LEAD, stamp_of(f), MUTED, tw, CAPTION)
        y += card_h + 20
    return img


# ======================================================================================================== §4.2 延时判断图
JUDGE_LEFT_W = 1860
JUDGE_FRAME_W, JUDGE_FRAME_H, JUDGE_GAP = 576, 384, 34
ZOOM_W, ZOOM_H, ZOOM_GAP = 880, 496, 36


def zoom_crop(img: Image.Image, d: ImageDraw.ImageDraw, box: Sequence[float], path: str | None,
              centre: Sequence[float]) -> None:
    """800 %: one proxy pixel drawn as eight, around centre (fractions of the frame)."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    cw, ch = (x1 - x0) / 8, (y1 - y0) / 8
    try:
        if not path:
            raise FileNotFoundError(path)
        with Image.open(path) as src:
            src = src.convert("RGB")
            cx, cy = centre[0] * src.width, centre[1] * src.height
            part = src.crop((round(cx - cw / 2), round(cy - ch / 2), round(cx + cw / 2), round(cy + ch / 2)))
            put(img, part.resize(((x1 - x0) * SCALE, (y1 - y0) * SCALE), Image.NEAREST), x0, y0)
    except OSError:
        d.rectangle((x0, y0, x1, y1), fill=LINE)


def proxy_size(path: str | None) -> tuple[int, int] | None:
    try:
        if not path:
            return None
        with Image.open(path) as src:
            return src.size
    except OSError:
        return None


def draw_time_lapse_judge(data: dict[str, Any]) -> Image.Image:
    """One place to judge in a time-lapse (§4.2): the frames in question with the two inner ones, two 800 % crops of the
    same spot, and a note."""
    frames = [{**f, "role": "edge" if f.get("excluded") else "tl", "judge": bool(f.get("judge"))} for f in data["frames"]]
    leading = True
    rows = [frames[k:k + 3] for k in range(0, len(frames), 3)]
    row_h = JUDGE_FRAME_H + 12 + TEXT + 10 + note_height((f.get("note") for f in frames), JUDGE_FRAME_W) + 24
    left_h = PAD + len(rows) * row_h + 16 + TEXT + 12 + ZOOM_H + 24 + CAPTION + PAD
    right_x0 = X0 + JUDGE_LEFT_W + 32
    inner = X1 - right_x0 - 2 * PAD
    notes = data.get("notes", [])
    notes_h = sum(text_height(len(wrap(t, font(TEXT), inner))) + 16 for t in notes)
    h = max(left_h, PAD + TEXT + 18 + notes_h + PAD)
    img, d = new_page(TOP + h + 48)
    header(d, f"延时 {data['sequence_id']}　{data['place_name']}　待判断：{data['judge_label']}", data.get("summary"), data.get("page"))
    card(d, (X0, TOP, X0 + JUDGE_LEFT_W, TOP + h))
    zoom = data.get("zoom") or {"numbers": [frames[0]["number"], frames[1]["number"]], "centre": [0.5, 0.5]}
    centre = zoom["centre"]
    fw, fh = JUDGE_FRAME_W, JUDGE_FRAME_H
    marks = {}
    y = TOP + PAD
    for row in rows:
        x = X0 + 32
        for f in row:
            place_thumbnail(img, d, (x, y, x + fw, y + fh), f.get("proxy"))
            state, colour, bold = frame_state(f, set(), leading)
            draw_runs(d, x, y + fh + 12, [plain(str(f["number"]), INK, True), plain("  "), plain(state, colour, bold)])
            if f.get("note"):
                paragraph(d, x, y + fh + 12 + TEXT + 10, f["note"], MUTED, fw)
            marks[str(f["number"])] = (x, y)
            x += fw + JUDGE_GAP
        y += row_h
    # the red frame on the two thumbnails: the part shown at 800 %, through the thumbnail's own scale and centre crop
    by_number = {str(f["number"]): f for f in frames}
    for number in zoom["numbers"]:
        if str(number) in marks:
            mx, my = marks[str(number)]
            sw, sh = proxy_size(by_number[str(number)].get("proxy")) or (MEASURE_SIDE, 1080)
            s = max(fw / sw, fh / sh)
            cx, cy = mx + centre[0] * sw * s - (sw * s - fw) / 2, my + centre[1] * sh * s - (sh * s - fh) / 2
            zw, zh = ZOOM_W / 8 * s, ZOOM_H / 8 * s
            x0, y0 = max(mx, cx - zw / 2), max(my, cy - zh / 2)
            x1, y1 = min(mx + fw, cx + zw / 2), min(my + fh, cy + zh / 2)
            if x0 < x1 and y0 < y1:         # the part shown can lie outside the thumbnail's crop
                d.rectangle((x0, y0, x1, y1), outline=RED, width=3)
    y += 16
    x = X0 + 32
    for number in zoom["numbers"]:
        f = by_number[str(number)]
        state, colour, bold = frame_state(f, set(), leading)
        draw_runs(d, x, y, [plain(str(number), INK, True), plain("  "), plain(state, colour, bold),
                            plain("　放大 800%", MUTED, False, CAPTION)])
        zoom_crop(img, d, (x, y + TEXT + 12, x + ZOOM_W, y + TEXT + 12 + ZOOM_H), f.get("proxy"), centre)
        x += ZOOM_W + ZOOM_GAP
    y += TEXT + 12 + ZOOM_H + 24
    card(d, (right_x0, TOP, X1, TOP + h))
    d.text((right_x0 + PAD, TOP + PAD), "说明", fill=INK, font=font(TEXT, True))
    yy = TOP + PAD + TEXT + 18
    for t in notes:
        yy = paragraph(d, right_x0 + PAD, yy, t, INK, inner) + 16
    return img


# ======================================================================================================== §2.1 地点卡片
CELL_W, CELL_H = 1018, 600

def draw_place_card(data: dict[str, Any]) -> Image.Image:
    """One place (§2.1): street map, satellite image, photos taken there; name, address, a line about it, coordinate."""
    inner = X1 - (X0 + 2 * CELL_W + 48) - 2 * PAD
    right_x0 = X0 + 2 * CELL_W + 48
    regular = font(TEXT)
    lat, lon = data["coordinate"]
    fields = [("地址", data.get("address") or "无"), ("介绍", data.get("about") or "无"), ("坐标", f"{lat:.5f}, {lon:.5f}")]
    blocks = [(label, wrap(v, regular, inner - 110)) for label, v in fields]
    head = [plain(str(data["number"]), MUTED, True), plain("  ")] + fitted_name(data["name"], inner - 120, "place")
    pending = data.get("pending")
    pending_h = text_height(len(wrap(pending, regular, inner))) + 28 if pending else 0
    text_h = TEXT + 30 + pending_h + sum(text_height(len(v)) + 16 for _, v in blocks)
    h = max(2 * CELL_H + 24, PAD + text_h + TEXT + 40 + PAD)
    img, d = new_page(TOP + h + 48)
    header(d, data.get("title") or "地点", None, data.get("page"))
    m = {"points": [{"coord": [lat, lon]}], "objects": data.get("objects", []), "phone": data.get("phone", [])}
    photos = list(data.get("photos", []))[:2]
    cells = [(0, 0, "osm", 1.0), (1, 0, "satellite", 1.25), (0, 1, None, photos[0] if photos else None),
             (1, 1, None, photos[1]) if len(photos) > 1 else (1, 1, "satellite", 0.25)]
    for cx, cy, source, extra in cells:
        box = (X0 + cx * (CELL_W + 24), TOP + cy * (CELL_H + 24), X0 + cx * (CELL_W + 24) + CELL_W,
               TOP + cy * (CELL_H + 24) + CELL_H)
        if source is None:
            place_thumbnail(img, d, box, extra)
            d.rectangle(box, outline=LINE, width=2)
        else:
            small_map(img, d, box, {**m, "basemap": source}, LINE if source == "satellite" else None, extra)
    card(d, (right_x0, TOP, X1, TOP + h))
    x, y = right_x0 + PAD, TOP + PAD
    draw_runs(d, x, y, head)
    y += TEXT + 30
    if pending:
        y = paragraph(d, x, y, pending, RED, inner, TEXT, True) + 28
    for label, content in blocks:
        d.text((x, y), label, fill=MUTED, font=regular)
        for k, line in enumerate(content):
            d.text((x + 110, y + k * (TEXT + LEAD)), line, fill=INK, font=regular)
        y += text_height(len(content)) + 16
    ly = TOP + h - PAD - TEXT
    dot(d, (x + 10, ly + TEXT / 2 + 4), 10, INK, 3)
    d.text((x + 34, ly), "坐标", fill=MUTED, font=regular)
    x = legend(d, x + 34 + width("坐标", regular) + 44, ly, [("line", INK, "OSM 对象")])
    dot(d, (x + 6, ly + TEXT / 2 + 4), 5, BLUE)
    d.text((x + 24, ly), "手机定位", fill=MUTED, font=regular)
    return img


KINDS: dict[str, Callable[[dict[str, Any]], Image.Image]] = {
    "place_card": draw_place_card,
    "day_overview": draw_day_overview,
    "boundaries": draw_boundaries,
    "time_lapse": draw_time_lapse,
    "time_lapse_judge": draw_time_lapse_judge,
}


def render(kind: str, data: dict[str, Any], out: Path, tiles: Path | None = None) -> Path:
    """Draw one image; with a tile folder, basemaps are read from it and missing tiles fetched into it."""
    global TILE_CACHE
    if kind not in KINDS:
        raise ImageInputError(f"unknown kind {kind!r}; known: {', '.join(KINDS)}")
    TILE_CACHE = tiles
    try:
        img = KINDS[kind](data)
    finally:
        TILE_CACHE = None
    if img.width != OUTPUT_W:
        img = img.resize((OUTPUT_W, round(img.height * OUTPUT_W / img.width)), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):        # a pipe would otherwise use the system's code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("kind", help="image kind; 'measure' measures a time-lapse only; 'kinds' lists the kinds")
    parser.add_argument("input", nargs="?", help="JSON file with the image content")
    parser.add_argument("output", nargs="?", help="PNG file to write ('measure': a JSON file)")
    parser.add_argument("--tiles", help="folder for basemap tiles; without it, maps are drawn on a plain ground and "
                                        "nothing is fetched")
    args = parser.parse_args(argv)
    if args.kind == "kinds":
        print("\n".join(KINDS))
        return 0
    if not args.input or not args.output:
        parser.error("input and output are required")
    try:
        if Path(args.output).exists() and Path(args.output).samefile(args.input):
            raise ImageInputError("the output would overwrite the input")
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if args.kind == "measure":
            out = Path(args.output)
            out.write_text(json.dumps(measure_time_lapse(data["frames"], data["original_long_side"]),
                                      ensure_ascii=False), encoding="utf-8")
        else:
            out = render(args.kind, data, Path(args.output), Path(args.tiles) if args.tiles else None)
    except (ImageInputError, KeyError, ValueError, TypeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    result: dict[str, Any] = {"ok": True, "output": str(out)}
    if WARNINGS:
        result["warnings"] = WARNINGS
    if args.kind == "place_card":
        result["map_extents"] = [{**e, "box": [round(v * OUTPUT_W / W) for v in e["box"]]} for e in MAP_EXTENTS]
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
