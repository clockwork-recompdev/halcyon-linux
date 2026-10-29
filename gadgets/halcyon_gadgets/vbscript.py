"""A small VBScript -> JavaScript translator for the helper blocks old gadgets
embed (<script language="VBScript">): functions, Dim, If/ElseIf/Else, For,
Do/While loops and the date/string built-ins. Built-ins are implemented in
JavaScript by shim.js as window.__vb."""
import re

BUILTINS = {n.lower(): n for n in (
    "DateSerial WeekDay Date Now Time Year Month Day Hour Minute Second MonthName WeekDayName Len Left Right Mid "
    "UCase LCase Trim LTrim RTrim InStr InStrRev Replace CStr CInt CLng CDbl CBool CDate Int Fix Abs Round Sqr "
    "IsNumeric IsEmpty IsNull IsArray IsDate FormatNumber FormatDateTime FormatPercent DateAdd DateDiff DatePart "
    "Chr ChrW Asc AscW Split Join UBound LBound Array Space String StrReverse Timer Rnd Hex Oct LCase MsgBox "
    "TypeName VarType Eval Escape Unescape").split()}
CONSTANTS = {"true": "true", "false": "false", "nothing": "null", "null": "null", "empty": '""',
             "vbcrlf": '"\\r\\n"', "vbcr": '"\\r"', "vblf": '"\\n"', "vbtab": '"\\t"', "vbnewline": '"\\r\\n"',
             "vbnullstring": '""', "vbsunday": "1", "vbmonday": "2", "vbtuesday": "3", "vbwednesday": "4",
             "vbthursday": "5", "vbfriday": "6", "vbsaturday": "7", "vbusesystemdayofweek": "0",
             "vbusesystem": "0", "vbtrue": "-1", "vbfalse": "0", "vbgeneraldate": "0", "vblongdate": "1",
             "vbshortdate": "2", "vblongtime": "3", "vbshorttime": "4"}
OPS = {"and": "&&", "or": "||", "not": "!", "mod": "%", "xor": "!=", "is": "==="}
TOKEN = re.compile(r'\s*(?:("(?:[^"]|"")*")|(\d+\.\d*|\.\d+|\d+|&H[0-9A-Fa-f]+)|([A-Za-z_]\w*)|(<>|<=|>=|[-+*/\\^&=<>(),.:]))')


def _expr(src, assign_ok=False):
    """Translate one expression. In VBScript '=' compares, except the first one of a statement."""
    out = []
    pos = 0
    first_eq = assign_ok
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m or m.end() == pos:
            out.append(src[pos:].strip())
            break
        pos = m.end()
        s, num, ident, op = m.groups()
        if s is not None:
            out.append('"' + s[1:-1].replace('""', '\\"') + '"')
        elif num is not None:
            out.append(str(int(num[2:], 16)) if num.lower().startswith("&h") else num)
        elif ident is not None:
            low = ident.lower()
            prev = out[-1] if out else ""
            if prev == ".":
                out.append(ident)
            elif low in OPS:
                out.append(" " + OPS[low] + " ")
            elif low in CONSTANTS:
                out.append(CONSTANTS[low])
            elif low in BUILTINS:
                out.append("__vb." + BUILTINS[low])
                # Built-ins called without parentheses (Date, Now, Time, Timer).
                nxt = TOKEN.match(src, pos)
                if not nxt or nxt.group(4) != "(":
                    out.append("()")
            else:
                out.append(ident)
        else:
            if op == "=":
                out.append(" = " if first_eq else " == ")
                first_eq = False
            elif op == "<>":
                out.append(" != ")
            elif op == "&":
                out.append(' + "" + ')
            elif op == "\\":
                out.append(" / ")
            elif op == "^":
                out.append(" ** ")
            else:
                out.append(op)
    return "".join(out)


def _strip_comment(line):
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == "'" and not in_str:
            return line[:i]
    if re.match(r"\s*rem\b", line, re.I):
        return ""
    return line


def translate(code):
    js = []
    func = []          # stack of (kind, name)
    lines = code.replace("\r\n", "\n").split("\n")
    # Join continuation lines ( _ at end of line).
    joined = []
    for l in lines:
        if joined and joined[-1].rstrip().endswith(" _"):
            joined[-1] = joined[-1].rstrip()[:-1] + " " + l.strip()
        else:
            joined.append(l)
    for raw in joined:
        for stmt in _split_statements(_strip_comment(raw)):
            js.append(_statement(stmt.strip(), func))
    return "\n".join(j for j in js if j)


def _split_statements(line):
    parts, cur, in_str = [], "", False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        if ch == ":" and not in_str:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _statement(s, func):
    if not s:
        return ""
    low = s.lower()
    m = re.match(r"(?i)^(?:(?:public|private)\s+)?(function|sub)\s+(\w+)\s*(?:\((.*)\))?$", s)
    if m:
        kind, name, args = m.group(1).lower(), m.group(2), m.group(3) or ""
        args = ", ".join(re.sub(r"(?i)^\s*(byval|byref)\s+", "", a).strip() for a in args.split(",") if a.strip())
        func.append((kind, name))
        return f"function {name}({args}) {{ var {name};" if kind == "function" else f"function {name}({args}) {{"
    if re.match(r"(?i)^end\s+(function|sub)$", s):
        kind, name = func.pop() if func else ("sub", "")
        return f"return {name}; }}" if kind == "function" else "}"
    if re.match(r"(?i)^exit\s+function$", s):
        return f"return {func[-1][1]};" if func else "return;"
    if re.match(r"(?i)^exit\s+sub$", s):
        return "return;"
    if re.match(r"(?i)^exit\s+(for|do)$", s):
        return "break;"
    m = re.match(r"(?i)^(?:dim|redim(?:\s+preserve)?|private|public|const)\s+(.*)$", s)
    if m:
        decls = []
        for d in re.split(r",(?![^(]*\))", m.group(1)):
            d = d.strip()
            arr = re.match(r"(\w+)\s*\((.*)\)$", d)
            if arr:
                decls.append(f"{arr.group(1)} = []")
            elif "=" in d:
                n, v = d.split("=", 1)
                decls.append(f"{n.strip()} = {_expr(v)}")
            else:
                decls.append(d)
        return "var " + ", ".join(decls) + ";"
    m = re.match(r"(?i)^if\s+(.+?)\s+then\s*(.*)$", s)
    if m:
        cond, rest = m.group(1), m.group(2)
        if not rest:
            return f"if ({_expr(cond)}) {{"
        e = re.split(r"(?i)\s+else\s+", rest, maxsplit=1)
        out = f"if ({_expr(cond)}) {{ {_statement(e[0], func)} }}"
        if len(e) > 1:
            out += f" else {{ {_statement(e[1], func)} }}"
        return out
    m = re.match(r"(?i)^elseif\s+(.+?)\s+then$", s)
    if m:
        return f"}} else if ({_expr(m.group(1))}) {{"
    if low == "else":
        return "} else {"
    if re.match(r"(?i)^end\s+if$", s) or low in ("next", "wend", "loop") or re.match(r"(?i)^next\s+\w+$", s):
        return "}"
    m = re.match(r"(?i)^for\s+(\w+)\s*=\s*(.+?)\s+to\s+(.+?)(?:\s+step\s+(.+))?$", s)
    if m:
        v, a, b, step = m.group(1), _expr(m.group(2)), _expr(m.group(3)), m.group(4)
        st = _expr(step) if step else "1"
        cmp = ">=" if step and step.strip().startswith("-") else "<="
        return f"for ({v} = {a}; {v} {cmp} {b}; {v} += {st}) {{"
    m = re.match(r"(?i)^for\s+each\s+(\w+)\s+in\s+(.+)$", s)
    if m:
        return f"for (var __e = new Enumerator({_expr(m.group(2))}); !__e.atEnd() && (({m.group(1)} = __e.item()) || true); __e.moveNext()) {{"
    m = re.match(r"(?i)^(?:do\s+while|while)\s+(.+)$", s)
    if m:
        return f"while ({_expr(m.group(1))}) {{"
    m = re.match(r"(?i)^do\s+until\s+(.+)$", s)
    if m:
        return f"while (!({_expr(m.group(1))})) {{"
    if low == "do":
        return "while (true) {"
    m = re.match(r"(?i)^loop\s+(while|until)\s+(.+)$", s)
    if m:
        c = _expr(m.group(2))
        return f"if ({'!' if m.group(1).lower() == 'while' else ''}({c})) break; }}"
    if re.match(r"(?i)^on\s+error\s+", s) or re.match(r"(?i)^option\s+explicit", s):
        return ""
    m = re.match(r"(?i)^set\s+(.+)$", s)
    if m:
        s = m.group(1)
    m = re.match(r"(?i)^call\s+(.+)$", s)
    if m:
        return _expr(m.group(1)) + ";"
    # Assignment or a call.
    if re.match(r"^[\w.()\s,\"]+?\s*=", s):
        return _expr(s, assign_ok=True) + ";"
    m = re.match(r"^([\w.]+)\s+(.+)$", s)           # Sub call without parentheses: Foo a, b
    if m and not re.match(r"(?i)^(and|or|not)$", m.group(1)):
        return f"{_expr(m.group(1))}({_expr(m.group(2))});"
    return _expr(s) + ";"


SCRIPT = re.compile(r'(<script\b[^>]*\blanguage\s*=\s*["\']?vbscript["\']?[^>]*>)(.*?)(</script\s*>)', re.I | re.S)


def rewrite_html(html):
    """Replace VBScript blocks in a page with their JavaScript translation."""
    def sub(m):
        try:
            js = translate(m.group(2))
        except Exception as e:                       # leave a clue rather than break the page
            js = f"console.error('VBScript block could not be translated: {str(e)!r}');"
        return '<script type="text/javascript">\n' + js + "\n</script>"
    return SCRIPT.sub(sub, html)
