var results = [];
function check(name, fn) {
  var ok;
  try { ok = fn(); } catch (e) { ok = false; name += " (" + e.message + ")"; }
  results.push([name, ok]);
  System.Debug.outputString((ok ? "PASS " : "FAIL ") + name);
}
function show() {
  var html = "", pass = 0;
  for (var i = 0; i < results.length; i++) {
    if (results[i][1]) pass++;
    html += "<div" + (results[i][1] ? "" : " class=f") + ">" + (results[i][1] ? "✔ " : "✘ ") + results[i][0] + "</div>";
  }
  document.getElementById("out").innerHTML = "<b>" + pass + "/" + results.length + " passed</b>" + html;
}
function run() {
  check("gadget name", function () { return System.Gadget.name == "Legacy Test"; });
  check("g:background", function () { return typeof document.getElementById("bg").addTextObject == "function"; });
  check("addTextObject", function () { var t = document.getElementById("bg").addTextObject("hi", "Tahoma", 8, "white", 100, 180); return t.value == "hi"; });
  check("attachEvent", function () {
    var hit = false; document.body.attachEvent("onclick", function () { hit = !!window.event; });
    document.body.dispatchEvent(new MouseEvent("click")); return hit;
  });
  var xml;
  check("ActiveX XMLHTTP + \\ path", function () {
    var x = new ActiveXObject("Microsoft.XMLHTTP");
    x.open("GET", System.Gadget.path + "\\data\\feed.xml", false); x.send(); xml = x.responseText; return x.status == 200;
  });
  check("XMLDOM selectNodes", function () {
    var d = new ActiveXObject("Microsoft.XMLDOM"); d.async = false; d.loadXML(xml);
    var n = d.selectNodes("//item/title"); return n.length == 2 && n.item(1).text == "Second";
  });
  check("Enumerator", function () {
    var c = System.Machine.CPUs, n = 0;
    for (var e = new Enumerator(c); !e.atEnd(); e.moveNext()) n++;
    return n == c.count && n > 0;
  });
  check("pixelLeft", function () { var s = document.getElementById("out").style; s.pixelLeft = 6; return s.left == "6px"; });
  check("filter alpha", function () { var d = document.createElement("div"); d.style.filter = "alpha(opacity=50)"; return d.style.opacity == "0.5"; });
  check("getYear", function () { return new Date().getYear() > 2000; });
  check("Settings", function () { System.Gadget.Settings.write("n", 42); return System.Gadget.Settings.read("n") === 42 && System.Gadget.Settings.readString("n") === "42"; });
  check("time zone", function () { return !!System.Time.currentTimeZone.name; });
  check("FSO FileExists", function () { return new ActiveXObject("Scripting.FileSystemObject").FileExists(System.Gadget.path + "\\Images\\bg.png"); });
  check("memory", function () { return System.Machine.totalMemory > 0; });
  check("WMI disks", function () {
    var w = GetObject("winmgmts:\\\\.\\root\\cimv2"), n = 0;
    for (var e = new Enumerator(w.ExecQuery("SELECT * FROM Win32_LogicalDisk WHERE DriveType = 3")); !e.atEnd(); e.moveNext()) n += e.item().Size > 0 ? 1 : 0;
    return n > 0;
  });
  check("COM any case", function () { var f = new ActiveXObject("Scripting.FileSystemObject"); return f.fileexists(System.Gadget.path + "\\Images\\BG.PNG"); });
  check("WMP object", function () { var m = new ActiveXObject("WMPlayer.OCX"); m.settings.volume = 30; return m.settings.volume == 30 && m.playState == 1; });
  check("IE7 getElementById", function () { return document.getElementById("OUT") === document.getElementById("out"); });
  check("callable collection", function () { return document.getElementsByTagName("div")(0) === document.getElementsByTagName("div")[0]; });
  check("x-gadget src", function () { var i = document.createElement("img"); i.src = "images/bg.png"; return i.src.indexOf("x-gadget:///") === 0; });
  show();
  var img = new Image();
  img.onload = function () { check("image via \\ path", function () { return img.width == 160; }); show(); };
  img.onerror = function () { check("image via \\ path", function () { return false; }); show(); };
  img.src = System.Gadget.path + "\\images\\bg.png";
  var r = new XMLHttpRequest();
  r.open("GET", "https://example.com/", true);
  r.onreadystatechange = function () { if (r.readyState == 4) { check("cross-site XHR", function () { return r.status == 200; }); show(); } };
  r.send();
}
