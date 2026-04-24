/**
 * Client-side privacy toggle for dashboards.
 *
 * Reads preference from localStorage, masks [data-sensitive] elements,
 * and disables Chart.js tooltips/axis labels when privacy is on.
 *
 * Usage: include this script, then call initPrivacyToggle() after charts
 * are created, passing the array of Chart.js instances.
 */
(function () {
  var STORAGE_KEY = 'dashboard_privacy';

  function isPrivacyOn() {
    return localStorage.getItem(STORAGE_KEY) !== 'off';
  }

  function applyPrivacy(charts) {
    var on = isPrivacyOn();

    // Mask or reveal [data-sensitive] elements
    document.querySelectorAll('[data-sensitive]').forEach(function (el) {
      if (on) {
        if (!el.dataset.originalText) {
          el.dataset.originalText = el.textContent;
        }
        el.textContent = '\u2022\u2022\u2022\u2022\u2022';
      } else if (el.dataset.originalText) {
        el.textContent = el.dataset.originalText;
      }
    });

    // Update chart instances (supports both Chart.js and ApexCharts)
    (charts || []).forEach(function (chart) {
      if (chart.update && chart.options && chart.options.plugins) {
        // Chart.js instance
        chart.options.plugins.tooltip.enabled = !on;
        if (chart.options.scales && chart.options.scales.y) {
          if (on) {
            chart.options.scales.y.ticks.callback = function () { return ''; };
          } else {
            delete chart.options.scales.y.ticks.callback;
          }
        }
        chart.update('none');
      } else if (chart.updateOptions) {
        // ApexCharts instance
        chart.updateOptions({
          yaxis: { labels: { show: !on } },
          tooltip: { enabled: !on },
        }, false, false);
      }
    });

    // Update toggle button appearance
    var btn = document.getElementById('privacyToggle');
    if (btn) {
      btn.classList.toggle('btn-secondary', on);
      btn.classList.toggle('btn-outline-secondary', !on);
      btn.title = on ? 'Show values' : 'Hide values';
      var eyeOpen = btn.querySelector('.privacy-eye-open');
      var eyeClosed = btn.querySelector('.privacy-eye-closed');
      if (eyeOpen) eyeOpen.style.display = on ? 'none' : 'inline';
      if (eyeClosed) eyeClosed.style.display = on ? 'inline' : 'none';
    }
  }

  function toggle(charts) {
    var on = isPrivacyOn();
    localStorage.setItem(STORAGE_KEY, on ? 'off' : 'on');
    applyPrivacy(charts);
  }

  // Expose globally
  window.dashboardPrivacy = {
    isOn: isPrivacyOn,
    apply: applyPrivacy,
    toggle: toggle
  };
})();
