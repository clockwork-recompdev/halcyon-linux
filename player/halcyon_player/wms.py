"""Forgiving parser for Windows Media Player skin definition files (.wms).

WMS looks like XML but WMP accepted far more: tag names in any case, script
code with raw <, > and & inside attribute values, unquoted attributes, UTF-16
with or without a BOM, and stray text. This parser takes the same liberties.
"""
import re
from dataclasses import dataclass, field


@dataclass
class Node:
    tag: str                                  # upper-case tag name, e.g. "VIEW"
    attrs: dict = field(default_factory=dict) # lower-case keys, raw string values
    children: list = field(default_factory=list)
    parent: "Node | None" = None

    def get(self, key, default=None):
        return self.attrs.get(key.lower(), default)

    def find_all(self, tag):
        tag = tag.upper()
        for c in self.children:
            if c.tag == tag:
                yield c
            yield from c.find_all(tag)

    def __repr__(self):
        return f"<{self.tag} {self.attrs.get('id', '')} children={len(self.children)}>"


def decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    # UTF-16LE without a BOM: every other byte of ASCII text is zero.
    if len(data) > 3 and data[1] == 0 and data[3] == 0:
        return data.decode("utf-16-le", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


_NAME = re.compile(r"[A-Za-z_][\w:.-]*")
_ATTR = re.compile(r"""\s*([A-Za-z_][\w:.\-]*)\s*(?:=\s*("[^"]*"|'[^']*'|[^\s>"']+))?""", re.S)


def parse(text: str) -> Node:
    """Parse WMS text into a tree; returns a synthetic ROOT node."""
    root = Node("ROOT")
    stack = [root]
    i, n = 0, len(text)
    while i < n:
        lt = text.find("<", i)
        if lt < 0:
            break
        if text.startswith("<!--", lt):
            end = text.find("-->", lt + 4)
            i = n if end < 0 else end + 3
            continue
        if text.startswith("<?", lt) or text.startswith("<!", lt):
            end = text.find(">", lt)
            i = n if end < 0 else end + 1
            continue
        if text.startswith("</", lt):
            m = _NAME.match(text, lt + 2)
            end = text.find(">", lt)
            i = n if end < 0 else end + 1
            if m:
                tag = m.group(0).upper()
                # Pop to the matching open tag; ignore stray closers.
                for depth in range(len(stack) - 1, 0, -1):
                    if stack[depth].tag == tag:
                        del stack[depth:]
                        break
            continue
        m = _NAME.match(text, lt + 1)
        if not m:
            i = lt + 1
            continue
        node = Node(m.group(0).upper(), parent=stack[-1])
        j = m.end()
        self_closing = False
        # Attributes: quoted values may contain '>' (script code), so walk them.
        while j < n:
            while j < n and text[j].isspace():
                j += 1
            if j >= n:
                break
            if text[j] == ">":
                j += 1
                break
            if text.startswith("/>", j):
                self_closing = True
                j += 2
                break
            am = _ATTR.match(text, j)
            if not am or am.end() == j:
                j += 1          # junk character; skip it like WMP did
                continue
            key, val = am.group(1).lower(), am.group(2)
            if val is None:
                val = "true"
            elif val[:1] in "\"'":
                val = val[1:-1]
            node.attrs[key] = val
            j = am.end()
        stack[-1].children.append(node)
        if not self_closing:
            stack.append(node)
        i = j
    return root
