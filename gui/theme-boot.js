// Early theme loader (plan v0.5 D4). Separate non-module script because CSP is script-src 'self' (no inline scripts);
// runs in <head> before first paint so the saved theme is on <html data-theme="..."> with no flash.
(function () {
  var KEY = 'neo.theme';
  var VALID = { 'dark-high-contrast': 1, light: 1, dayfox: 1 };
  var DEF = 'dark-high-contrast';
  function pick(v) { return VALID[v] ? v : DEF; }
  function load() {
    try { return pick(localStorage.getItem(KEY)); } catch (_) { return DEF; }
  }
  function apply(t) {
    var v = pick(t);
    document.documentElement.setAttribute('data-theme', v);
    return v;
  }
  function save(t) {
    var v = apply(t);
    try { localStorage.setItem(KEY, v); } catch (_) { /* private / disabled storage */ }
    return v;
  }
  apply(load());
  window.neoTheme = { KEY: KEY, DEFAULT: DEF, VALID: Object.keys(VALID), load: load, apply: apply, save: save };
})();
