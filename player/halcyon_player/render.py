"""Render a skin's views to PNG files (for checking the renderer headlessly).

    python -m halcyon_player.render SKIN.wmz OUTDIR [view-id ...]
"""
import os
import sys

from PySide6.QtGui import QGuiApplication

from .skin import Skin, build


def render_view(skin, node):
    view = build(skin, node)
    if view.size().isEmpty():
        return None, view
    img = view.render()
    return img, view


def main(argv):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(argv)  # noqa: F841  (fonts and images need it)
    skin = Skin(argv[1])
    out = argv[2]
    os.makedirs(out, exist_ok=True)
    wanted = [v.lower() for v in argv[3:]]
    start = skin.start_view()
    for node in skin.views:
        vid = node.get("id") or "view"
        if wanted and vid.lower() not in wanted:
            continue
        img, view = render_view(skin, node)
        if img is None:
            print(f"  {vid}: no size (set by script)")
            continue
        path = os.path.join(out, f"{vid}.png")
        img.save(path)
        n = sum(1 for _ in view.walk())
        star = "*" if node is start else " "
        print(f" {star}{vid}: {img.width()}x{img.height()}, {n} elements -> {path}")


if __name__ == "__main__":
    main(sys.argv)
