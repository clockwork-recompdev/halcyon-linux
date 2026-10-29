// Halcyon Clock gadget. Faces are drawn as SVG; settings: face, name, time zone, seconds hand.
var NS = "http://www.w3.org/2000/svg";
var FACES = {
  aero:    { rim: ["#8ad8ff", "#1b6aa6"], face: ["#ffffff", "#d8ecf8"], tick: "#2a4a66", hour: "#1d3347", sec: "#e0412b", text: "#2a4a66", gloss: .45 },
  night:   { rim: ["#5b6b7f", "#11161d"], face: ["#24303d", "#0c1117"], tick: "#8fd3ff", hour: "#e8f6ff", sec: "#39b6ff", text: "#9fdcff", gloss: .18 },
  classic: { rim: ["#f4f4f4", "#9a9a9a"], face: ["#ffffff", "#f1f1ec"], tick: "#222", hour: "#111", sec: "#c0281b", text: "#333", gloss: .25, numbers: true },
  leaf:    { rim: ["#b8ec84", "#3d8a1e"], face: ["#fbfff3", "#e2f4cb"], tick: "#2f5e19", hour: "#26431a", sec: "#e08a1b", text: "#2f5e19", gloss: .4 }
};
var hands = {}, zone = "", face, timer;

function el(tag, attrs, parent) {
  var e = document.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

function draw() {
  var s = document.getElementById("c");
  while (s.firstChild) s.removeChild(s.firstChild);
  face = FACES[System.Gadget.Settings.readString("face")] || FACES.aero;
  var defs = el("defs", {}, s);
  var g1 = el("linearGradient", { id: "rim", x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
  el("stop", { offset: 0, "stop-color": face.rim[0] }, g1);
  el("stop", { offset: 1, "stop-color": face.rim[1] }, g1);
  var g2 = el("radialGradient", { id: "face", cx: "50%", cy: "38%", r: "65%" }, defs);
  el("stop", { offset: 0, "stop-color": face.face[0] }, g2);
  el("stop", { offset: 1, "stop-color": face.face[1] }, g2);
  var g3 = el("linearGradient", { id: "gloss", x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
  el("stop", { offset: 0, "stop-color": "#fff", "stop-opacity": face.gloss }, g3);
  el("stop", { offset: 1, "stop-color": "#fff", "stop-opacity": 0 }, g3);

  var cx = 65, cy = 65;
  el("circle", { cx: cx, cy: cy, r: 62, fill: "url(#rim)" }, s);
  el("circle", { cx: cx, cy: cy, r: 61, fill: "none", stroke: "rgba(255,255,255,.5)", "stroke-width": 1 }, s);
  el("circle", { cx: cx, cy: cy, r: 54, fill: "url(#face)" }, s);
  for (var i = 0; i < 60; i++) {
    var a = i * Math.PI / 30, big = i % 5 === 0;
    var r1 = big ? 44 : 49, r2 = 52;
    el("line", { x1: cx + r1 * Math.sin(a), y1: cy - r1 * Math.cos(a), x2: cx + r2 * Math.sin(a), y2: cy - r2 * Math.cos(a),
                 stroke: face.tick, "stroke-width": big ? 2.4 : 0.9, "stroke-linecap": "round", opacity: big ? 1 : .6 }, s);
    if (big && face.numbers) {
      var t = el("text", { x: cx + 36 * Math.sin(a), y: cy - 36 * Math.cos(a) + 4, "text-anchor": "middle",
                           "font-size": 11, fill: face.text }, s);
      t.textContent = i === 0 ? 12 : i / 5;
    }
  }
  var name = System.Gadget.Settings.readString("name");
  if (name) {
    var n = el("text", { x: cx, y: cy + 24, "text-anchor": "middle", "font-size": 9.5, fill: face.text, opacity: .85 }, s);
    n.textContent = name.length > 16 ? name.slice(0, 15) + "…" : name;
  }
  hands.h = el("line", { x1: cx, y1: cy + 7, x2: cx, y2: cy - 29, stroke: face.hour, "stroke-width": 4.2, "stroke-linecap": "round" }, s);
  hands.m = el("line", { x1: cx, y1: cy + 9, x2: cx, y2: cy - 42, stroke: face.hour, "stroke-width": 2.8, "stroke-linecap": "round" }, s);
  hands.s = el("line", { x1: cx, y1: cy + 12, x2: cx, y2: cy - 46, stroke: face.sec, "stroke-width": 1.2, "stroke-linecap": "round" }, s);
  hands.s.style.display = System.Gadget.Settings.read("seconds") === false ? "none" : "";
  el("circle", { cx: cx, cy: cy, r: 3.6, fill: face.sec }, s);
  el("circle", { cx: cx, cy: cy, r: 1.4, fill: "#fff" }, s);
  el("path", { d: "M 14 60 A 51 51 0 0 1 116 60 Q 65 46 14 60 Z", fill: "url(#gloss)" }, s);
  tick();
}

function now() {
  if (!zone) return new Date();
  return new Date(System.Time.getLocalTime(zone));
}

function rot(h, deg) { h.setAttribute("transform", "rotate(" + deg + " 65 65)"); }

function tick() {
  var d = now();
  var s = d.getSeconds(), m = d.getMinutes() + s / 60, h = (d.getHours() % 12) + m / 60;
  rot(hands.h, h * 30);
  rot(hands.m, m * 6);
  rot(hands.s, s * 6);
  document.title = d.toLocaleTimeString();
}

function init() {
  System.Gadget.settingsUI = "settings.html";
  System.Gadget.onSettingsClosed = function (e) { if (e.closeAction == e.Action.commit) load(); };
  load();
  timer = setInterval(tick, 1000);
}

function load() {
  zone = System.Gadget.Settings.readString("zone");
  draw();
}
