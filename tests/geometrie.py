#!/usr/bin/env python3
"""tests/geometrie.py — computed geometry of every SVG the generator draws.

The fourth blind spot named on 2026-09-06: every design fault in this
project so far was found by looking at a screenshot. Nothing in the suite
drew anything, so nothing could see that a bar was full when it should
have been a tenth, that a label ran off the edge, or that a dot sat above
its own line.

No image diffing. The numbers are already in the file — every bar carries
its value in a <title> or aria-label, and every SVG carries the frame it
must stay inside. That makes an oracle independent of the drawing code:
the check compares the picture against the number the reader sees next to
it, not against the formula that produced both.

Used by probelauf.py; standalone it checks tests/ausgabe/index.html.
"""
import math
import re
from html.parser import HTMLParser


class SvgCollector(HTMLParser):
    """Collect every <svg> in the page with its elements.

    HTMLParser rather than an XML parser: the page writes attributes
    unquoted (class=hashkurve), which no XML parser accepts.
    """

    SHAPES = ("rect", "circle", "line", "polyline", "polygon", "text", "image", "g")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.svgs = []
        self._depth = 0
        self._text_of = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "svg":
            self._depth += 1
            if self._depth == 1:
                self.svgs.append({"attrs": a, "shapes": [], "titles": []})
                return
        if not self._depth:
            return
        if tag in self.SHAPES:
            self.svgs[-1]["shapes"].append((tag, a))
        if tag == "title":
            self._text_of = len(self.svgs[-1]["shapes"]) - 1

    handle_startendtag = handle_starttag

    def handle_data(self, data):
        if self._depth and self._text_of is not None:
            self.svgs[-1]["titles"].append((self._text_of, data))
            self._text_of = None
        elif self._depth and self.svgs and self.svgs[-1]["shapes"] \
                and self.svgs[-1]["shapes"][-1][0] == "text" and data.strip():
            self.svgs[-1]["shapes"][-1][1]["__text"] = data

    def handle_endtag(self, tag):
        if tag == "svg" and self._depth:
            self._depth -= 1
        if tag == "title":
            self._text_of = None


def num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


UNIT = r"(?:°C|BTC|sat/vB|%|EH/s|PH/s)"
# Units with a scale, for the "A of B" bars: both sides may differ
# ("381,7 MB of 4,1 GB"), so the ratio needs the factor.
SCALE = r"(?:[KMGTP]?B|Bl[oö]cke[n]?|blocks)"
FACTOR = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "PB": 1e15}


# Which separator means what. Never guessed: '1,067 BTC' is one thousand
# and sixty-seven on the English page and one point zero six seven on the
# German one, and a guess picks the wrong one on whichever page it did not
# have in mind — the English run said "a higher value is a shorter column"
# for exactly that reason (2026-09-06).
COMMA = [True]          # True = German notation, set by run()


def first_number(text, unit_first=False):
    """A number out of a label, in the page's own notation.

    With unit_first the number that carries a unit wins over the one that
    comes first: a column's title reads '08:00 · peak 48.0 °C', and the
    hour is not what the column is as tall as. That mistake was in this
    file before it ever ran (2026-09-06).
    """
    m = None
    if unit_first:
        m = re.search(r"-?\d[\d.,]*(?=\s*" + UNIT + r")", text or "")
    m = m or re.search(r"-?\d[\d.,]*", text or "")
    if not m:
        return None
    raw = m.group(0).rstrip(".,")
    if COMMA[0]:
        raw = raw.replace(".", "").replace(",", ".")    # 1.705,5 -> 1705.5
    else:
        raw = raw.replace(",", "")                      # 1,705.5 -> 1705.5
    try:
        return float(raw)
    except ValueError:
        return None


def box_of(svg):
    parts = (svg["attrs"].get("viewbox") or svg["attrs"].get("viewBox") or "").split()
    if len(parts) != 4:
        return None
    return [num(p) for p in parts]


def bounds(tag, a):
    """Bounding box of one shape, or None when it has no geometry of its own."""
    if tag == "rect":
        x, y = num(a.get("x")), num(a.get("y"))
        return (x, y, x + num(a.get("width")), y + num(a.get("height")))
    if tag == "circle":
        cx, cy, r = num(a.get("cx")), num(a.get("cy")), num(a.get("r"))
        return (cx - r, cy - r, cx + r, cy + r)
    if tag == "line":
        xs = (num(a.get("x1")), num(a.get("x2")))
        ys = (num(a.get("y1")), num(a.get("y2")))
        return (min(xs), min(ys), max(xs), max(ys))
    if tag in ("polyline", "polygon"):
        pts = [p.split(",") for p in (a.get("points") or "").split() if "," in p]
        if not pts:
            return None
        xs = [num(p[0]) for p in pts]
        ys = [num(p[1]) for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    if tag == "image":
        x, y = num(a.get("x")), num(a.get("y"))
        return (x, y, x + num(a.get("width")), y + num(a.get("height")))
    return None


def run(page, check, comma=True, chars_per_unit=0.63, font=12.5):
    """All geometry checks. 'check(ok, text, detail)' is the caller's.

    'comma' is the page's number notation (German by default), and it is
    told, not guessed — see COMMA above.
    """
    COMMA[0] = comma
    print("\n  Geometry of the drawings")
    parser = SvgCollector()
    parser.feed(page)
    svgs = parser.svgs
    check(len(svgs) >= 8, f"the page carries drawings ({len(svgs)} SVGs)", str(len(svgs)))

    # ---- 1. nothing is drawn outside its own frame -----------------------
    # An SVG clips at the viewBox. A label that grows past it does not
    # wrap, it disappears — which is why the network map computes its
    # width from the longest line instead of fixing it.
    outside = []
    for svg in svgs:
        box = box_of(svg)
        if not box:
            outside.append("an SVG without a viewBox")
            continue
        x0, y0, w, h = box
        for tag, a in svg["shapes"]:
            b = bounds(tag, a)
            if b is None:
                continue
            if b[0] < x0 - 0.6 or b[1] < y0 - 0.6 or b[2] > x0 + w + 0.6 or b[3] > y0 + h + 0.6:
                outside.append(f"{tag} {tuple(round(v, 1) for v in b)} outside {box}")
    check(not outside, "every shape lies inside its viewBox", " | ".join(outside[:3]))

    # Text has no width in the markup, so it is measured: the map sizes its
    # frame from character count times font size times an average width,
    # and if that estimate is short the end of the longest line is cut off.
    cut = []
    for svg in svgs:
        box = box_of(svg)
        if not box:
            continue
        x0, y0, w, h = box
        for tag, a in svg["shapes"]:
            if tag != "text" or "__text" not in a:
                continue
            width = len(a["__text"]) * font * chars_per_unit
            x = num(a.get("x"))
            left = x - width if a.get("text-anchor") == "end" else (
                x - width / 2 if a.get("text-anchor") == "middle" else x)
            if left < x0 - 1 or left + width > x0 + w + 1:
                cut.append(f"{a['__text'][:24]!r} at x={x:.0f} in {box}")
    check(not cut, "no text line is cut off at the frame", " | ".join(cut[:2]))

    # ---- 2. a stretched SVG carries no rounded corners -------------------
    # preserveAspectRatio="none" stretches the box many times over, and an
    # rx goes with it: at small fractions the radius exceeds the fill and
    # the bar becomes a blob. That was on screen on 2026-08-23; the corners
    # come from CSS on the wrapper since.
    blobs = [f"{tag} rx={a.get('rx')}" for svg in svgs
             if (svg["attrs"].get("preserveaspectratio") or "").lower() == "none"
             for tag, a in svg["shapes"] if a.get("rx") or a.get("ry")]
    check(not blobs, "no rounded corners inside a stretched SVG", " | ".join(blobs[:3]))

    # ---- 3. a fill bar is as full as its own two numbers say -------------
    # The oracle has to come from outside the drawing. A label reading
    # "23 % CPU" is no use: build_bar makes both the width and that label
    # out of the same number, so a bar stuck at full would carry the label
    # "100 %" and agree with itself. (Tried on 2026-09-06, found nothing —
    # the mistake this whole file exists to avoid.)
    #
    # Two absolute numbers are independent of it: "381,7 MB of 4,1 GB",
    # "915.309 of 915.312 blocks". Their ratio is computed here and
    # compared with the drawn width — which is exactly the bug of
    # 2026-08-23, when the CSP dropped an inline width and every bar stood
    # full while its label was right.
    pair = re.compile(r"^(\d[\d.,]*)\s*(" + UNIT + r"|" + SCALE + r")?\s+(?:of|von)\s+"
                      r"(\d[\d.,]*)\s*(" + UNIT + r"|" + SCALE + r")?", re.I)
    wrong, bars = [], 0
    for svg in svgs:
        label = (svg["attrs"].get("aria-label") or "").strip()
        box = box_of(svg)
        m = pair.match(label)
        if not box or box[2] != 100 or not m:
            continue
        have = first_number(m.group(1)) * FACTOR.get((m.group(2) or "").upper(), 1)
        full = first_number(m.group(3)) * FACTOR.get((m.group(4) or "").upper(), 1)
        if not full:
            continue
        rects = [a for tag, a in svg["shapes"] if tag == "rect"]
        if not rects:
            continue
        bars += 1
        want = min(100.0, have / full * 100)
        got = num(rects[0].get("width"))
        if abs(got - want) > 1.5:
            wrong.append(f"{label!r}: {want:.1f} % expected, {got:.1f} % drawn")
    # One during the initial sync (the progress bar), more once the chain
    # stands and the cards with storage and mempool appear.
    check(bars >= 1, f"fill bars that name both their numbers ({bars})", str(bars))
    check(not wrong, "every such bar is as full as its two numbers say", " | ".join(wrong[:3]))

    # The percentage-labelled bars cannot be checked against their label —
    # same source — but they can be checked for being drawn at all, and
    # inside the box. A bar of width 0 next to a value that is not zero is
    # the other half of the 2026-08-23 fault.
    hidden = []
    for svg in svgs:
        label = (svg["attrs"].get("aria-label") or "").strip()
        box = box_of(svg)
        if not box or box[2] != 100:
            continue
        value = first_number(label)
        rects = [a for tag, a in svg["shapes"] if tag == "rect"]
        if not rects or value is None:
            continue
        w = num(rects[0].get("width"))
        if not (0 <= w <= 100):
            hidden.append(f"{label!r} drawn at {w:.1f}")
        if value > 1 and w == 0:
            hidden.append(f"{label!r} has no bar at all")
    check(not hidden, "no fill bar is drawn outside 0…100 or missing", " | ".join(hidden[:3]))

    # ---- 4. columns follow their own values ------------------------------
    # Every column carries its value in a <title> (Jakob, 2026-09-03,
    # "a bar without a number is a shape"). Heights need not be
    # proportional — the temperature scale starts at 30 °C — but the order
    # must hold: a higher value is never a shorter column.
    swapped, checked = [], 0
    for svg in svgs:
        if "saeulen" not in (svg["attrs"].get("class") or ""):
            continue
        titles = dict(svg["titles"])
        cols = [(i, a) for i, (tag, a) in enumerate(svg["shapes"]) if tag == "rect"]
        pairs = [(first_number(titles[i], True), num(a.get("height")))
                 for i, a in cols if i in titles and first_number(titles[i], True) is not None]
        if len(pairs) < 3:
            continue
        checked += 1
        for (v1, h1), (v2, h2) in zip(pairs, pairs[1:]):
            if (v1 - v2) * (h1 - h2) < 0 and abs(h1 - h2) > 0.2:
                swapped.append(f"{v1}->{h1:.1f} vs {v2}->{h2:.1f}")
        # A column of height zero is invisible and reads as "no data".
        if any(h < 0.5 for _, h in pairs):
            swapped.append("a column with no height at all")
    check(checked > 0, f"column charts carry their values ({checked} charts)")
    check(not swapped, "a higher value is never a shorter column", " | ".join(swapped[:3]))

    # Columns must not overlap or sit on top of each other.
    overlap = []
    for svg in svgs:
        if "saeulen" not in (svg["attrs"].get("class") or ""):
            continue
        rects = sorted((a for tag, a in svg["shapes"] if tag == "rect"),
                       key=lambda a: num(a.get("x")))
        for a, b in zip(rects, rects[1:]):
            if num(a.get("x")) + num(a.get("width")) > num(b.get("x")) + 0.01:
                overlap.append(f"x={num(a.get('x')):.1f} into x={num(b.get('x')):.1f}")
    check(not overlap, "columns stand next to each other, never on top", " | ".join(overlap[:3]))

    # ---- 5. the network map: dot and line share one row -------------------
    # There used to be a horizontal running outward with the label above
    # it, which put dot and text at different heights. Both sit on one line
    # now, and rows must not collide.
    maps = [s for s in svgs if any(
        "peerpunkt" in (a.get("class") or "") for _, a in s["shapes"])]
    if not maps:
        # No peers at all: the 'leer' case draws no map, and that is right.
        print("  [ --  ] no network map on this page (no peers)")
    check(len(maps) <= 1, "at most one network map on the page", str(len(maps)))
    for svg in maps:
        rows = {}
        for tag, a in svg["shapes"]:
            cls = a.get("class") or ""
            if tag == "circle" and "peerpunkt" in cls:
                rows.setdefault(round(num(a.get("cy")), 1), {})["dot"] = num(a.get("cx"))
            if tag == "text" and "peerzeile" in cls:
                rows.setdefault(round(num(a.get("y")), 1), {})["text"] = num(a.get("x"))
        both = [y for y, r in rows.items() if "dot" in r and "text" in r]
        check(len(both) == len([y for y, r in rows.items() if "dot" in r]) and both,
              f"every dot has its label on the same line ({len(both)} peers)",
              str(sorted(rows.items())[:2]))
        ys = sorted(rows)
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        check(not gaps or min(gaps) >= 12,
              "the rows keep their distance (no two peers on one line)",
              f"smallest gap {min(gaps):.1f}" if gaps else "")
        # Both sides hang off the same hub, and the hub is in the middle.
        box = box_of(svg)
        hub = [a for tag, a in svg["shapes"]
               if tag == "circle" and "nabefeld" in (a.get("class") or "")]
        check(len(hub) == 1 and abs(num(hub[0].get("cx")) - box[2] / 2) < 1.0,
              "the hub sits on the middle of the frame",
              f"{num(hub[0].get('cx')) if hub else '-'} vs {box[2] / 2}")
        left = sorted(r["dot"] for r in rows.values() if r.get("dot", 0) < box[2] / 2)
        right = sorted(r["dot"] for r in rows.values() if r.get("dot", 0) > box[2] / 2)
        check(len({round(v, 1) for v in left}) <= 1 and len({round(v, 1) for v in right}) <= 1,
              "all dots of one side stand on one vertical",
              f"{sorted(set(left))[:3]} | {sorted(set(right))[:3]}")
        check(not left or not right
              or abs((box[2] / 2 - left[0]) - (right[0] - box[2] / 2)) < 1.0,
              "the two sides are the same distance from the hub")
        # Spokes end at their dot, not somewhere near it.
        loose = []
        for tag, a in svg["shapes"]:
            if tag == "line" and "peerlinie" in (a.get("class") or ""):
                end = (num(a.get("x2")), round(num(a.get("y2")), 1))
                if end[1] not in rows or abs(rows[end[1]].get("dot", 1e9) - end[0]) > 0.2:
                    loose.append(str(end))
        check(not loose, "every spoke ends on its dot", " | ".join(loose[:3]))

    # ---- 6. the hashrate curve --------------------------------------------
    for svg in svgs:
        if "hashkurve" not in (svg["attrs"].get("class") or ""):
            continue
        box = box_of(svg)
        line = [a for tag, a in svg["shapes"] if tag == "polyline"]
        if not line:
            continue
        pts = [(num(p.split(",")[0]), num(p.split(",")[1]))
               for p in line[0]["points"].split() if "," in p]
        xs = [p[0] for p in pts]
        check(xs == sorted(xs) and len(set(xs)) == len(xs),
              f"the curve runs left to right, one point per period ({len(pts)})")
        check(abs(xs[0] - 0) < 0.6 and abs(xs[-1] - box[2]) < 0.6,
              "the curve spans the full width", f"{xs[0]:.1f}…{xs[-1]:.1f} of {box[2]}")
        check(min(p[1] for p in pts) >= box[1] - 0.6
              and max(p[1] for p in pts) <= box[1] + box[3] + 0.6,
              "the curve stays inside its frame")
        # The area below the line is closed along the bottom edge, or the
        # gradient bleeds upwards over the whole box.
        area = [a for tag, a in svg["shapes"] if tag == "polygon"]
        if area:
            apts = [p for p in area[0]["points"].split() if "," in p]
            check(apts[0].endswith(f",{box[3]:g}") and apts[-1].endswith(f",{box[3]:g}"),
                  "the area under the curve is closed at the bottom",
                  f"{apts[0]} … {apts[-1]}")
    return True
