/**
 * Shared chart helpers for dashboards.
 */
var DashboardCharts = (function () {
  function parseJSON(id) {
    var el = document.getElementById(id);
    if (!el || !el.textContent.trim()) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  return {
    parseJSON: parseJSON,
  };
})();
