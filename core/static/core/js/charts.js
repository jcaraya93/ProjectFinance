/**
 * Shared chart helpers for dashboards.
 */
var DashboardCharts = (function () {
  function parseJSON(id) {
    var el = document.getElementById(id);
    if (!el || !el.textContent.trim()) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  // Adds All/None buttons above a chart container to show or hide every series.
  function addVisibilityToggle(chart, container, seriesNames) {
    var group = document.createElement('div');
    group.className = 'btn-group btn-group-sm';
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Chart series visibility');
    [['All', 'showSeries'], ['None', 'hideSeries']].forEach(function (item) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-outline-secondary';
      button.textContent = item[0];
      button.addEventListener('click', function () {
        seriesNames.forEach(function (name) { chart[item[1]](name); });
      });
      group.appendChild(button);
    });
    var title = container.previousElementSibling;
    var row = document.createElement('div');
    row.className = 'd-flex justify-content-between align-items-center mb-2';
    container.parentNode.insertBefore(row, title && title.classList.contains('card-title') ? title : container);
    if (title && title.classList.contains('card-title')) {
      title.classList.add('mb-0');
      row.appendChild(title);
    }
    row.appendChild(group);
  }

  return {
    parseJSON: parseJSON,
    addVisibilityToggle: addVisibilityToggle,
  };
})();
