#!/usr/bin/env python3
"""VELMARA — travel poster for a place that isn't there.

A 1024x1024 travel poster, drawn from scratch with a small pure-stdlib
rasterizer (no PIL, no network, deterministic). The only text on the
poster is the place name VELMARA, blitted from a bitmap atlas rendered
at build time from the real Bodoni Moda Bold typeface (atlas.png +
atlas.json live next to this file).

Scene: an island cliff at golden hour where the sea falls UP — a
reversed waterfall arcs from the cliff edge into the sky and breaks
into light. Deep teal sea, gold sun, one hero landmark, one small boat.
"""

import json
import math
import os
import struct
import sys
import zlib

W = H = 1024
OUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"

# ---------------------------------------------------------------- palette
CREAM = (251, 243, 220)
GOLD = (232, 180, 76)
GOLD_LIGHT = (247, 215, 116)
GOLD_DEEP = (214, 148, 52)
TEAL_DEEP = (10, 51, 64)
TEAL = (20, 88, 107)
TEAL_MID = (62, 138, 138)
CORAL = (226, 114, 91)
CLIFF = (11, 47, 58)
CLIFF_RIM = (232, 180, 76)
WATER = (240, 248, 244)
SKY_TOP = (32, 88, 106)
SKY_MID = (110, 155, 139)
SKY_LOW = (232, 185, 92)
SKY_HORIZON = (242, 201, 107)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


# ---------------------------------------------------------------- canvas
class Canvas:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.px = bytearray(w * h * 3)

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i] = c[0]
            self.px[i + 1] = c[1]
            self.px[i + 2] = c[2]

    def blend(self, x, y, c, a):
        """Blend colour c over pixel (x, y) with alpha a in 0..1."""
        if 0 <= x < self.w and 0 <= y < self.h and a > 0:
            i = (y * self.w + x) * 3
            ia = 1.0 - a
            self.px[i] = int(self.px[i] * ia + c[0] * a)
            self.px[i + 1] = int(self.px[i + 1] * ia + c[1] * a)
            self.px[i + 2] = int(self.px[i + 2] * ia + c[2] * a)

    def hline(self, x0, x1, y, c):
        if y < 0 or y >= self.h:
            return
        x0 = max(0, x0)
        x1 = min(self.w - 1, x1)
        if x1 < x0:
            return
        i = (y * self.w + x0) * 3
        n = x1 - x0 + 1
        self.px[i:i + n * 3] = bytes(c) * n

    def vgrad(self, y0, y1, stops):
        """Fill rows y0..y1 with a vertical multi-stop gradient.

        stops: list of (t, colour) with t in 0..1, sorted.
        """
        y0 = max(0, y0)
        y1 = min(self.h - 1, y1)
        span = max(1, y1 - y0)
        for y in range(y0, y1 + 1):
            t = (y - y0) / span
            c = grad_at(stops, t)
            self.hline(0, self.w - 1, y, c)

    def fill_poly(self, pts, c):
        """Scanline polygon fill (pts: list of (x, y))."""
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.ceil(min(ys))))
        y1 = min(self.h - 1, int(math.floor(max(ys))))
        n = len(pts)
        for y in range(y0, y1 + 1):
            xs = []
            for i in range(n):
                x1, y1p = pts[i]
                x2, y2p = pts[(i + 1) % n]
                if y1p == y2p:
                    continue
                if (y1p <= y < y2p) or (y2p <= y < y1p):
                    xs.append(x1 + (y - y1p) * (x2 - x1) / (y2p - y1p))
            if len(xs) < 2:
                continue
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                self.hline(int(math.ceil(xs[k])), int(math.floor(xs[k + 1])), y, c)

    def fill_poly_alpha(self, pts, c, a):
        if len(pts) < 3 or a <= 0:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.ceil(min(ys))))
        y1 = min(self.h - 1, int(math.floor(max(ys))))
        n = len(pts)
        for y in range(y0, y1 + 1):
            xs = []
            for i in range(n):
                x1, y1p = pts[i]
                x2, y2p = pts[(i + 1) % n]
                if y1p == y2p:
                    continue
                if (y1p <= y < y2p) or (y2p <= y < y1p):
                    xs.append(x1 + (y - y1p) * (x2 - x1) / (y2p - y1p))
            if len(xs) < 2:
                continue
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                for x in range(max(0, int(math.ceil(xs[k]))),
                               min(self.w - 1, int(math.floor(xs[k + 1]))) + 1):
                    self.blend(x, y, c, a)

    def disc(self, cx, cy, r, c):
        r = int(r)
        for dy in range(-r, r + 1):
            dx = int(math.sqrt(max(0, r * r - dy * dy)))
            self.hline(cx - dx, cx + dx, cy + dy, c)

    def disc_alpha(self, cx, cy, r, c, a):
        r = int(r)
        for dy in range(-r, r + 1):
            dx = int(math.sqrt(max(0, r * r - dy * dy)))
            for x in range(cx - dx, cx + dx + 1):
                self.blend(x, cy + dy, c, a)

    def ellipse(self, cx, cy, rx, ry, c):
        for dy in range(-int(ry), int(ry) + 1):
            t = dy / ry
            dx = int(rx * math.sqrt(max(0.0, 1.0 - t * t)))
            self.hline(cx - dx, cx + dx, cy + dy, c)

    def ellipse_alpha(self, cx, cy, rx, ry, c, a):
        for dy in range(-int(ry), int(ry) + 1):
            t = dy / ry
            dx = int(rx * math.sqrt(max(0.0, 1.0 - t * t)))
            for x in range(cx - dx, cx + dx + 1):
                self.blend(x, cy + dy, c, a)

    def thick_line(self, x0, y0, x1, y1, w, c):
        """A thick line as a filled quad."""
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * w / 2.0, dx / ln * w / 2.0
        self.fill_poly([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
                        (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], c)


def grad_at(stops, t):
    if t <= stops[0][0]:
        return stops[0][1]
    for i in range(1, len(stops)):
        if t <= stops[i][0]:
            t0, c0 = stops[i - 1]
            t1, c1 = stops[i]
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return lerp(c0, c1, f)
    return stops[-1][1]


def bezier(p0, p1, p2, p3, steps):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
             + 3 * mt * t * t * p2[0] + t ** 3 * p3[0])
        y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
             + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])
        pts.append((x, y))
    return pts


def ribbon(cv, pts, w0, w1, c0, c1):
    """Tapered ribbon along a polyline, colour lerped along its length."""
    n = len(pts)
    for i in range(n - 1):
        t0, t1 = i / (n - 1), (i + 1) / (n - 1)
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        a0 = w0 + (w1 - w0) * t0
        a1 = w0 + (w1 - w0) * t1
        ca = lerp(c0, c1, t0)
        cb = lerp(c0, c1, t1)
        # quad split into two triangles approximated by two quads: draw
        # the segment as one quad in the average colour (segments are short).
        cm = lerp(ca, cb, 0.5)
        cv.fill_poly([(x0 + nx * a0 / 2, y0 + ny * a0 / 2),
                      (x1 + nx * a1 / 2, y1 + ny * a1 / 2),
                      (x1 - nx * a1 / 2, y1 - ny * a1 / 2),
                      (x0 - nx * a0 / 2, y0 - ny * a0 / 2)], cm)


# ---------------------------------------------------------------- PNG codec
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_rgba(path):
    """Minimal PNG decoder: 8-bit, non-interlaced, colour types 0/2/4/6."""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    idat = bytearray()
    width = height = bitdepth = ctype = None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            width, height, bitdepth, ctype = struct.unpack(">IIBB", chunk[:10])
            if bitdepth != 8:
                raise ValueError("only 8-bit PNG supported")
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln
    raw = zlib.decompress(bytes(idat))
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    stride = width * ch
    out = bytearray(width * height * 4)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        for x in range(width):
            s = x * ch
            d = (y * width + x) * 4
            if ctype == 6:
                out[d:d + 4] = line[s:s + 4]
            elif ctype == 2:
                out[d:d + 3] = line[s:s + 3]
                out[d + 3] = 255
            elif ctype == 4:
                out[d] = out[d + 1] = out[d + 2] = line[s]
                out[d + 3] = line[s + 1]
            else:
                out[d] = out[d + 1] = out[d + 2] = line[s]
                out[d + 3] = 255
        prev = line
    return width, height, bytes(out)


def encode_png_rgb(cv):
    rows = bytearray()
    for y in range(cv.h):
        rows.append(0)
        rows += cv.px[y * cv.w * 3:(y + 1) * cv.w * 3]
    ihdr = struct.pack(">IIBBBBB", cv.w, cv.h, 8, 2, 0, 0, 0)

    def chunk(typ, payload):
        c = struct.pack(">I", len(payload)) + typ + payload
        return c + struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------- atlas text
def blit_word(cv, atlas_path, meta_path, target_w, cx, top, tint):
    """Blit the VELMARA atlas, bilinearly resampled to target width,
    tinted, centred on cx with its top at `top`."""
    aw, ah, rgba = decode_png_rgba(atlas_path)
    meta = json.load(open(meta_path))
    scale = target_w / meta["width"]
    tw = max(1, int(round(aw * scale)))
    th = max(1, int(round(ah * scale)))
    # Bilinear resample of the whole atlas.
    small = bytearray(tw * th * 4)
    for y in range(th):
        sy = (y + 0.5) / scale - 0.5
        y0 = clamp(int(math.floor(sy)), 0, ah - 1)
        y1 = min(ah - 1, y0 + 1)
        fy = clamp(sy - y0, 0.0, 1.0)
        for x in range(tw):
            sx = (x + 0.5) / scale - 0.5
            x0 = clamp(int(math.floor(sx)), 0, aw - 1)
            x1 = min(aw - 1, x0 + 1)
            fx = clamp(sx - x0, 0.0, 1.0)
            d = (y * tw + x) * 4
            for k in range(4):
                a00 = rgba[(y0 * aw + x0) * 4 + k]
                a10 = rgba[(y0 * aw + x1) * 4 + k]
                a01 = rgba[(y1 * aw + x0) * 4 + k]
                a11 = rgba[(y1 * aw + x1) * 4 + k]
                v = (a00 * (1 - fx) * (1 - fy) + a10 * fx * (1 - fy)
                     + a01 * (1 - fx) * fy + a11 * fx * fy)
                small[d + k] = int(round(v))
    ox = int(round(cx - tw / 2))
    oy = top
    for y in range(th):
        for x in range(tw):
            a = small[(y * tw + x) * 4 + 3] / 255.0
            if a > 0.003:
                cv.blend(ox + x, oy + y, tint, a)
    return tw, th


# ---------------------------------------------------------------- scene
def draw_scene(cv):
    # --- sky: deep teal down to warm gold at the horizon (y=640)
    cv.vgrad(0, 640, [(0.0, SKY_TOP), (0.42, SKY_MID),
                      (0.78, SKY_LOW), (1.0, SKY_HORIZON)])
    # --- sea: light teal at the horizon to deep teal at the bottom
    cv.vgrad(640, H - 1, [(0.0, (94, 164, 150)), (0.35, TEAL_MID),
                           (1.0, TEAL_DEEP)])

    # --- soft clouds: pale gold ellipses, low alpha
    for (cx, cy, rx, ry, a) in [(210, 250, 150, 20, 0.30),
                                (300, 268, 110, 15, 0.25),
                                (880, 130, 130, 16, 0.28),
                                (820, 470, 170, 18, 0.20),
                                (140, 470, 120, 14, 0.22)]:
        cv.ellipse_alpha(cx, cy, rx, ry, (247, 220, 160), a)

    # --- sun: halo + disc, upper right
    sx, sy, sr = 772, 296, 88
    cv.disc_alpha(sx, sy, sr + 46, GOLD_LIGHT, 0.16)
    cv.disc_alpha(sx, sy, sr + 20, GOLD_LIGHT, 0.22)
    cv.disc(sx, sy, sr, GOLD_LIGHT)
    cv.disc(sx, sy, sr - 14, (250, 226, 138))

    # --- sun path on the sea: gold streaks under the sun
    for i, (y, wdt, a) in enumerate([(668, 190, 0.45), (700, 230, 0.38),
                                     (740, 270, 0.32), (790, 310, 0.26),
                                     (850, 360, 0.20), (920, 410, 0.15)]):
        cv.ellipse_alpha(sx, y, wdt // 2, 5, GOLD_LIGHT, a)

    # --- distant headland on the right horizon (keeps the sea from
    #     feeling empty, stays out of the hero's way)
    cv.fill_poly([(1024, 640), (1024, 612), (940, 622), (880, 640)],
                 (16, 68, 82))

    # --- THE LANDMARK: the waterfall that falls UP.
    # From the cliff edge it arcs into the sky and dissolves into light.
    # (Its top stays below the name rule, so no droplets speckle the type.)
    fall = bezier((420, 655), (540, 580), (630, 460), (664, 292), 48)
    ribbon(cv, fall, 40, 7, WATER, (250, 226, 150))
    # bright core thread
    core = bezier((420, 655), (534, 584), (624, 464), (660, 300), 48)
    ribbon(cv, core, 16, 2, (255, 255, 255), (255, 240, 190))
    # droplets breaking off near the top, rising and fading
    drops = [(664, 292, 9), (676, 262, 7), (688, 238, 6), (700, 220, 4),
             (652, 258, 5), (642, 230, 4)]
    for (dx, dy, dr) in drops:
        cv.disc(dx, dy, dr, (255, 250, 225))
    # sparkles where the water becomes sky
    def sparkle(cx, cy, r, c):
        cv.fill_poly([(cx, cy - r), (cx + r // 3, cy - r // 3),
                      (cx + r, cy), (cx + r // 3, cy + r // 3),
                      (cx, cy + r), (cx - r // 3, cy + r // 3),
                      (cx - r, cy), (cx - r // 3, cy - r // 3)], c)

    sparkle(690, 240, 12, (255, 244, 200))
    sparkle(704, 216, 9, (255, 244, 200))
    sparkle(652, 268, 8, (255, 244, 200))
    # faint mist where the fall leaves the cliff
    cv.ellipse_alpha(472, 646, 40, 14, (240, 248, 244), 0.35)
    cv.ellipse_alpha(486, 630, 26, 10, (240, 248, 244), 0.28)

    # --- island cliff (left): flat top, steep face into the sea
    cliff = [(28, 1024), (52, 742), (96, 668), (150, 648), (300, 640),
             (408, 642), (452, 668), (470, 742), (486, 1024)]
    cv.fill_poly(cliff, CLIFF)
    # gold rim light along the cliff top edge
    cv.thick_line(96, 668, 150, 648, 4, CLIFF_RIM)
    cv.thick_line(150, 648, 300, 640, 4, CLIFF_RIM)
    cv.thick_line(300, 640, 408, 642, 4, CLIFF_RIM)
    # a few subtle vertical strata on the face (no floating shapes)
    for (sx0, sy0, sx1, sy1) in [(150, 700, 130, 960), (260, 690, 250, 1000),
                                 (360, 700, 372, 980)]:
        cv.thick_line(sx0, sy0, sx1, sy1, 3, (7, 36, 45))

    # --- palm cluster on the cliff top (silhouette, gold-rimmed)
    def palm(tx, ty, s, lean):
        # trunk: tapered ribbon curving with the lean
        pts = bezier((tx, ty), (tx + lean * 0.4, ty - 34 * s),
                     (tx + lean * 0.8, ty - 62 * s),
                     (tx + lean, ty - 88 * s), 12)
        ribbon(cv, pts, 9 * s, 5 * s, CLIFF, CLIFF)
        hx, hy = tx + lean, ty - 88 * s
        # fronds: arcs fanning from the crown
        for ang in (-165, -125, -85, -45, -10, 25):
            r = math.radians(ang)
            ex = hx + math.cos(r) * 46 * s
            ey = hy + math.sin(r) * 30 * s - 6 * s
            mx = hx + math.cos(r) * 26 * s
            my = hy + math.sin(r) * 16 * s - 16 * s
            cv.thick_line(int(hx), int(hy), int(mx), int(my), 5, CLIFF)
            cv.thick_line(int(mx), int(my), int(ex), int(ey), 4, CLIFF)
        cv.disc(int(hx), int(hy), int(6 * s), CLIFF)

    palm(150, 652, 1.0, -14)
    palm(215, 646, 0.85, 10)
    palm(268, 644, 0.7, -8)

    # --- birds
    def bird(bx, by, s):
        cv.thick_line(bx - 12 * s, by, bx, by - 7 * s, 3, CLIFF)
        cv.thick_line(bx, by - 7 * s, bx + 12 * s, by, 3, CLIFF)

    bird(180, 330, 1)
    bird(250, 300, 1)
    bird(310, 350, 1)

    # --- small sailboat on the sea (the promise of a stay)
    bx, by = 690, 812
    cv.fill_poly([(bx - 34, by), (bx + 34, by), (bx + 22, by + 16),
                  (bx - 22, by + 16)], CLIFF)
    cv.fill_poly([(bx, by - 58), (bx, by - 2), (bx + 30, by - 2)], CREAM)
    cv.fill_poly([(bx - 2, by - 46), (bx - 2, by - 2), (bx - 24, by - 2)],
                 CORAL)
    cv.thick_line(bx, by - 60, bx, by, 3, CLIFF)
    # reflection
    cv.ellipse_alpha(bx, by + 26, 40, 5, GOLD_LIGHT, 0.35)

    # --- name: VELMARA, big, top centre, from the real-type atlas
    here = os.path.dirname(os.path.abspath(__file__))
    tw, th = blit_word(cv, os.path.join(here, "atlas.png"),
                       os.path.join(here, "atlas.json"),
                       800, W // 2, 66, CREAM)
    # thin gold rule under the name, with a small diamond at its centre
    ry = 66 + th + 22
    cv.hline(W // 2 - 400, W // 2 + 400, ry, GOLD)
    cv.hline(W // 2 - 400, W // 2 + 400, ry + 1, GOLD)
    sparkle(W // 2, ry, 9, GOLD)

    # --- classic poster frame
    m = 26
    cv.thick_line(m, m, W - m, m, 3, CREAM)
    cv.thick_line(m, H - m, W - m, H - m, 3, CREAM)
    cv.thick_line(m, m, m, H - m, 3, CREAM)
    cv.thick_line(W - m, m, W - m, H - m, 3, CREAM)


def main():
    cv = Canvas(W, H)
    draw_scene(cv)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "entry.png")
    with open(path, "wb") as f:
        f.write(encode_png_rgb(cv))
    print("wrote", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
