/**
 * Shared chart initialization helpers for dashboards.
 */
var DashboardCharts = (function () {
  function parseJSON(id) {
    var el = document.getElementById(id);
    if (!el || !el.textContent.trim()) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  // Standard chart option presets
  var PRESETS = {
    base: {
      responsive: true,
      plugins: { legend: { position: 'bottom' } },
    },
    stacked: {
      responsive: true,
      plugins: { legend: { position: 'bottom' } },
      scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
    },
    horizontalStacked: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { position: 'bottom' } },
      scales: { x: { stacked: true, beginAtZero: true }, y: { stacked: true } },
    },
    doughnut: {
      responsive: true,
      plugins: { legend: { position: 'bottom' } },
    },
    noLegend: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true } },
    },
    horizontalNoLegend: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true } },
    },
  };

  function deepMerge(target, source) {
    var result = {};
    for (var key in target) {
      if (target[key] && typeof target[key] === 'object' && !Array.isArray(target[key])) {
        result[key] = deepMerge({}, target[key]);
      } else {
        result[key] = target[key];
      }
    }
    for (var key in source) {
      if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
        result[key] = deepMerge(result[key] || {}, source[key]);
      } else {
        result[key] = source[key];
      }
    }
    return result;
  }

  function create(canvasId, type, data, presetName, optionOverrides) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    var opts = PRESETS[presetName || 'base'] || PRESETS.base;
    if (optionOverrides) { opts = deepMerge(opts, optionOverrides); }
    return new Chart(canvas, { type: type, data: data, options: opts });
  }

  // Doughnut with percentage tooltip
  function createDoughnut(canvasId, data, legendPosition) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    return new Chart(canvas, {
      type: 'doughnut',
      data: data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: legendPosition || 'bottom' },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                var pct = total > 0 ? (ctx.parsed / total * 100).toFixed(1) : 0;
                return ctx.label + ': ' + ctx.parsed.toLocaleString() + ' (' + pct + '%)';
              }
            }
          }
        },
      },
    });
  }

  return {
    parseJSON: parseJSON,
    create: create,
    createDoughnut: createDoughnut,
    PRESETS: PRESETS,
    deepMerge: deepMerge,
  };
})();
