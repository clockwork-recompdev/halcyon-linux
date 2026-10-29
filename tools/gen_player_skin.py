#!/usr/bin/env python3
"""Generate Halcyon Media Player's built-in skin (glass panel + orb buttons).

Writes player/skins/halcyon/: SVG sources and halcyon.wms. PNGs are rendered
from the SVGs at package time (rsvg-convert), then zipped into Halcyon.wmz.
"""
import os

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "player", "skins", "halcyon")
W, H = 380, 112
# Real PNG transparency gives clean anti-aliased edges, so no colour key.


def svg(w, h, body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">{body}</svg>\n'


def write(name, text):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(text)


# Glass panel: dark Aero glass, bright sheen, magenta outside the rounded shape.
write("panel.svg", svg(W, H, f'''
<defs>
 <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#3a7fb0"/><stop offset="0.45" stop-color="#123a5c"/>
  <stop offset="0.47" stop-color="#061a2e"/><stop offset="1" stop-color="#0b3554"/></linearGradient>
 <linearGradient id="s" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#fff" stop-opacity="0.55"/><stop offset="1" stop-color="#fff" stop-opacity="0.04"/></linearGradient>
 <linearGradient id="lcd" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#0a2236"/><stop offset="1" stop-color="#102e48"/></linearGradient>
</defs>
<rect x="0.5" y="0.5" width="{W-1}" height="{H-1}" rx="18" fill="url(#g)" stroke="#000a14"/>
<rect x="1.5" y="1.5" width="{W-3}" height="{H-3}" rx="17" fill="none" stroke="#fff" stroke-opacity="0.3"/>
<rect x="6" y="3" width="{W-12}" height="{H*0.42:.0f}" rx="14" fill="url(#s)"/>
<rect x="16" y="14" width="{W-32}" height="34" rx="7" fill="url(#lcd)" stroke="#000a14"/>
<rect x="17" y="15" width="{W-34}" height="32" rx="6" fill="none" stroke="#7fe0ff" stroke-opacity="0.25"/>
'''))

# Seek bar track and thumb.
write("track.svg", svg(W - 48, 8, '''
<rect x="0.5" y="0.5" width="331" height="7" rx="3.5" fill="#06121e" stroke="#000a14"/>'''))
write("fill.svg", svg(W - 48, 8, '''
<defs><linearGradient id="f" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#bfe9ff"/><stop offset="1" stop-color="#1e9be0"/></linearGradient></defs>
<rect x="0.5" y="0.5" width="331" height="7" rx="3.5" fill="url(#f)"/>'''))
write("thumb.svg", svg(14, 14, '''
<defs><radialGradient id="o" cx="0.5" cy="0.7" r="0.7"><stop offset="0" stop-color="#fff"/>
<stop offset="1" stop-color="#7fe0ff"/></radialGradient></defs>
<circle cx="7" cy="7" r="6" fill="url(#o)" stroke="#063a5e"/>'''))

# Orb buttons in three states.
GLYPH = {
    "prev": '<path d="M9,9 V23 M23,9 L12,16 L23,23 Z" stroke="#fff" stroke-width="2.4" fill="#fff" stroke-linejoin="round"/>',
    "next": '<path d="M23,9 V23 M9,9 L20,16 L9,23 Z" stroke="#fff" stroke-width="2.4" fill="#fff" stroke-linejoin="round"/>',
    "play": '<path d="M12,8 L25,16 L12,24 Z" fill="#fff" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>',
    "pause": '<path d="M11,9 V23 M21,9 V23" stroke="#fff" stroke-width="4" stroke-linecap="round"/>',
    "stop": '<rect x="10" y="10" width="12" height="12" rx="2" fill="#fff"/>',
    "close": '<path d="M11,11 L21,21 M21,11 L11,21" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>',
    "min": '<path d="M10,20 H22" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>',
    "open": '<path d="M8,22 H24 M16,9 V19 M11,14 L16,9 L21,14" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
}
STATES = {"": ("#7fe0ff", "#0a78c2"), "_hover": ("#b8f2ff", "#16a4ec"), "_down": ("#4cb8e8", "#064d80")}
for name, glyph in GLYPH.items():
    for suf, (light, dark) in STATES.items():
        if name == "close":
            light, dark = {"": ("#ffb08a", "#d0341a"), "_hover": ("#ffd0a8", "#f04a24"), "_down": ("#e07050", "#8a1a08")}[suf]
        size = 32
        write(f"{name}{suf}.svg", svg(size, size, f'''
<defs><radialGradient id="o" cx="0.5" cy="0.75" r="0.75"><stop offset="0" stop-color="{light}"/>
<stop offset="1" stop-color="{dark}"/></radialGradient>
<linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0.9"/>
<stop offset="1" stop-color="#fff" stop-opacity="0.05"/></linearGradient></defs>
<circle cx="16" cy="16" r="14.5" fill="url(#o)" stroke="#00223a" stroke-opacity="0.7"/>
<ellipse cx="16" cy="9.5" rx="9.5" ry="6" fill="url(#g)"/>{glyph}'''))

# Layout. Values bound with wmpprop: are filled in by the player.
buttons = [("open", 22), ("prev", 106), ("stop", 146), ("play", 186), ("pause", 186), ("next", 226)]
items = []
for name, x in buttons:
    action = {"open": "player.openPlayerDialog()", "prev": "player.controls.previous()",
              "stop": "player.controls.stop()", "play": "player.controls.play()",
              "pause": "player.controls.pause()", "next": "player.controls.next()"}[name]
    items.append(f'''  <BUTTON id="btn_{name}" left="{x}" top="72" image="{name}.png" hoverImage="{name}_hover.png"
          downImage="{name}_down.png" onClick="{action}" upToolTip="{name.title()}"/>''')
wms = f'''<THEME id="Halcyon" title="Halcyon" author="Halcyon Linux">
<VIEW id="mainView" width="{W}" height="{H}" backgroundImage="panel.png" titleBar="false">
  <TEXT id="title" left="26" top="17" width="{W-52}" height="16" value="wmpprop:player.currentMedia.name"
        foregroundColor="#e6f6ff" fontFace="Source Sans 3" fontSize="10" fontStyle="bold"/>
  <TEXT id="hint" left="26" top="31" width="{W-52}" height="14" value="Right-click or drop files to play"
        foregroundColor="#7fb8d8" fontFace="Source Sans 3" fontSize="8"/>
  <SLIDER id="seek" left="24" top="56" width="{W-48}" height="10" backgroundImage="track.png"
          foregroundImage="fill.png" thumbImage="thumb.png" min="0" max="100"
          value="wmpprop:player.controls.currentPosition"/>
{chr(10).join(items)}
  <BUTTON id="btn_min" left="{W-80}" top="72" image="min.png" hoverImage="min_hover.png" downImage="min_down.png"
          onClick="view.minimize()" upToolTip="Minimize"/>
  <BUTTON id="btn_close" left="{W-44}" top="72" image="close.png" hoverImage="close_hover.png" downImage="close_down.png"
          onClick="view.close()" upToolTip="Close"/>
</VIEW>
</THEME>
'''
write("halcyon.wms", wms)
print("wrote", OUT)
