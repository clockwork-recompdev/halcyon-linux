// Windows Sidebar gadget runtime for Chromium: the System.* object model and
// the Internet Explorer behaviour old gadgets were written against.
// Injected into every gadget page before its own scripts run.
(function () {
  "use strict";
  var ORIGIN = "%ORIGIN%", TOKEN = "%TOKEN%";
  if (location.origin !== ORIGIN) return;
  var seg = location.pathname.split("/");
  if (seg[1] !== TOKEN || !seg[2] || seg[2] === "api" || seg[2] === "proxy") return;
  var INST = seg[2];
  var ROOT = ORIGIN + "/" + TOKEN + "/" + INST;
  var API = ORIGIN + "/" + TOKEN + "/api";
  var KIND = window.name === "__flyout" ? "flyout" : window.name === "__settings" ? "settings" : "gadget";

  var XHR = window.XMLHttpRequest, xopen = XHR.prototype.open;
  function call(f, args) {
    var x = new XHR();
    xopen.call(x, "POST", API, false);
    x.send(JSON.stringify({ i: INST, f: f, a: args || [] }));
    var o = JSON.parse(x.responseText || "{}");
    if (o.e) throw new Error(o.e);
    return o.r;
  }
  function post(f, args) {
    var x = new XHR();
    xopen.call(x, "POST", API, true);
    x.send(JSON.stringify({ i: INST, f: f, a: args || [] }));
  }
  function coll(arr, wrap) {
    arr = (arr || []).map(wrap || function (v) { return v; });
    arr.count = arr.length;
    arr.item = function (i) { return arr[i]; };
    return arr;
  }
  var FS = ORIGIN + "/" + TOKEN + "/fs";
  function fsurl(p) { return p && p.charAt(0) === "/" ? FS + encodeURI(p) : p; }
  function url(p) {
    p = fromXGadget(String(p || "")).replace(/\\/g, "/");
    try { return new URL(p, location.href).href; } catch (e) { return p; }
  }

  // Inside Sidebar, a gadget's own files had x-gadget:///path URLs, and img.src
  // read back that way; some gadgets cut it up with substring().
  var PAGE_DIR = ROOT + "/" + (seg[3] && seg.length > 4 ? seg[3] + "/" : "");
  function toXGadget(u) {
    u = String(u);
    if (u.indexOf(PAGE_DIR) === 0) return "x-gadget:///" + u.slice(PAGE_DIR.length);
    if (u.indexOf(ROOT + "/") === 0) return "x-gadget:///" + u.slice(ROOT.length + 1);
    return u;
  }
  function fromXGadget(u) {
    return typeof u === "string" && /^x-gadget:\/\/\//i.test(u) ? PAGE_DIR + u.slice(12).replace(/\\/g, "/") : u;
  }
  [HTMLImageElement.prototype].forEach(function (proto) {
    var d = Object.getOwnPropertyDescriptor(proto, "src");
    Object.defineProperty(proto, "src", {
      configurable: true, enumerable: d.enumerable,
      get: function () { return toXGadget(d.get.call(this)); },
      set: function (v) { d.set.call(this, fromXGadget(v)); },
    });
  });

  // ---------------------------------------------------- network: like IE ---
  // Gadgets fetched from anywhere; send other sites through the host.
  XHR.prototype.open = function (method, u) {
    var a = Array.prototype.slice.call(arguments);
    try {
      var t = new URL(String(u).replace(/\\/g, "/"), location.href);
      if (t.origin !== ORIGIN && /^https?:$/.test(t.protocol))
        a[1] = ORIGIN + "/" + TOKEN + "/proxy?u=" + encodeURIComponent(t.href);
    } catch (e) {}
    return xopen.apply(this, a);
  };

  // -------------------------------------------------------- IE behaviour ---
  var EP = EventTarget.prototype;
  if (!EP.attachEvent) {
    EP.attachEvent = function (name, fn) {
      this.addEventListener(String(name).replace(/^on/i, ""), fn, false);
      return true;
    };
    EP.detachEvent = function (name, fn) {
      this.removeEventListener(String(name).replace(/^on/i, ""), fn, false);
    };
  }
  function define(proto, name, desc) {
    if (!(name in proto)) Object.defineProperty(proto, name, Object.assign({ configurable: true }, desc));
  }
  define(Element.prototype, "currentStyle", { get: function () { return getComputedStyle(this); } });
  define(Element.prototype, "setCapture", { value: function () {} });
  define(Element.prototype, "releaseCapture", { value: function () {} });
  define(Document.prototype, "releaseCapture", { value: function () {} });
  define(Document.prototype, "parentWindow", { get: function () { return this.defaultView; } });
  define(Element.prototype, "filters", {
    get: function () {
      var el = this;
      var alpha = {
        apply: function () {}, play: function () {}, stop: function () {},
        get opacity() { var o = parseFloat(getComputedStyle(el).opacity); return isNaN(o) ? 100 : o * 100; },
        set opacity(v) { el.style.opacity = v / 100; },
        enabled: true,
      };
      // IE's filters collection: el.filters(0), el.filters.item(0), el.filters.alpha
      var fc = function () { return alpha; };
      fc.item = fc;
      fc.alpha = alpha;
      fc.Alpha = alpha;
      fc["DXImageTransform.Microsoft.Alpha"] = alpha;
      Object.defineProperty(fc, "length", { value: 1 });
      return fc;
    },
  });
  var CSD = CSSStyleDeclaration.prototype;
  [["pixelLeft", "left"], ["pixelTop", "top"], ["pixelWidth", "width"], ["pixelHeight", "height"],
   ["pixelRight", "right"], ["pixelBottom", "bottom"], ["posLeft", "left"], ["posTop", "top"],
   ["posWidth", "width"], ["posHeight", "height"]].forEach(function (p) {
    define(CSD, p[0], {
      get: function () { return parseFloat(this[p[1]]) || 0; },
      set: function (v) { this[p[1]] = (parseFloat(v) || 0) + "px"; },
    });
  });
  // filter: alpha(opacity=50) -> opacity; other DirectX filters are dropped. CSS
  // properties are per-object here, so wrap element.style to catch the assignment.
  function ieFilter(style, v) {
    var m = /alpha\s*\(\s*opacity\s*=\s*(\d+)/i.exec(String(v));
    if (m) { style.opacity = m[1] / 100; return true; }
    return /progid:|alpha\s*\(/i.test(String(v));
  }
  // IE (quirks) took lengths without units anywhere: "left: 24", "background: -24 -24 url(x)".
  var UNITLESS = /^(opacity|z-?index|zoom|line-?height|font-?weight|flex|flex-?grow|flex-?shrink|order|orphans|widows|column-?count|fill-?opacity|stroke-?opacity)$/i;
  function addPx(v) { return String(v).replace(/(^|[\s,(])(-?\d*\.?\d+)(?=$|[\s,)])/g, function (_, a, n) { return a + n + "px"; }); }
  function bareNumbers(v) { return /(^|[\s,])-?\d*\.?\d+($|[\s,])/.test(String(v)); }
  function fixDeclarations(el) {
    var raw = el.getAttribute && el.getAttribute("style");
    if (!raw || !/\d/.test(raw)) return;
    var st = styleDesc.get.call(el);
    raw.split(";").forEach(function (decl) {
      var i = decl.indexOf(":");
      if (i < 0) return;
      var prop = decl.slice(0, i).trim().toLowerCase(), val = decl.slice(i + 1).trim();
      if (!prop || UNITLESS.test(prop) || !bareNumbers(val) || st.getPropertyValue(prop)) return;
      // A shorthand that was dropped leaves its longhands empty too.
      st.setProperty(prop, addPx(val));
    });
  }
  function fixTree(root) {
    if (root.nodeType !== 1) return;
    fixDeclarations(root);
    var l = root.querySelectorAll("[style]");
    for (var i = 0; i < l.length; i++) fixDeclarations(l[i]);
  }
  new MutationObserver(function (records) {
    records.forEach(function (r) {
      if (r.type === "attributes") fixDeclarations(r.target);
      else for (var i = 0; i < r.addedNodes.length; i++) fixTree(r.addedNodes[i]);
    });
  }).observe(document, { childList: true, subtree: true, attributes: true, attributeFilter: ["style"] });
  var styleDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "style");
  if (styleDesc && styleDesc.get && window.Proxy) {
    var wrapped = new WeakMap();
    Object.defineProperty(HTMLElement.prototype, "style", {
      configurable: true,
      get: function () {
        var real = styleDesc.get.call(this), p = wrapped.get(real);
        if (!p) {
          p = new Proxy(real, {
            get: function (t, k) { var v = t[k]; return typeof v === "function" ? v.bind(t) : v; },
            set: function (t, k, v) {
              if (k === "filter" && ieFilter(t, v)) return true;
              t[k] = v;
              if (typeof k === "string" && v !== "" && v !== null && t[k] === "" && !UNITLESS.test(k) && bareNumbers(v))
                t[k] = addPx(v);
              return true;
            },
          });
          wrapped.set(real, p);
        }
        return p;
      },
      set: function (v) { styleDesc.set.call(this, v); },
    });
  }
  function ieFiltersInMarkup() {
    var els = document.querySelectorAll("[style*='alpha']");
    for (var i = 0; i < els.length; i++) {
      var m = /alpha\s*\(\s*opacity\s*=\s*(\d+)/i.exec(els[i].getAttribute("style"));
      if (m) els[i].style.opacity = m[1] / 100;
    }
    var css = "", sheets = document.getElementsByTagName("style"), re = /([^{}]+)\{[^}]*?filter\s*:\s*alpha\s*\(\s*opacity\s*=\s*(\d+)/gi, r;
    for (i = 0; i < sheets.length; i++)
      while ((r = re.exec(sheets[i].textContent))) css += r[1] + "{opacity:" + r[2] / 100 + "}";
    if (css) { var st = document.createElement("style"); st.textContent = css; (document.head || document.documentElement).appendChild(st); }
  }
  Date.prototype.getYear = function () { return this.getFullYear(); };   // IE: full year
  ["userLanguage", "browserLanguage", "systemLanguage"].forEach(function (k) {
    define(Navigator.prototype, k, { get: function () { return navigator.language; } });
  });

  // XML: MSXML's selectNodes/selectSingleNode/.text/.xml on standard DOM nodes.
  function xpath(node, expr, single) {
    var doc = node.nodeType === 9 ? node : node.ownerDocument;
    var resolver = doc.createNSResolver(doc.documentElement || node);
    var r = doc.evaluate(expr, node, resolver,
      single ? XPathResult.FIRST_ORDERED_NODE_TYPE : XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    if (single) return r.singleNodeValue;
    var out = [];
    for (var i = 0; i < r.snapshotLength; i++) out.push(r.snapshotItem(i));
    return coll(out);
  }
  define(Node.prototype, "selectNodes", { value: function (e) { return xpath(this, e, false); } });
  define(Node.prototype, "selectSingleNode", { value: function (e) { return xpath(this, e, true); } });
  define(Node.prototype, "text", {
    get: function () { return this.textContent; }, set: function (v) { this.textContent = v; },
  });
  define(Node.prototype, "xml", { get: function () { return new XMLSerializer().serializeToString(this); } });

  function xmlDocument() {
    var d = document.implementation.createDocument("", "", null);
    d.async = true;
    d.validateOnParse = false;
    d.resolveExternals = false;
    d.preserveWhiteSpace = false;
    d.parseError = { errorCode: 0, reason: "", line: 0 };
    Object.defineProperty(d, "readyState", { value: 0, writable: true, configurable: true });
    d.onreadystatechange = null;
    d.setProperty = function () {};
    d.loadXML = function (s) {
      var p = new DOMParser().parseFromString(String(s).replace(/^\uFEFF/, ""), "text/xml");
      while (d.firstChild) d.removeChild(d.firstChild);
      var bad = p.getElementsByTagName("parsererror").length;
      d.parseError = bad ? { errorCode: 1, reason: "XML parse error", line: 0 } : { errorCode: 0, reason: "" };
      if (!bad) for (var n = p.firstChild; n; n = n.nextSibling) d.appendChild(d.importNode(n, true));
      d.readyState = 4;
      return !bad;
    };
    d.load = function (u) {
      var x = new XMLHttpRequest();
      var done = function () {
        var ok = x.status >= 200 && x.status < 300 && d.loadXML(x.responseText);
        if (typeof d.onreadystatechange === "function") d.onreadystatechange();
        if (typeof d.onload === "function") d.onload();
        return ok;
      };
      x.open("GET", url(u), !!d.async);
      if (d.async) { x.onload = done; x.onerror = done; x.send(); return true; }
      try { x.send(); } catch (e) { return false; }
      return done();
    };
    return d;
  }

  window.Enumerator = function (c) {
    this._c = c || [];
    this._i = 0;
  };
  Enumerator.prototype = {
    _n: function () { var c = this._c; return c.count !== undefined ? c.count : c.length || 0; },
    atEnd: function () { return this._i >= this._n(); },
    moveNext: function () { this._i++; },
    moveFirst: function () { this._i = 0; },
    item: function () { var c = this._c; return typeof c.item === "function" ? c.item(this._i) : c[this._i]; },
  };
  window.VBArray = function (a) { this._a = a; };
  VBArray.prototype.toArray = function () { return Array.prototype.slice.call(this._a || []); };
  VBArray.prototype.ubound = function () { return (this._a || []).length - 1; };
  VBArray.prototype.lbound = function () { return 0; };

  // COM objects (ActiveX, the gadget API) answer to any capitalisation:
  // fso.fileexists, stream.close and System.Gadget.settings all work on Windows.
  function ci(obj) {
    if (!obj || typeof obj !== "object" || obj.__ci) return obj;
    var keys = null;
    function find(t, k) {
      if (typeof k !== "string" || k in t) return k;
      var low = k.toLowerCase();
      for (var key in t) if (key.toLowerCase() === low) return key;
      return k;
    }
    return new Proxy(obj, {
      get: function (t, k) {
        if (k === "__ci") return true;
        var v = t[find(t, k)];
        if (v && typeof v === "object" && (Object.getPrototypeOf(v) === Object.prototype || Array.isArray(v))) return ci(v);
        return v;
      },
      set: function (t, k, v) { t[find(t, k)] = v; return true; },
      has: function (t, k) { return find(t, k) in t; },
    });
  }
  function fail(msg) { var e = new Error(msg); e.number = -2146828235; e.description = msg; throw e; }

  // Shell items, in both the gadget API's spelling and FileSystemObject's.
  function shellItem(o) {
    if (!o) return null;
    var it = {
      name: o.name, path: fsurl(o.path), type: o.type, isFolder: o.isFolder, isLink: o.isLink,
      isFileSystem: true, size: o.size, modifyDate: new Date(o.modifyDate),
      Name: o.name, Path: fsurl(o.path), ShortPath: fsurl(o.path), Type: o.type, Size: o.size,
      DateLastModified: new Date(o.modifyDate), DateCreated: new Date(o.modifyDate), Attributes: o.isFolder ? 16 : 32,
      metadata: function (k) {
        k = String(k).toLowerCase();
        if (k === "size") return o.size;
        if (k === "datemodified") return new Date(o.modifyDate);
        return "";
      },
      invokeVerb: function () { call("shell.execute", [o.path, "", "", "open"]); },
      OpenAsTextStream: function (mode) { return textStream(o.path, mode || 1); },
      Delete: function () { call("fs.delete", [o.path]); },
      get ParentFolder() { return shellItem(call("shell.item", [o.path.replace(/\/[^\/]*$/, "") || "/"])); },
    };
    if (o.isFolder) {
      Object.defineProperty(it, "SHFolder", { get: function () { return shellFolder(o.path); } });
      Object.defineProperty(it, "Files", {
        get: function () { return coll(call("shell.list", [o.path]).filter(function (i) { return !i.isFolder; }), shellItem); },
      });
      Object.defineProperty(it, "SubFolders", {
        get: function () { return coll(call("shell.list", [o.path]).filter(function (i) { return i.isFolder; }), shellItem); },
      });
      it.CreateTextFile = function (n) { return textStream(o.path + "/" + n, 2); };
      it.IsRootFolder = o.path === "/";
    }
    return ci(it);
  }
  function shellFolder(path) {
    return ci({
      path: path, Title: String(path).split("/").pop(), Self: shellItem(call("shell.item", [path])),
      get Items() { return coll(call("shell.list", [path]), shellItem); },
      ParseName: function (n) { return shellItem(call("shell.item", [path + "/" + n])); },
      parse: function (n) { return shellItem(call("shell.item", [path + "/" + n])); },
    });
  }
  function textStream(p, mode) {
    var text = mode === 1 ? call("fs.read", [p]) : "", pos = 0, buf = "", line = 1;
    if (text === null) fail("File not found");
    if (mode === 2) call("fs.write", [p, ""]);
    var close = function () { if (buf) call("fs.write", [p, buf, true]); buf = ""; };
    return ci({
      get AtEndOfStream() { return pos >= text.length; },
      get AtEndOfLine() { return pos >= text.length || text.charAt(pos) === "\n" || text.charAt(pos) === "\r"; },
      get Line() { return line; },
      ReadAll: function () { var r = text.slice(pos); pos = text.length; return r; },
      ReadLine: function () {
        var i = text.indexOf("\n", pos), r = text.slice(pos, i < 0 ? text.length : i);
        pos = i < 0 ? text.length : i + 1;
        line++;
        return r.replace(/\r$/, "");
      },
      Read: function (n) { var r = text.substr(pos, n); pos += r.length; return r; },
      Skip: function (n) { pos += n; }, SkipLine: function () { this.ReadLine(); },
      Write: function (s) { buf += s; },
      WriteLine: function (s) { buf += (s === undefined ? "" : s) + "\r\n"; },
      WriteBlankLines: function (n) { for (var i = 0; i < n; i++) buf += "\r\n"; },
      Close: close,
    });
  }
  function must(p, kind) {
    var o = call("shell.item", [p]);
    if (!o || (kind === "dir") !== !!o.isFolder) fail(kind === "dir" ? "Path not found" : "File not found");
    return shellItem(o);
  }
  var FSO = ci({
    FileExists: function (p) { return call("fs.exists", [p, "file"]); },
    FolderExists: function (p) { return call("fs.exists", [p, "dir"]); },
    DriveExists: function (d) { return call("drives").some(function (x) { return x.letter === String(d).charAt(0).toUpperCase(); }); },
    GetFile: function (p) { return must(p, "file"); },
    GetFolder: function (p) { return must(p, "dir"); },
    CreateFolder: function (p) { call("fs.mkdir", [p]); return must(p, "dir"); },
    OpenTextFile: function (p, mode, create) {
      mode = mode || 1;
      if (mode !== 1 && !call("fs.exists", [p, "file"])) call("fs.write", [p, ""]);
      if (mode === 1 && create && !call("fs.exists", [p, "file"])) call("fs.write", [p, ""]);
      return textStream(p, mode === 8 ? 8 : mode);
    },
    CreateTextFile: function (p) { return textStream(p, 2); },
    BuildPath: function (a, b) { return String(a).replace(/[\\\/]$/, "") + "\\" + b; },
    GetFileName: function (p) { return String(p).split(/[\\\/]/).pop(); },
    GetBaseName: function (p) { return String(p).split(/[\\\/]/).pop().replace(/\.[^.]*$/, ""); },
    GetExtensionName: function (p) { var m = /\.([^.\\\/]*)$/.exec(p); return m ? m[1] : ""; },
    GetParentFolderName: function (p) { return String(p).replace(/[\\\/][^\\\/]*$/, ""); },
    GetAbsolutePathName: function (p) { return p; },
    GetSpecialFolder: function (n) { return shellItem(call("shell.item", [n === 2 ? "/tmp" : call("env", ["HOME"])])); },
    GetTempName: function () { return "rad" + Math.random().toString(36).slice(2, 7).toUpperCase() + ".tmp"; },
    GetDrive: function (d) { return drive(String(d).charAt(0)); },
    get Drives() { return coll(call("drives"), function (x) { return drive(x.letter); }); },
    DeleteFile: function (p) { call("fs.delete", [p]); }, DeleteFolder: function (p) { call("fs.delete", [p]); },
    CopyFile: function () {}, MoveFile: function () {}, CopyFolder: function () {}, MoveFolder: function () {},
  });
  function drive(letter) {
    letter = String(letter).toUpperCase();
    var d = call("wmi", ["SELECT * FROM Win32_LogicalDisk WHERE DeviceID='" + letter + ":'"])[0] || {};
    return ci({
      DriveLetter: letter, Path: letter + ":", IsReady: !!d.Size, DriveType: d.DriveType === 2 ? 1 : 2,
      TotalSize: d.Size || 0, FreeSpace: d.FreeSpace || 0, AvailableSpace: d.FreeSpace || 0,
      VolumeName: d.VolumeName || "", FileSystem: d.FileSystem || "", SerialNumber: 0,
      get RootFolder() { return shellItem(call("shell.item", [letter + ":\\"])); },
    });
  }
  function env(n) { return call("env", [n]); }
  // A few registry values gadgets read to decide what the host supports.
  var REG = {
    "hklm\\software\\microsoft\\internet explorer\\svcversion": "11.0.9600.0",
    "hklm\\software\\microsoft\\internet explorer\\version": "9.11.9600.0",
    "hklm\\software\\microsoft\\windows nt\\currentversion\\currentversion": "6.1",
    "hklm\\software\\microsoft\\windows nt\\currentversion\\productname": "Windows 7",
    "hklm\\software\\microsoft\\windows nt\\currentversion\\currentbuildnumber": "7601",
  };
  var WShell = ci({
    Run: function (cmd) {
      var m = /^\s*(?:"([^"]+)"|(\S+))\s*(.*)$/.exec(String(cmd));
      return m && call("shell.execute", [m[1] || m[2], m[3] || "", "", "open"]) ? 0 : 1;
    },
    Exec: function (cmd) { WShell.Run(cmd); return ci({ Status: 1, ExitCode: 0, StdOut: ci({ ReadAll: function () { return ""; }, AtEndOfStream: true }) }); },
    ExpandEnvironmentStrings: function (s) { return String(s).replace(/%([^%]+)%/g, function (_, n) { return env(n); }); },
    RegRead: function (key) {
      var k = String(key).toLowerCase().replace(/^hkey_local_machine/, "hklm").replace(/^hkey_current_user/, "hkcu");
      if (k in REG) return REG[k];
      fail("Unable to open registry key \"" + key + "\" for reading.");
    },
    RegWrite: function () {}, RegDelete: function () {},
    Popup: function (msg) { alert(msg); return 1; },
    SpecialFolders: function (n) { return call("shell.known", [n]); },
    Environment: function () { return ci({ item: env, Item: env }); },
    get CurrentDirectory() { return call("env", ["HOME"]); },
    CreateShortcut: function () { return ci({ Save: function () {}, TargetPath: "", Arguments: "" }); },
    AppActivate: function () { return false; },
    SendKeys: function () {},
  });
  var ShellApp = ci({
    ShellExecute: function (f, a, d, op) { call("shell.execute", [f, a || "", d || "", op || "open"]); },
    NameSpace: function (p) {
      if (typeof p === "number") p = call("shell.known", [{ 39: "pictures", 13: "music", 14: "videos", 5: "documents", 0: "desktop", 16: "desktop", 40: "profile" }[p] || "profile"]);
      var f = shellFolder(p);
      f.Items = function () { return coll(call("shell.list", [p]), shellItem); };
      return f;
    },
    BrowseForFolder: function (h, title) {
      var r = call("shell.choose", ["folder", title || "", ""]);
      return r ? ShellApp.NameSpace(r) : null;
    },
    Open: function (p) { call("shell.execute", [p, "", "", "open"]); },
    Explore: function (p) { call("shell.execute", [p, "", "", "open"]); },
    MinimizeAll: function () {}, UndoMinimizeALL: function () {}, ShutdownWindows: function () {},
  });

  // WMI: the monitoring gadgets read CPU, disks, network and processes through it.
  function wmiObject(row, cls) {
    var o = {};
    for (var k in row) o[k] = row[k];
    o.Path_ = { Class: cls, RelPath: cls };
    o.Properties_ = coll(Object.keys(row).map(function (k) { return { Name: k, Value: row[k] }; }));
    o.Properties_.Item = function (n) { return { Name: n, Value: o[n] }; };
    return ci(o);
  }
  function wmiSet(rows, cls) {
    var set = coll(rows, function (r) { return wmiObject(r, cls); });
    set.Count = set.count;
    set.ItemIndex = set.item;
    return ci(set);
  }
  var WMI = ci({
    ExecQuery: function (q) {
      var m = /from\s+(\w+)/i.exec(String(q));
      return wmiSet(call("wmi", [String(q)]), m ? m[1] : "");
    },
    InstancesOf: function (cls) { return wmiSet(call("wmi.class", [cls]), cls); },
    Get: function (path) {
      var m = /^(\w+)(?:\.(\w+)\s*=\s*['"]?([^'"]*)['"]?)?/.exec(String(path));
      if (!m) fail("Invalid object path");
      var rows = m[2] ? call("wmi", ["SELECT * FROM " + m[1] + " WHERE " + m[2] + "='" + m[3] + "'"]) : call("wmi.class", [m[1]]);
      if (!rows.length) fail("Not found");
      return wmiObject(rows[0], m[1]);
    },
    ExecNotificationQuery: function () { return ci({ NextEvent: function () { fail("Timed out"); } }); },
  });
  var Locator = ci({ ConnectServer: function () { return WMI; }, Security_: ci({ ImpersonationLevel: 3 }) });

  window.ActiveXObject = function (prog) {
    var p = String(prog).toLowerCase();
    if (/xmlhttp/.test(p)) return new XMLHttpRequest();
    if (/domdocument|xmldom/.test(p)) return xmlDocument();
    if (/filesystemobject/.test(p)) return FSO;
    if (/wscript\.shell/.test(p)) return WShell;
    if (/shell\.application/.test(p)) return ShellApp;
    if (/wbemscripting\.swbemlocator/.test(p)) return Locator;
    if (/wmplayer\.ocx|mediaplayer/.test(p)) return ci(wmp());
    if (/scripting\.dictionary/.test(p)) return dictionary();
    if (/adodb\.stream/.test(p)) return ci({ Open: function () {}, Close: function () {}, Type: 1, Charset: "", WriteText: function () {}, ReadText: function () { return ""; }, SaveToFile: function () {}, LoadFromFile: function () {} });
    fail("Automation server can't create object");
  };
  window.GetObject = function (name) {
    if (/^winmgmts:/i.test(String(name))) return WMI;
    fail("Automation server can't create object");
  };
  function dictionary() {
    var map = {};
    return ci({
      Add: function (k, v) { if (k in map) fail("This key is already associated with an element of this collection"); map[k] = v; },
      Exists: function (k) { return k in map; },
      Item: function (k, v) { if (arguments.length > 1) map[k] = v; return map[k]; },
      Remove: function (k) { delete map[k]; }, RemoveAll: function () { map = {}; },
      Keys: function () { return new VBArray(Object.keys(map)); },
      Items: function () { return new VBArray(Object.keys(map).map(function (k) { return map[k]; })); },
      get Count() { return Object.keys(map).length; },
      CompareMode: 0,
    });
  }

  // ------------------------------------------------- IE7 document mode ---
  // Sidebar rendered gadgets like IE7: collections can be called like
  // functions (list(0)), element.all exists, getElementById ignores case and
  // also finds name="", and events can be fired with fireEvent.
  function callable(c) {
    if (!c || typeof c === "function") return c;
    var f = function (i, j) {
      if (typeof i === "number") return c[i] || null;
      var hits = Array.prototype.filter.call(c, function (e) { return e.id === i || e.name === i; });
      if (j !== undefined) return hits[j] || null;
      return hits.length > 1 ? callable(hits) : hits[0] || null;
    };
    return new Proxy(f, {
      get: function (t, k) {
        if (k === "tags") return function (tag) { return callable(Array.prototype.filter.call(c, function (e) { return e.tagName && e.tagName.toLowerCase() === String(tag).toLowerCase(); })); };
        if (k === "length") return c.length;
        if (k === Symbol.iterator) return Array.prototype[Symbol.iterator].bind(c);
        var v = c[k];
        if (v === undefined && typeof k === "string" && !/^\d+$/.test(k) && typeof c.namedItem === "function") v = c.namedItem(k) || undefined;
        return typeof v === "function" ? v.bind(c) : v;
      },
      has: function (t, k) { return k in c; },
      ownKeys: function () { return Reflect.ownKeys(c); },
      getOwnPropertyDescriptor: function (t, k) {
        var d = Reflect.getOwnPropertyDescriptor(c, k);
        if (d) d.configurable = true;
        return d;
      },
    });
  }
  function wrapMethod(proto, name) {
    var orig = proto[name];
    if (typeof orig !== "function") return;
    proto[name] = function () { return callable(orig.apply(this, arguments)); };
  }
  function wrapGetter(proto, name) {
    var d = Object.getOwnPropertyDescriptor(proto, name);
    if (!d || !d.get) return;
    Object.defineProperty(proto, name, { configurable: true, enumerable: d.enumerable, get: function () { return callable(d.get.call(this)); } });
  }
  ["getElementsByTagName", "getElementsByName", "getElementsByClassName"].forEach(function (n) {
    wrapMethod(Document.prototype, n);
    wrapMethod(Element.prototype, n);
  });
  wrapGetter(Element.prototype, "children");
  wrapGetter(Node.prototype, "childNodes");
  ["forms", "images", "links", "anchors", "scripts"].forEach(function (n) { wrapGetter(Document.prototype, n); });
  wrapGetter(HTMLTableElement.prototype, "rows");
  wrapGetter(HTMLTableSectionElement.prototype, "rows");
  wrapGetter(HTMLTableRowElement.prototype, "cells");
  wrapGetter(HTMLSelectElement.prototype, "options");
  define(Element.prototype, "all", { get: function () { return callable(this.getElementsByTagName("*")); } });
  if (window.HTMLAllCollection && !HTMLAllCollection.prototype.tags)
    HTMLAllCollection.prototype.tags = function (tag) { return callable(document.getElementsByTagName(tag)); };

  var byId = Document.prototype.getElementById;
  Document.prototype.getElementById = function (id) {
    var self = this instanceof Document ? this : document;     // IE allowed var $ = document.getElementById
    var e = byId.call(self, id);
    if (e || id === null || id === undefined) return e;
    var low = String(id).toLowerCase(), all = self.getElementsByTagName("*");
    for (var i = 0; i < all.length; i++)
      if ((all[i].id && all[i].id.toLowerCase() === low) || all[i].getAttribute("name") === String(id)) return all[i];
    return null;
  };

  var createEl = Document.prototype.createElement;
  Document.prototype.createElement = function (tag) {
    var self = this instanceof Document ? this : document;
    if (typeof tag === "string" && /^\s*</.test(tag)) {        // IE: createElement("<input type='radio'>")
      var t = createEl.call(self, "template");
      t.innerHTML = tag;
      return document.adoptNode(t.content.firstElementChild || createEl.call(self, "span"));
    }
    return createEl.apply(self, arguments);
  };
  function fire(target, name, ev) {
    var type = String(name).replace(/^on/i, "");
    var e = new Event(type, { bubbles: true, cancelable: true });
    if (ev) for (var k in ev) try { e[k] = ev[k]; } catch (x) {}
    return target.dispatchEvent(e);
  }
  define(Element.prototype, "fireEvent", { value: function (n, e) { return fire(this, n, e); } });
  define(Document.prototype, "fireEvent", { value: function (n, e) { return fire(this, n, e); } });
  define(Document.prototype, "createEventObject", { value: function () { return {}; } });
  define(Element.prototype, "contains", { value: Node.prototype.contains });
  define(Element.prototype, "removeNode", { value: function (deep) { var p = this.parentNode; if (p) p.removeChild(this); return this; } });
  define(Element.prototype, "swapNode", { value: function (o) {
    var a = this.nextSibling, p = this.parentNode;
    o.parentNode.replaceChild(this, o);
    p.insertBefore(o, a);
    return this;
  } });
  define(HTMLElement.prototype, "runtimeStyle", { get: function () { return this.style; } });
  define(Element.prototype, "sourceIndex", { get: function () { return Array.prototype.indexOf.call(document.getElementsByTagName("*"), this); } });


  // Built-ins for VBScript blocks the server translated to JavaScript.
  function sysFirstDay() {                       // 1 = Sunday ... 7 = Saturday, from the locale
    try {
      var l = new Intl.Locale(navigator.language), w = l.getWeekInfo ? l.getWeekInfo() : l.weekInfo;
      if (w && w.firstDay) return w.firstDay % 7 + 1;
    } catch (e) {}
    return 1;
  }
  function vbDate(d) { return d instanceof Date ? d : new Date(d); }
  window.__vb = {
    DateSerial: function (y, m, d) { return new Date(y, m - 1, d); },
    Date: function () { var d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate()); },
    Now: function () { return new Date(); },
    Time: function () { return new Date(); },
    Timer: function () { var d = new Date(); return (d - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 1000; },
    Year: function (d) { return vbDate(d).getFullYear(); },
    Month: function (d) { return vbDate(d).getMonth() + 1; },
    Day: function (d) { return vbDate(d).getDate(); },
    Hour: function (d) { return vbDate(d).getHours(); },
    Minute: function (d) { return vbDate(d).getMinutes(); },
    Second: function (d) { return vbDate(d).getSeconds(); },
    WeekDay: function (d, first) {
      first = first === undefined ? 1 : first === 0 ? sysFirstDay() : first;
      return ((vbDate(d).getDay() + 1 - first + 7) % 7) + 1;
    },
    MonthName: function (m, abbr) { return new Date(2000, m - 1, 1).toLocaleDateString(undefined, { month: abbr ? "short" : "long" }); },
    WeekDayName: function (w, abbr, first) {
      first = first === undefined ? 1 : first === 0 ? sysFirstDay() : first;
      return new Date(2000, 0, 2 + (w - 1 + first - 1) % 7).toLocaleDateString(undefined, { weekday: abbr ? "short" : "long" });   // Jan 2 2000 = Sunday
    },
    DateAdd: function (unit, n, d) {
      var x = new Date(vbDate(d)), u = String(unit).toLowerCase();
      if (u === "yyyy") x.setFullYear(x.getFullYear() + n); else if (u === "m") x.setMonth(x.getMonth() + n);
      else if (u === "d" || u === "y" || u === "w") x.setDate(x.getDate() + n); else if (u === "ww") x.setDate(x.getDate() + 7 * n);
      else if (u === "h") x.setHours(x.getHours() + n); else if (u === "n") x.setMinutes(x.getMinutes() + n); else if (u === "s") x.setSeconds(x.getSeconds() + n);
      return x;
    },
    DateDiff: function (unit, a, b) {
      a = vbDate(a); b = vbDate(b);
      var ms = { d: 864e5, y: 864e5, w: 6048e5, ww: 6048e5, h: 36e5, n: 6e4, s: 1e3 }[String(unit).toLowerCase()];
      if (ms) return Math.floor((b - a) / ms);
      if (unit === "m") return (b.getFullYear() - a.getFullYear()) * 12 + b.getMonth() - a.getMonth();
      return b.getFullYear() - a.getFullYear();
    },
    FormatDateTime: function (d, f) {
      d = vbDate(d);
      return f === 1 ? d.toLocaleDateString(undefined, { dateStyle: "long" }) : f === 2 ? d.toLocaleDateString() :
             f === 3 ? d.toLocaleTimeString() : f === 4 ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString();
    },
    Len: function (s) { return s === null || s === undefined ? 0 : String(s).length; },
    Left: function (s, n) { return String(s).slice(0, n); },
    Right: function (s, n) { s = String(s); return n ? s.slice(-n) : ""; },
    Mid: function (s, i, n) { return n === undefined ? String(s).slice(i - 1) : String(s).substr(i - 1, n); },
    UCase: function (s) { return String(s).toUpperCase(); }, LCase: function (s) { return String(s).toLowerCase(); },
    Trim: function (s) { return String(s).trim(); }, LTrim: function (s) { return String(s).replace(/^\s+/, ""); },
    RTrim: function (s) { return String(s).replace(/\s+$/, ""); },
    InStr: function (a, b, c) { if (c === undefined) return String(a).indexOf(b) + 1; return String(b).indexOf(c, a - 1) + 1; },
    InStrRev: function (a, b) { return String(a).lastIndexOf(b) + 1; },
    Replace: function (s, a, b) { return String(s).split(a).join(b); },
    StrReverse: function (s) { return String(s).split("").reverse().join(""); },
    Space: function (n) { return new Array(n + 1).join(" "); },
    String: function (n, c) { return new Array(n + 1).join(String(c).charAt(0)); },
    CStr: String, CDbl: Number, CDate: vbDate, CBool: Boolean,
    CInt: function (x) { return Math.round(Number(x)); }, CLng: function (x) { return Math.round(Number(x)); },
    Int: Math.floor, Fix: Math.trunc, Abs: Math.abs, Sqr: Math.sqrt, Rnd: Math.random,
    Round: function (x, n) { var p = Math.pow(10, n || 0); return Math.round(x * p) / p; },
    IsNumeric: function (x) { return x !== "" && x !== null && !isNaN(Number(x)); },
    IsEmpty: function (x) { return x === undefined || x === ""; }, IsNull: function (x) { return x === null; },
    IsArray: Array.isArray, IsDate: function (x) { return !isNaN(vbDate(x)); },
    FormatNumber: function (x, n) { return Number(x).toLocaleString(undefined, { minimumFractionDigits: n === undefined ? 2 : n, maximumFractionDigits: n === undefined ? 2 : n }); },
    FormatPercent: function (x, n) { return (Number(x) * 100).toFixed(n === undefined ? 2 : n) + "%"; },
    Chr: function (c) { return String.fromCharCode(c); }, ChrW: function (c) { return String.fromCharCode(c); },
    Asc: function (s) { return String(s).charCodeAt(0); }, AscW: function (s) { return String(s).charCodeAt(0); },
    Hex: function (x) { return Number(x).toString(16).toUpperCase(); }, Oct: function (x) { return Number(x).toString(8); },
    Split: function (s, d) { return String(s).split(d === undefined ? " " : d); },
    Join: function (a, d) { return a.join(d === undefined ? " " : d); },
    UBound: function (a) { return a.length - 1; }, LBound: function () { return 0; },
    Array: function () { return Array.prototype.slice.call(arguments); },
    MsgBox: function (m) { alert(m); return 1; },
    TypeName: function (x) { return x === null ? "Null" : x === undefined ? "Empty" : typeof x === "string" ? "String" : typeof x === "number" ? "Double" : x instanceof Date ? "Date" : "Object"; },
    VarType: function (x) { return typeof x === "string" ? 8 : typeof x === "number" ? 5 : 9; },
    Eval: function (x) { return (0, eval)(x); }, Escape: escape, Unescape: unescape,
  };
  // ---------------------------------------------------------- System.* ---
  if (KIND !== "gadget" && window.opener && window.opener.System && window.opener.System.Gadget) {
    // Flyouts and the settings page share the gadget's System object (as on Windows).
    window.System = window.opener.System;
  } else {
    var flyWin = null, flyFile = "", bgLayer = null, bgValue = "";
    var Settings = {
      read: function (n) { var v = call("settings.read", [n]); return v === null || v === undefined ? "" : v; },
      readString: function (n) { var v = call("settings.read", [n]); return v === null || v === undefined ? "" : String(v); },
      write: function (n, v) { call("settings.write", [n, v === undefined ? null : v]); },
      writeString: function (n, v) { call("settings.write", [n, String(v)]); },
    };
    Settings.ClosingEvent = { Action: { cancel: 0, commit: 1 } };
    var Flyout = {
      onShow: null, onHide: null,
      get file() { return flyFile; },
      set file(v) { flyFile = v; },
      get show() { return !!(flyWin && !flyWin.closed); },
      set show(v) {
        if (v) {
          var target = url(flyFile);
          if (flyWin && !flyWin.closed) { if (flyWin.location.href !== target) flyWin.location.href = target; return; }
          call("popup", ["flyout"]);
          flyWin = window.open(target, "__flyout");
        } else if (flyWin && !flyWin.closed) {
          flyWin.close();
          flyWin = null;
        }
      },
      get document() { return flyWin && !flyWin.closed ? flyWin.document : null; },
    };
    var state = call("state");
    var Gadget = {
      name: state.name, version: state.version, path: ROOT, platformVersion: "1.0.0.0",
      get docked() { return call("state").docked; },
      get visible() { return true; },
      get opacity() { return call("opacity"); },
      settingsUI: "", onDock: null, onUndock: null, onSettingsClosed: null, onSettingsClosing: null,
      onShowSettings: null, onVisibilityChange: null,
      beginTransition: function () {},
      endTransition: function () {},
      close: function () { call("close"); },
      get background() { return bgValue; },
      set background(v) {
        bgValue = String(v || "");
        var m = /url\(\s*['"]?([^'")]*)['"]?\s*\)/i.exec(bgValue);
        var src = m ? m[1] : bgValue;
        if (!bgLayer) {
          bgLayer = document.createElement("div");
          bgLayer.style.cssText = "position:absolute;left:0;top:0;right:0;bottom:0;z-index:-1;pointer-events:none;background-repeat:no-repeat";
          (document.body || document.documentElement).insertBefore(bgLayer, (document.body || document.documentElement).firstChild);
        }
        bgLayer.style.backgroundImage = src ? "url('" + url(src) + "')" : "none";
      },
      get document() { return document; },
      Settings: Settings, Flyout: Flyout,
      Sidebar: { dockSide: "Right", onDockSideChanged: null },
      TransitionType: { none: 0, morph: 1 },
      SettingsClosingEvent: Settings.ClosingEvent,
    };
    var Machine = {
      get CPUs() { return coll(call("machine").cpus); },
      get totalMemory() { return call("machine").totalMemory; },
      get availableMemory() { return call("machine").availableMemory; },
      get processorArchitecture() { return call("machine").processorArchitecture; },
      get processorSpeed() { return call("machine").processorSpeed; },
      PowerStatus: {
        onPowerLineStatusChanged: null, onBatteryStatusChanged: null,
      },
    };
    ["batteryPercentRemaining", "batteryCapacityRemaining", "batteryCapacityTotal", "isBatteryCharging",
     "isPowerLineConnected", "batteryStatus", "timeRemaining", "totalBatteryLifeTime"].forEach(function (k) {
      Object.defineProperty(Machine.PowerStatus, k, { get: function () { return call("power")[k]; } });
    });
    var Time = {
      get currentTimeZone() { return call("tz.current"); },
      get timeZones() { return coll(call("tz.all")); },
      getLocalTime: function (tz) { return call("tz.local", [tz && tz.name !== undefined ? tz.name : tz || ""]); },
    };
    var Shell = {
      execute: function (f, a, d, op) { return call("shell.execute", [f, a || "", d || "", op || "open"]); },
      itemFromPath: function (p) { return shellItem(call("shell.item", [p])); },
      knownFolderPath: function (n) { return fsurl(call("shell.known", [n])); },
      knownFolder: function (n) { var p = call("shell.known", [n]); return p ? shellFolder(p) : null; },
      chooseFile: function (open, filter, initial) {
        var r = call("shell.choose", [open === false ? "save" : "file", String(filter || ""), String(initial || "")]);
        return r ? shellItem(call("shell.item", [r]) || { name: r.split("/").pop(), path: r }) : null;
      },
      chooseFolder: function (title) {
        var r = call("shell.choose", ["folder", String(title || ""), ""]);
        return r ? shellFolder(r) : null;
      },
      drive: function () { return { driveLetter: "C", driveType: 3, freeSpace: 0, totalSize: 0, volumeLabel: "Halcyon" }; },
      refreshDesktop: function () {},
      saveFileDialog: function (initial, filter) { return call("shell.choose", ["save", String(filter || ""), String(initial || "")]) || ""; },
      RecycleBin: {
        get fileCount() { return call("trash").fileCount; },
        get folderCount() { return call("trash").folderCount; },
        get sizeUsed() { return call("trash").folderSize; },
        get folderSize() { return call("trash").folderSize; },
        emptyAll: function () { call("trash.empty"); },
        showRecycleSettings: function () {},
        deleteItem: function () {}, undoDelete: function () {},
        onRecycleBinChanged: null,
      },
    };
    window.System = {
      Gadget: Gadget, Machine: Machine, Time: Time, Shell: Shell,
      Network: {
        Wireless: {
          get ssid() { return call("wireless").ssid; },
          get signalStrength() { return call("wireless").signalStrength; },
          get instance() { return "Wi-Fi"; },
        },
      },
      Sound: {
        playSound: function (p) { call("sound", [url(p)]); },
        beep: function () {},
      },
      Debug: { outputString: function (s) { post("debug", [String(s)]); } },
      Environment: {
        getEnvironmentVariable: env,
        get machineName() { return env("COMPUTERNAME"); },
      },
      Diagnostics: { EventLog: { writeEntry: function () {} } },
      Contact: {}, MessageStore: {},
    };
    window.System = ci(window.System);
  }

  // --------------------------------------------------------- g: elements ---
  function gObject(parent, tag, src, x, y) {
    var el = document.createElement(tag === "text" ? "span" : "img");
    el.style.position = "absolute";
    el.style.left = (x || 0) + "px";
    el.style.top = (y || 0) + "px";
    el.style.whiteSpace = "nowrap";
    parent.appendChild(el);
    var o = { element: el, rotation: 0, blur: 0, brightness: 0, opacity: 100 };
    var props = {
      src: { get: function () { return el.getAttribute("src") || ""; }, set: function (v) { el.src = url(v); } },
      left: { get: function () { return parseFloat(el.style.left) || 0; }, set: function (v) { el.style.left = v + "px"; } },
      top: { get: function () { return parseFloat(el.style.top) || 0; }, set: function (v) { el.style.top = v + "px"; } },
      width: { get: function () { return el.offsetWidth; }, set: function (v) { el.style.width = v + "px"; } },
      height: { get: function () { return el.offsetHeight; }, set: function (v) { el.style.height = v + "px"; } },
      opacity: { get: function () { return (parseFloat(el.style.opacity || "1")) * 100; }, set: function (v) { el.style.opacity = v / 100; } },
      rotation: { get: function () { return o._rot || 0; }, set: function (v) { o._rot = v; el.style.transform = "rotate(" + v + "deg)"; } },
      value: { get: function () { return el.textContent; }, set: function (v) { el.textContent = v; } },
      font: { get: function () { return el.style.fontFamily; }, set: function (v) { el.style.fontFamily = v; } },
      fontsize: { get: function () { return parseFloat(el.style.fontSize) || 0; }, set: function (v) { el.style.fontSize = v + "pt"; } },
      color: { get: function () { return el.style.color; }, set: function (v) { el.style.color = v; } },
      align: { get: function () { return 0; }, set: function () {} },
      visible: { get: function () { return el.style.display !== "none"; }, set: function (v) { el.style.display = v ? "" : "none"; } },
    };
    delete o.rotation; delete o.opacity;
    aliases(o, props);
    o.addGlow = function (c, r) { el.style.filter = "drop-shadow(0 0 " + (r || 2) + "px " + (c || "white") + ")"; };
    o.addShadow = function (c, r, a, x, y) { el.style.filter = "drop-shadow(" + (x || 1) + "px " + (y || 1) + "px " + (r || 2) + "px " + (c || "black") + ")"; };
    o.removeObjects = function () {};
    if (tag === "text") o.value = src; else if (src) o.src = src;
    return o;
  }
  function upgrade(el) {
    if (el.__g) return;
    el.__g = true;
    var tag = el.localName.replace(/^g:/, "");
    // <g:image ... /> is not self-closing to an HTML parser: everything after it
    // became its children. Move them back out, as IE had them.
    var after = el.nextSibling;
    while (el.firstElementChild) {
      var kid = el.lastChild;
      if (kid.nodeType === 3 && tag === "text") break;
      el.parentNode.insertBefore(kid, after);
      after = kid;
    }
    if (tag === "background") {
      el.style.position = "absolute";
      el.style.left = el.style.left || "0";
      el.style.top = el.style.top || "0";
      el.style.zIndex = el.style.zIndex || "-1";
      el.style.display = "block";
      el.style.backgroundRepeat = "no-repeat";
      el.style.backgroundSize = "100% 100%";
      var setSrc = function (v) {
        el.__src = v;
        var m = /^\s*url\(\s*['"]?([^'")]*)['"]?\s*\)\s*$/i.exec(String(v || ""));
        if (m) v = m[1];
        if (!v) { el.style.backgroundImage = "none"; return; }
        var u = url(v);
        el.style.backgroundImage = "url('" + u + "')";
        if (!el.style.width || !el.style.height) {
          var im = new Image();
          im.onload = function () {
            if (!el.style.width) el.style.width = im.naturalWidth + "px";
            if (!el.style.height) el.style.height = im.naturalHeight + "px";
          };
          im.src = u;
        }
      };
      Object.defineProperty(el, "src", { configurable: true, get: function () { return el.__src || ""; }, set: setSrc });
      Object.defineProperty(el, "opacity", {
        configurable: true,
        get: function () { return (parseFloat(el.style.opacity || "1")) * 100; },
        set: function (v) { el.style.opacity = v / 100; },
      });
      el.addImageObject = function (src, x, y) { return gObject(el, "image", src, x, y); };
      el.addTextObject = function (t, font, size, color, x, y) {
        var o = gObject(el, "text", t, x, y);
        if (font) o.font = font;
        if (size) o.fontsize = size;
        if (color) o.color = color;
        return o;
      };
      el.removeObjects = function () { while (el.firstChild) el.removeChild(el.firstChild); };
      setSrc(el.getAttribute("src"));
    } else if (tag === "image" || tag === "text") {
      // The element itself is drawn: its style (left/top/z-index...) keeps working.
      gElement(el, tag);
    }
  }
  var G_PROPS = ["src", "left", "top", "width", "height", "opacity", "rotation", "value", "font", "fontsize",
                 "color", "align", "blur", "brightness", "visible"];
  function aliases(target, getset) {
    G_PROPS.forEach(function (k) {
      var d = getset[k];
      if (!d) return;
      [k, k.charAt(0).toUpperCase() + k.slice(1)].forEach(function (name) {
        Object.defineProperty(target, name, { configurable: true, get: d.get, set: d.set });
      });
    });
  }
  function gElement(el, tag) {
    var st = el.style, rot = 0, natural = null, opacity = 100;
    if (!/position/.test(st.cssText)) st.position = "absolute";
    st.display = "block";
    st.whiteSpace = "nowrap";
    st.pointerEvents = "none";
    var transform = function () { st.transform = rot ? "rotate(" + rot + "deg)" : ""; };
    var props = {
      left: { get: function () { return parseFloat(st.left) || 0; }, set: function (v) { st.left = (parseFloat(v) || 0) + "px"; } },
      top: { get: function () { return parseFloat(st.top) || 0; }, set: function (v) { st.top = (parseFloat(v) || 0) + "px"; } },
      width: { get: function () { return parseFloat(st.width) || el.offsetWidth; }, set: function (v) { st.width = (parseFloat(v) || 0) + "px"; } },
      height: { get: function () { return parseFloat(st.height) || el.offsetHeight; }, set: function (v) { st.height = (parseFloat(v) || 0) + "px"; } },
      opacity: { get: function () { return opacity; }, set: function (v) { opacity = parseFloat(v); st.opacity = opacity / 100; } },
      rotation: { get: function () { return rot; }, set: function (v) { rot = parseFloat(v) || 0; transform(); } },
      blur: { get: function () { return 0; }, set: function () {} },
      brightness: { get: function () { return 0; }, set: function () {} },
      visible: { get: function () { return st.visibility !== "hidden"; }, set: function (v) { st.visibility = v ? "" : "hidden"; } },
      align: { get: function () { return 0; }, set: function () {} },
    };
    if (tag === "image") {
      var src = "";
      st.backgroundRepeat = "no-repeat";
      st.backgroundSize = "100% 100%";
      props.src = {
        get: function () { return src; },
        set: function (v) {
          src = String(v || "");
          var m = /^\s*url\(\s*['"]?([^'")]*)['"]?\s*\)\s*$/i.exec(src), u = url(m ? m[1] : src);
          st.backgroundImage = src ? "url('" + u + "')" : "none";
          if (!src) return;
          var im = new Image();
          im.onload = function () {
            natural = [im.naturalWidth, im.naturalHeight];
            if (!el.__w) st.width = natural[0] + "px";
            if (!el.__h) st.height = natural[1] + "px";
          };
          im.src = u;
        },
      };
      el.__w = !!st.width;
      el.__h = !!st.height;
      var w0 = props.width.set, h0 = props.height.set;
      props.width.set = function (v) { el.__w = true; w0(v); };
      props.height.set = function (v) { el.__h = true; h0(v); };
    } else {
      props.value = { get: function () { return el.textContent; }, set: function (v) { el.textContent = v; } };
      props.font = { get: function () { return st.fontFamily; }, set: function (v) { st.fontFamily = v; } };
      props.fontsize = { get: function () { return parseFloat(st.fontSize) || 0; }, set: function (v) { st.fontSize = v + "pt"; } };
      props.color = { get: function () { return st.color; }, set: function (v) { st.color = String(v).replace(/^Color\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)$/i, "rgba($2,$3,$4,$1)"); } };
    }
    aliases(el, props);
    var shadow = function (c, r, a, x, y) { st.filter = "drop-shadow(" + (x || 0) + "px " + (y || 0) + "px " + (r || 2) + "px " + (c || "black") + ")"; };
    el.addShadow = el.AddShadow = shadow;
    el.addGlow = el.AddGlow = function (c, r) { shadow(c || "white", r, 0, 0, 0); };
    el.removeObjects = el.RemoveObjects = function () {};
    if (tag === "image" && el.getAttribute("src")) el.src = el.getAttribute("src");
    if (el.getAttribute("opacity")) el.opacity = el.getAttribute("opacity");
    if (el.getAttribute("rotation")) el.rotation = el.getAttribute("rotation");
  }

  // Windows Media Player control (<object classid="clsid:6BF52A52-...">, or the
  // WMPlayer.OCX ActiveX object): enough of it for web radio and alert sounds.
  function wmp(target) {
    var audio = new Audio(), src = "", o = target || {};
    audio.preload = "none";
    var ps = function () { return audio.ended ? 1 : audio.paused ? (audio.currentTime ? 2 : 1) : 3; };
    var controls = ci({
      play: function () { audio.play().catch(function () {}); }, pause: function () { audio.pause(); },
      stop: function () { audio.pause(); try { audio.currentTime = 0; } catch (e) {} },
      next: function () {}, previous: function () {},
      get currentPosition() { return audio.currentTime; }, set currentPosition(v) { audio.currentTime = v; },
      get currentPositionString() { var t = Math.floor(audio.currentTime); return Math.floor(t / 60) + ":" + ("0" + t % 60).slice(-2); },
      isAvailable: function () { return true; },
    });
    var settings = ci({
      get volume() { return Math.round(audio.volume * 100); }, set volume(v) { audio.volume = Math.max(0, Math.min(1, v / 100)); },
      get mute() { return audio.muted; }, set mute(v) { audio.muted = !!v; },
      autoStart: true, setMode: function () {}, getMode: function () { return false; },
    });
    var props = {
      URL: { get: function () { return src; }, set: function (v) { src = String(v || ""); audio.src = src ? url(src) : ""; if (src && settings.autoStart !== false) controls.play(); } },
      controls: { get: function () { return controls; } },
      settings: { get: function () { return settings; } },
      playState: { get: ps },
      currentMedia: { get: function () { return ci({ name: src.split(/[\\\/]/).pop(), duration: audio.duration || 0, durationString: "", getItemInfo: function () { return ""; } }); } },
      uiMode: { get: function () { return "invisible"; }, set: function () {} },
      versionInfo: { get: function () { return "12.0.7601"; } },
      close: { value: function () { controls.stop(); audio.src = ""; } },
    };
    for (var k in props) {
      var d = props[k];
      d.configurable = true;
      Object.defineProperty(o, k, d);
      Object.defineProperty(o, k.toLowerCase(), d);
    }
    audio.addEventListener("playing", function () { if (typeof o.PlayStateChange === "function") o.PlayStateChange(3); });
    return o;
  }
  function upgradeObject(el) {
    if (el.__wmp) return;
    var cls = (el.getAttribute("classid") || "").toLowerCase();
    if (cls.indexOf("6bf52a52-394a-11d3-b153-00c04f79faa6") >= 0 || cls.indexOf("22d6f312-b0f6-11d0-94ab-0080c74c7e95") >= 0) {
      el.__wmp = true;
      wmp(el);
      var p = el.querySelector("param[name=URL i], param[name=FileName i]");
      if (p && p.getAttribute("value")) el.URL = p.getAttribute("value");
    }
  }

  // IE XML data islands: <xml id="x">...</xml> or <xml src="..."> as a parsed document.
  function xmlIsland(el) {
    if (el.__x) return;
    el.__x = true;
    var d = xmlDocument();
    if (el.getAttribute("src")) { d.async = false; d.load(el.getAttribute("src")); }
    else d.loadXML(el.innerHTML.replace(/^\s*<!--|-->\s*$/g, ""));
    Object.defineProperty(el, "XMLDocument", { configurable: true, value: d });
    Object.defineProperty(el, "documentElement", { configurable: true, get: function () { return d.documentElement; } });
    ["selectNodes", "selectSingleNode", "getElementsByTagName"].forEach(function (m) {
      Object.defineProperty(el, m, { configurable: true, value: function () { return d[m].apply(d, arguments); } });
    });
    el.style.display = "none";
  }
  function upgradeAll(root) {
    var objs = (root || document).getElementsByTagName("object");
    for (var ob = 0; ob < objs.length; ob++) upgradeObject(objs[ob]);
    var xs = (root || document).getElementsByTagName("xml");
    for (var x = 0; x < xs.length; x++) xmlIsland(xs[x]);
    ["g:background", "g:image", "g:text"].forEach(function (t) {
      var l = (root || document).getElementsByTagName(t);
      for (var i = 0; i < l.length; i++) upgrade(l[i]);
    });
  }
  new MutationObserver(function () { upgradeAll(); }).observe(document, { childList: true, subtree: true });

  // ----------------------------------------------------- host <-> page ---
  function report() {
    var b = document.body;
    if (!b) return;
    var cs = getComputedStyle(b), r = b.getBoundingClientRect();   // includes CSS zoom
    var w = r.width + (parseFloat(cs.marginLeft) || 0) + (parseFloat(cs.marginRight) || 0);
    var h = r.height + (parseFloat(cs.marginTop) || 0) + (parseFloat(cs.marginBottom) || 0);
    // A g:background larger than the body sets the gadget's size, as on Windows.
    var bgs = document.getElementsByTagName("g:background");
    for (var i = 0; i < bgs.length; i++) {
      var br = bgs[i].getBoundingClientRect();
      w = Math.max(w, br.right);
      h = Math.max(h, br.bottom);
    }
    w = Math.ceil(w); h = Math.ceil(h);
    if (w > 0 && h > 0 && (w !== report.w || h !== report.h)) {
      report.w = w; report.h = h;
      post("size", [KIND, w, h]);
    }
  }
  function event(action) {
    var A = { cancel: 0, commit: 1 };
    return { closeAction: action, Action: A, cancel: false, cancellable: true };
  }
  window.__halcyon = {
    kind: KIND,
    info: function () {
      var G = System.Gadget;
      return { settingsUI: G.settingsUI || "", undockable: typeof G.onUndock === "function" || typeof G.onDock === "function" };
    },
    openSettings: function () {
      var G = System.Gadget;
      if (typeof G.onShowSettings === "function") try { G.onShowSettings(); } catch (e) { console.error(e); }
      call("popup", ["settings"]);
      window.open(url(G.settingsUI), "__settings");
    },
    settingsClosing: function (action) {
      var ev = event(action), G = System.Gadget;
      if (typeof G.onSettingsClosing === "function") try { G.onSettingsClosing(ev); } catch (e) { console.error(e); }
      return !!ev.cancel && action === 1;
    },
    fire: function (name, action) {
      var G = System.Gadget;
      if (typeof G[name] === "function") try { G[name](event(action)); } catch (e) { console.error(e); }
      setTimeout(report, 0);
    },
    closeFlyout: function () { System.Gadget.Flyout.show = false; },
    report: report,
  };

  // Drag the gadget by any part of it once the mouse has moved a little
  // (starting the move straight away would swallow clicks).
  if (KIND === "gadget") {
    var down = null;
    window.addEventListener("mousedown", function (e) {
      var t = e.target;
      if (e.button !== 0 || (t.closest && t.closest("input,textarea,select,option,[contenteditable],object,embed"))) { down = null; return; }
      down = { x: e.screenX, y: e.screenY };
    }, true);
    window.addEventListener("mousemove", function (e) {
      if (down && (e.buttons & 1) && Math.abs(e.screenX - down.x) + Math.abs(e.screenY - down.y) > 4) {
        down = null;
        post("drag", []);
      }
    }, true);
    window.addEventListener("mouseup", function () { down = null; }, true);
  }

  if (KIND === "flyout") {
    window.addEventListener("load", function () {
      var F = System.Gadget.Flyout;
      if (typeof F.onShow === "function") try { F.onShow(); } catch (e) { console.error(e); }
    });
    window.addEventListener("pagehide", function () {
      var F = window.opener && window.opener.System && window.opener.System.Gadget.Flyout;
      if (F && typeof F.onHide === "function") try { F.onHide(); } catch (e) {}
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    upgradeAll();
    ieFiltersInMarkup();
    var st = document.createElement("style");
    st.textContent = KIND === "settings"
      ? "html{background:#fff}"
      : "html{background:transparent!important}body{margin:0;-webkit-user-select:none;user-select:none}" +
        "input,textarea{-webkit-user-select:text;user-select:text}";
    document.head ? document.head.insertBefore(st, document.head.firstChild) : document.documentElement.appendChild(st);
    report();
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(report);
      ro.observe(document.body);
      var bgs = document.getElementsByTagName("g:background");
      for (var i = 0; i < bgs.length; i++) ro.observe(bgs[i]);
    }
    setInterval(report, 1000);   // style changes that don't resize the body box, e.g. on undock
  });
  window.addEventListener("load", report);
})();
