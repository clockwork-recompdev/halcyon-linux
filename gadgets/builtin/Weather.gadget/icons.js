// Weather icons drawn as SVG strings: sun, moon, clouds, rain, snow, storm, fog.
var WX = (function () {
  function sun(x, y, r) {
    var rays = "";
    for (var i = 0; i < 8; i++) {
      var a = i * Math.PI / 4;
      rays += '<line x1="' + (x + Math.cos(a) * r * 1.35) + '" y1="' + (y + Math.sin(a) * r * 1.35) + '" x2="' + (x + Math.cos(a) * r * 1.75) +
              '" y2="' + (y + Math.sin(a) * r * 1.75) + '" stroke="#ffc533" stroke-width="' + r * .28 + '" stroke-linecap="round"/>';
    }
    return rays + '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="url(#sun)"/>';
  }
  function moon(x, y, r) {
    return '<path d="M ' + (x + r * .3) + ' ' + (y - r) + ' A ' + r + ' ' + r + ' 0 1 0 ' + (x + r) + ' ' + (y + r * .45) +
           ' A ' + r * .8 + ' ' + r * .8 + ' 0 0 1 ' + (x + r * .3) + ' ' + (y - r) + ' Z" fill="url(#moon)"/>';
  }
  function cloud(x, y, s, dark) {
    return '<path transform="translate(' + x + ' ' + y + ') scale(' + s + ')" d="M -26 10 A 10 10 0 0 1 -18 -8 A 15 15 0 0 1 8 -14 A 12 12 0 0 1 24 -2 A 10 10 0 0 1 22 14 L -20 14 A 8 8 0 0 1 -26 10 Z" ' +
           'fill="url(#' + (dark ? "dcloud" : "cloud") + ')" stroke="rgba(255,255,255,.7)" stroke-width="1"/>';
  }
  function drops(x, y, s, kind) {
    var out = "";
    for (var i = 0; i < 3; i++) {
      var dx = x + (i - 1) * 12 * s, dy = y + (i % 2) * 5 * s;
      if (kind === "snow") out += '<text x="' + dx + '" y="' + (dy + 8 * s) + '" font-size="' + 13 * s + '" fill="#fff" text-anchor="middle">❄</text>';
      else out += '<line x1="' + dx + '" y1="' + dy + '" x2="' + (dx - 3 * s) + '" y2="' + (dy + 9 * s) + '" stroke="#5cc3ff" stroke-width="' + 2.6 * s + '" stroke-linecap="round"/>';
    }
    return out;
  }
  var defs = '<defs><radialGradient id="sun" cx="40%" cy="35%" r="70%"><stop offset="0" stop-color="#fff6b0"/><stop offset=".6" stop-color="#ffcf33"/><stop offset="1" stop-color="#f59a0c"/></radialGradient>' +
    '<linearGradient id="cloud" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#c9dbe9"/></linearGradient>' +
    '<linearGradient id="dcloud" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b9c6d2"/><stop offset="1" stop-color="#6c7f91"/></linearGradient>' +
    '<radialGradient id="moon" cx="40%" cy="40%" r="70%"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#cdd9e6"/></radialGradient></defs>';
  // WMO weather code -> [description, icon kind]
  function kind(code) {
    if (code === 0) return ["Clear", "clear"];
    if (code <= 2) return [code === 1 ? "Mostly clear" : "Partly cloudy", "partly"];
    if (code === 3) return ["Cloudy", "cloudy"];
    if (code <= 48) return ["Fog", "fog"];
    if (code <= 57) return ["Drizzle", "rain"];
    if (code <= 67) return [code >= 65 ? "Heavy rain" : "Rain", "rain"];
    if (code <= 77) return ["Snow", "snow"];
    if (code <= 82) return ["Rain showers", "rain"];
    if (code <= 86) return ["Snow showers", "snow"];
    return ["Thunderstorms", "storm"];
  }
  function svg(code, day, size) {
    var k = kind(code)[1], body = "";
    var orb = day ? sun(26, 24, 11) : moon(26, 24, 11);
    if (k === "clear") body = day ? sun(32, 32, 14) : moon(32, 32, 15);
    else if (k === "partly") body = orb + cloud(36, 38, .85);
    else if (k === "cloudy") body = cloud(26, 26, .7, true) + cloud(36, 36, .9);
    else if (k === "fog") body = cloud(32, 26, .8) + '<g stroke="#e3edf5" stroke-width="3" stroke-linecap="round"><line x1="12" y1="48" x2="52" y2="48"/><line x1="18" y1="55" x2="46" y2="55"/></g>';
    else if (k === "rain" || k === "snow") body = cloud(32, 26, .85, true) + drops(32, 44, 1, k);
    else body = cloud(32, 26, .85, true) + '<path d="M 34 38 L 26 50 L 32 50 L 28 60 L 40 46 L 34 46 L 38 38 Z" fill="#ffd23a" stroke="#c98a00" stroke-width=".8"/>';
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '" viewBox="0 0 64 64">' + defs + body + "</svg>";
  }
  return { svg: svg, text: function (c) { return kind(c)[0]; } };
})();
