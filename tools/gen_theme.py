#!/usr/bin/env python3
"""Generate the glossy UI chrome for Halcyon Linux.

Writes Plasma desktop-theme frame SVGs and the Aurorae window decoration
(titlebar frame + orb buttons) into branding/share/. Wallpapers and the logo are
NOT generated here; they are user-supplied in branding/assets/.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "branding", "share")

SVG_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}">\n')


def write(path, text):
    path = os.path.join(SRC, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# ---------------------------------------------------------------- frames ---
class Glass:
    """Colour recipe for a glossy glass frame."""

    def __init__(self, top, sheen, base, bottom, outline, highlight, split=0.48):
        self.top, self.sheen, self.base, self.bottom = top, sheen, base, bottom
        self.split = split  # where the gloss breaks, as a fraction of the height
        self.outline, self.highlight = outline, highlight

    def gradient(self, gid):
        # Classic Aero gloss: bright upper half, hard break, deeper lower half.
        return (f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" stop-color="{self.top[0]}" stop-opacity="{self.top[1]}"/>'
                f'<stop offset="{self.split}" stop-color="{self.sheen[0]}" stop-opacity="{self.sheen[1]}"/>'
                f'<stop offset="{self.split + 0.02}" stop-color="{self.base[0]}" stop-opacity="{self.base[1]}"/>'
                f'<stop offset="1" stop-color="{self.bottom[0]}" stop-opacity="{self.bottom[1]}"/>'
                '</linearGradient>\n')


def frame(prefix, g, c, r, x0=0, y0=0, top_h=None, gid=None):
    """Emit the nine FrameSvg pieces for one frame.

    c  = corner/edge thickness, r = corner radius, top_h = height of the top
    row (defaults to c; Aurorae uses a tall top row as the titlebar).
    Pieces are laid out side by side starting at (x0, y0) so several prefixes
    can share one document without overlapping.
    """
    th = top_h or c
    gid = gid or (prefix or "f") + "grad"
    p = (prefix + "-") if prefix else ""
    out = [f"<defs>{g.gradient(gid)}</defs>\n"]
    oc, oo = g.outline
    hc, ho = g.highlight
    tc, to = g.top
    bc, bo = g.bottom
    cw = 40  # width of stretchable sample pieces
    # Column x positions and row y positions in the sample sheet.
    xs = [x0, x0 + c, x0 + c + cw]
    ys = [y0, y0 + th, y0 + th + cw]
    ws = [c, cw, c]
    hs = [th, cw, c]
    names = [["topleft", "top", "topright"],
             ["left", "center", "right"],
             ["bottomleft", "bottom", "bottomright"]]

    def fill_for(row):
        if row == 0 and th == c:
            return f'fill="{tc}" fill-opacity="{to}"'
        if row == 2 or (row == 1 and th != c):
            return f'fill="{bc}" fill-opacity="{bo}"'
        return f'fill="url(#{gid})"'

    # Every piece is built from filled shapes only: strokes spill outside an
    # element's bounds and show up as seams when Plasma stretches the edges.
    def quarter(k, w, h):
        # Region of a w*h top-left corner piece inside a rounded rect inset by k.
        rr = max(r - k, 0.01)
        return f"M{w},{k} H{r} A{rr},{rr} 0 0 0 {k},{r} V{h} H{w} Z"

    def band(x, y, w, h, color, op):
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}" fill-opacity="{op}"/>'

    for row in range(3):
        for col in range(3):
            n = names[row][col]
            x, y, w, h = xs[col], ys[row], ws[col], hs[row]
            body = []
            fill = fill_for(row)
            if row != 1 and col != 1:
                # Rounded corner: build top-left, mirror into place.
                sx = -1 if col == 2 else 1
                sy = -1 if row == 2 else 1
                tx = x + (w if col == 2 else 0)
                ty = y + (h if row == 2 else 0)
                hop = ho if row == 0 else ho * 0.5
                body.append(f'<g transform="translate({tx},{ty}) scale({sx},{sy})">'
                            f'<rect width="{w}" height="{h}" fill="none"/>'
                            f'<path d="{quarter(0, w, h)} {quarter(1, w, h)}" fill-rule="evenodd" fill="{oc}" fill-opacity="{oo}"/>'
                            f'<path d="{quarter(1, w, h)} {quarter(2, w, h)}" fill-rule="evenodd" fill="{hc}" fill-opacity="{hop}"/>'
                            f'<path d="{quarter(2, w, h)}" {fill}/></g>')
            elif row == 1 and col == 1:
                body.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" {fill}/>')
            elif col == 1:  # top / bottom edge
                if row == 0:
                    body += [band(x, y, w, 1, oc, oo), band(x, y + 1, w, 1, hc, ho),
                             f'<rect x="{x}" y="{y + 2}" width="{w}" height="{h - 2}" {fill}/>']
                else:
                    body += [f'<rect x="{x}" y="{y}" width="{w}" height="{h - 2}" {fill}/>',
                             band(x, y + h - 2, w, 1, hc, ho * 0.5), band(x, y + h - 1, w, 1, oc, oo)]
            else:  # left / right edge
                if col == 0:
                    body += [band(x, y, 1, h, oc, oo), band(x + 1, y, 1, h, hc, ho),
                             f'<rect x="{x + 2}" y="{y}" width="{w - 2}" height="{h}" {fill}/>']
                else:
                    body += [f'<rect x="{x}" y="{y}" width="{w - 2}" height="{h}" {fill}/>',
                             band(x + w - 2, y, 1, h, hc, ho), band(x + w - 1, y, 1, h, oc, oo)]
            out.append(f'<g id="{p}{n}">{"".join(body)}</g>\n')
    return "".join(out), xs[2] + c, ys[2] + c


def shadow(prefix, s, x0, y0, color="#00203a", alpha=0.35):
    """FrameSvg 'shadow-' pieces: soft fall-off around the frame."""
    p = prefix + "-"
    out = ["<defs>"
           f'<radialGradient id="{p}sr"><stop offset="0" stop-color="{color}" stop-opacity="{alpha}"/>'
           f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>']
    for name, (x1, y1, x2, y2) in {"v": (0, 1, 0, 0), "vb": (0, 0, 0, 1), "h": (1, 0, 0, 0), "hr": (0, 0, 1, 0)}.items():
        out.append(f'<linearGradient id="{p}s{name}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">'
                   f'<stop offset="0" stop-color="{color}" stop-opacity="{alpha}"/>'
                   f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient>')
    out.append("</defs>\n")
    cw = 20
    pieces = {
        "topleft": (x0, y0, s, s, None),
        "top": (x0 + s, y0, cw, s, "v"),
        "topright": (x0 + s + cw, y0, s, s, None),
        "left": (x0, y0 + s, s, cw, "h"),
        "right": (x0 + s + cw, y0 + s, s, cw, "hr"),
        "bottomleft": (x0, y0 + s + cw, s, s, None),
        "bottom": (x0 + s, y0 + s + cw, cw, s, "vb"),
        "bottomright": (x0 + s + cw, y0 + s + cw, s, s, None),
    }
    for n, (x, y, w, h, grad) in pieces.items():
        if grad:
            out.append(f'<rect id="{p}{n}" x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#{p}s{grad})"/>\n')
        else:
            # Corner: radial centred on the inner corner of the piece.
            cx = x + (s if "left" in n else 0)
            cy = y + (s if "top" in n else 0)
            out.append(f'<g id="{p}{n}"><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none"/>'
                       f'<circle cx="{cx}" cy="{cy}" r="{s}" fill="url(#{p}sr)" '
                       f'clip-path="url(#{p}clip{n})"/></g>\n'
                       f'<clipPath id="{p}clip{n}"><rect x="{x}" y="{y}" width="{w}" height="{h}"/></clipPath>\n')
    return "".join(out)


def hints(margins):
    return '<rect id="hint-stretch-borders" width="1" height="1" fill="none"/>\n' + "".join(f'<rect id="hint-{k}" x="0" y="{i * 3}" width="{v}" height="{v}" fill="none"/>\n'
                   for i, (k, v) in enumerate(margins.items()))


# Palettes ------------------------------------------------------------------
AQUA_GLASS = Glass(top=("#ffffff", 0.82), sheen=("#e6f6ff", 0.62), base=("#9fd8f5", 0.52),
                   bottom=("#c8f0ff", 0.66), outline=("#0b4f7c", 0.55), highlight=("#ffffff", 0.9))
FROST_GLASS = Glass(top=("#ffffff", 0.94), sheen=("#f2faff", 0.88), base=("#e2f3fc", 0.86),
                    bottom=("#eef9ff", 0.90), outline=("#2a6e9a", 0.45), highlight=("#ffffff", 1.0))


# Dark Aero glass: deep blue-black, bright sheen across the top half.
DARK_GLASS = Glass(top=("#bfe9ff", 0.42), sheen=("#3a7fb0", 0.34), base=("#061a2e", 0.62),
                   bottom=("#0b3554", 0.72), outline=("#000a14", 0.85), highlight=("#ffffff", 0.32))
DARK_POPUP = Glass(top=("#9fdcff", 0.30), sheen=("#123a5c", 0.80), base=("#0a2236", 0.86),
                   bottom=("#0d2c46", 0.90), outline=("#000a14", 0.85), highlight=("#ffffff", 0.28),
                   split=0.08)


def plasma_frame_svg(glass, c=8, r=6, with_shadow=True, margins=None, prefixes=("",)):
    parts, W, H = [], 0, 0
    x = 0
    for pf in prefixes:
        body, w, h = frame(pf, glass, c, r, x0=x, y0=0, gid=(pf or "base") + "g")
        parts.append(body)
        x, H = w + 10, max(H, h)
    if with_shadow:
        parts.append(shadow("shadow", 14, x, 0))
        x += 14 * 2 + 20 + 10
        H = max(H, 14 * 2 + 20)
    if margins:
        parts.append(hints(margins))
    return SVG_HEAD.format(w=x, h=H + 10) + "".join(parts) + "</svg>\n"


def gen_plasma():
    base = "plasma/desktoptheme/Halcyon"
    # Panel: dark glass. Small margins so applet icons get a full 32px.
    write(f"{base}/widgets/panel-background.svg",
          plasma_frame_svg(DARK_GLASS, c=6, r=6, prefixes=("", "north", "south", "east", "west"),
                           margins={"top-margin": 2, "bottom-margin": 2, "left-margin": 6, "right-margin": 6}))
    write(f"{base}/dialogs/background.svg",
          plasma_frame_svg(DARK_POPUP, c=10, r=8,
                           margins={"top-margin": 8, "bottom-margin": 8, "left-margin": 8, "right-margin": 8}))
    write(f"{base}/widgets/background.svg",
          plasma_frame_svg(DARK_POPUP, c=10, r=8,
                           margins={"top-margin": 10, "bottom-margin": 10, "left-margin": 10, "right-margin": 10}))
    write(f"{base}/widgets/tooltip.svg",
          plasma_frame_svg(DARK_POPUP, c=8, r=6,
                           margins={"top-margin": 6, "bottom-margin": 6, "left-margin": 8, "right-margin": 8}))

    # Taskbar buttons on dark glass: invisible when idle, lit glass on hover,
    # aqua glow for the focused window.
    hover = Glass(("#ffffff", 0.32), ("#9fdcff", 0.16), ("#1e6fa8", 0.22), ("#3fa9e0", 0.30),
                  ("#000a14", 0.6), ("#ffffff", 0.35))
    focus = Glass(("#d8f4ff", 0.55), ("#5cc4f0", 0.38), ("#0b6fb0", 0.55), ("#2ac0f0", 0.70),
                  ("#000a14", 0.7), ("#ffffff", 0.5))
    attention = Glass(("#fff4c8", 0.7), ("#ffc850", 0.55), ("#c07000", 0.6), ("#ffb030", 0.75),
                      ("#3a2000", 0.7), ("#ffffff", 0.5))
    idle = Glass(("#ffffff", 0.0), ("#ffffff", 0.0), ("#ffffff", 0.0), ("#ffffff", 0.0),
                 ("#ffffff", 0.0), ("#ffffff", 0.0))
    minimized = Glass(("#ffffff", 0.12), ("#ffffff", 0.05), ("#3fa9e0", 0.06), ("#3fa9e0", 0.10),
                      ("#000a14", 0.3), ("#ffffff", 0.15))
    parts, x, H = [], 0, 0
    for pf, gl in (("normal", idle), ("hover", hover), ("focus", focus),
                   ("attention", attention), ("minimized", minimized)):
        body, w, h = frame(pf, gl, 5, 4, x0=x, gid=pf + "g")
        parts.append(body)
        x, H = w + 10, max(H, h)
    write(f"{base}/widgets/tasks.svg", SVG_HEAD.format(w=x, h=H) + "".join(parts) + "</svg>\n")


# ---------------------------------------------------------------- aurorae ---
TITLE_EDGE_TOP, TITLE_H, TITLE_EDGE_BOTTOM = 4, 24, 3
BORDER = 6
TOP_ROW = TITLE_EDGE_TOP + TITLE_H + TITLE_EDGE_BOTTOM


def gen_aurorae():
    base = "aurorae/themes/HalcyonAero"
    # Dark glass to match the taskbar; a little more opaque so titles stay readable.
    active = Glass(top=("#bfe9ff", 0.55), sheen=("#3a7fb0", 0.55), base=("#061a2e", 0.86),
                   bottom=("#0b3554", 0.92), outline=("#000a14", 0.9), highlight=("#ffffff", 0.32))
    inactive = Glass(top=("#c8d4dc", 0.35), sheen=("#56687a", 0.55), base=("#18242e", 0.84),
                     bottom=("#22323e", 0.88), outline=("#000a14", 0.75), highlight=("#ffffff", 0.18))
    a, w1, h1 = frame("decoration", active, BORDER, BORDER, top_h=TOP_ROW, gid="activeg")
    i, w2, h2 = frame("decoration-inactive", inactive, BORDER, BORDER, x0=w1 + 10, top_h=TOP_ROW, gid="inactiveg")
    write(f"{base}/decoration.svg", SVG_HEAD.format(w=w2 + 10, h=max(h1, h2)) + a + i + hints({}) + "</svg>\n")

    # Orb buttons: glossy spheres with white glyphs.
    glyphs = {
        "minimize": '<path d="M7,12.5 H15" stroke="#fff" stroke-width="2" stroke-linecap="round"/>',
        "maximize": '<rect x="7" y="7" width="8" height="8" rx="1.2" fill="none" stroke="#fff" stroke-width="1.8"/>',
        "restore": ('<rect x="6.5" y="8.5" width="7" height="7" rx="1" fill="none" stroke="#fff" stroke-width="1.6"/>'
                    '<path d="M9,6.5 H15.5 V13" fill="none" stroke="#fff" stroke-width="1.6"/>'),
        "close": '<path d="M7.5,7.5 L14.5,14.5 M14.5,7.5 L7.5,14.5" stroke="#fff" stroke-width="2" stroke-linecap="round"/>',
        "alldesktops": '<circle cx="11" cy="11" r="2.6" fill="#fff"/>',
        "keepabove": '<path d="M7,13 L11,8 L15,13" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/>',
        "keepbelow": '<path d="M7,9 L11,14 L15,9" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/>',
        "shade": '<path d="M7,8.5 H15 M7,12.5 L11,15.5 L15,12.5" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>',
        "help": '<text x="11" y="15.5" font-family="sans-serif" font-weight="bold" font-size="11" text-anchor="middle" fill="#fff">?</text>',
    }
    tints = {  # (light, dark) per state
        "aqua": {"active": ("#7fe0ff", "#0a78c2"), "hover": ("#a8f0ff", "#14a0e8"),
                 "pressed": ("#4cb8e8", "#064d80"), "inactive": ("#dbe8ef", "#8aa6b8")},
        "ember": {"active": ("#ffb08a", "#d0341a"), "hover": ("#ffd0a8", "#f04a24"),
                  "pressed": ("#e07050", "#8a1a08"), "inactive": ("#ecdcd6", "#b89a90")},
    }
    S = 22
    for name, glyph in glyphs.items():
        tint = tints["ember" if name == "close" else "aqua"]
        out = [SVG_HEAD.format(w=S * 5 + 20, h=S)]
        for k, state in enumerate(("active", "hover", "pressed", "inactive", "deactivated")):
            light, dark = tint.get(state, tint["inactive"])
            x = k * (S + 5)
            gid = f"{state}orb"
            op = 0.45 if state == "deactivated" else 1
            out.append(
                f'<defs><radialGradient id="{gid}" cx="0.5" cy="0.75" r="0.75">'
                f'<stop offset="0" stop-color="{light}"/><stop offset="1" stop-color="{dark}"/></radialGradient>'
                f'<linearGradient id="{gid}gl" x1="0" y1="0" x2="0" y2="1">'
                '<stop offset="0" stop-color="#fff" stop-opacity="0.95"/>'
                '<stop offset="1" stop-color="#fff" stop-opacity="0.05"/></linearGradient></defs>'
                f'<g id="{state}-center" transform="translate({x},0)" opacity="{op}">'
                f'<rect width="{S}" height="{S}" fill="none"/>'
                f'<circle cx="11" cy="11" r="9.5" fill="url(#{gid})" stroke="#00223a" stroke-opacity="0.55"/>'
                f'<ellipse cx="11" cy="6.6" rx="6.6" ry="4.2" fill="url(#{gid}gl)"/>'
                f'{glyph}</g>\n')
        out.append("</svg>\n")
        write(f"{base}/{name}.svg", "".join(out))

    write(f"{base}/HalcyonAerorc", f"""[General]
ActiveTextColor=240,250,255,255
InactiveTextColor=170,188,202,220
TitleAlignment=Left
TitleVerticalAlignment=Center
Animation=150
Shadow=true
DecorationPosition=0

[Layout]
BorderLeft={BORDER}
BorderRight={BORDER}
BorderBottom={BORDER}
TitleEdgeTop={TITLE_EDGE_TOP}
TitleEdgeBottom={TITLE_EDGE_BOTTOM}
TitleEdgeLeft=8
TitleEdgeRight=6
TitleBorderLeft=6
TitleBorderRight=6
TitleHeight={TITLE_H}
ButtonWidth=22
ButtonHeight=22
ButtonSpacing=3
ButtonMarginTop=1
ExplicitButtonSpacer=8
PaddingTop=0
PaddingBottom=0
PaddingLeft=0
PaddingRight=0
""")
    write(f"{base}/metadata.desktop", """[Desktop Entry]
Name=Halcyon Aero
Comment=Glossy aqua glass window decoration
X-KDE-PluginInfo-Author=Halcyon Linux
X-KDE-PluginInfo-Name=HalcyonAero
X-KDE-PluginInfo-Version=1.0
X-KDE-PluginInfo-License=GPL-3.0-or-later
X-KDE-ServiceTypes=KWin/Decoration
""")


def gen_aurorae_slim():
    """Tiling-mode decoration: no titlebar, 3px glowing border (Hyprland style)."""
    base = "aurorae/themes/HalcyonAeroSlim"
    B = 3
    active = Glass(top=("#7fe0ff", 1), sheen=("#5cc8f5", 1), base=("#2aa3e6", 1),
                   bottom=("#6cc04a", 1), outline=("#063a5e", 0.6), highlight=("#ffffff", 0.7))
    inactive = Glass(top=("#9fb6c4", 0.55), sheen=("#9fb6c4", 0.55), base=("#9fb6c4", 0.55),
                     bottom=("#9fb6c4", 0.55), outline=("#3a5568", 0.4), highlight=("#ffffff", 0.2))
    a, w1, h1 = frame("decoration", active, B, 2, gid="slimactive")
    i, w2, h2 = frame("decoration-inactive", inactive, B, 2, x0=w1 + 10, gid="sliminactive")
    write(f"{base}/decoration.svg", SVG_HEAD.format(w=w2 + 10, h=max(h1, h2)) + a + i + hints({}) + "</svg>\n")
    write(f"{base}/HalcyonAeroSlimrc", f"""[General]
ActiveTextColor=12,46,74,255
InactiveTextColor=70,96,116,200
Shadow=true

[Layout]
BorderLeft={B}
BorderRight={B}
BorderBottom={B}
TitleEdgeTop={B}
TitleEdgeBottom=0
TitleEdgeLeft=0
TitleEdgeRight=0
TitleHeight=0
ButtonWidth=0
ButtonHeight=0
PaddingTop=0
PaddingBottom=0
PaddingLeft=0
PaddingRight=0
""")
    write(f"{base}/metadata.desktop", """[Desktop Entry]
Name=Halcyon Aero Slim
Comment=Borderless tiling decoration with a glowing active edge
X-KDE-PluginInfo-Author=Halcyon Linux
X-KDE-PluginInfo-Name=HalcyonAeroSlim
X-KDE-PluginInfo-Version=1.0
X-KDE-PluginInfo-License=GPL-3.0-or-later
X-KDE-ServiceTypes=KWin/Decoration
""")


def gen_sprites():
    # Small glossy aqua orb used by the splash screens' progress row.
    write("halcyon/orb.svg", SVG_HEAD.format(w=18, h=18) +
          '<defs><radialGradient id="o" cx="0.5" cy="0.75" r="0.75">'
          '<stop offset="0" stop-color="#a8f0ff"/><stop offset="1" stop-color="#0a78c2"/></radialGradient>'
          '<linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
          '<stop offset="0" stop-color="#fff" stop-opacity="0.95"/>'
          '<stop offset="1" stop-color="#fff" stop-opacity="0.05"/></linearGradient></defs>'
          '<circle cx="9" cy="9" r="8" fill="url(#o)" stroke="#00223a" stroke-opacity="0.5"/>'
          '<ellipse cx="9" cy="5.4" rx="5.4" ry="3.4" fill="url(#g)"/></svg>\n')


if __name__ == "__main__":
    gen_sprites()
    gen_plasma()
    gen_aurorae()
    gen_aurorae_slim()
    print("generated chrome into", SRC, file=sys.stderr)
