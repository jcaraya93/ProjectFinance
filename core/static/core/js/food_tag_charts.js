(function() {
  var data = JSON.parse(document.getElementById('foodTagData').textContent);
  var labels = data.labels;
  var colors = data.group_colors;

  function renderChart(id, datasets, type, stacked, palette) {
    var el = document.getElementById(id);
    if (!el || !labels.length || !datasets.length) return;
    var series = datasets.map(function(ds) {
      return {
        name: ds.name,
        data: ds.data.map(function(value) { return value === null ? null : Number(value); }),
      };
    });
    var chart = new ApexCharts(el, {
      chart: {
        type: type, height: 320, stacked: stacked, toolbar: { show: false },
        events: { dataPointSelection: function(event, context, config) {
          var ds = datasets[config.seriesIndex];
          var url = ds && ds.urls && ds.urls[config.dataPointIndex];
          if (url) window.location.href = url;
        } },
      },
      series: series,
      colors: palette,
      xaxis: { categories: labels },
      yaxis: { labels: { formatter: function(value) { return value.toLocaleString(undefined, { maximumFractionDigits: 2 }); } } },
      stroke: { width: type === 'line' ? 3 : 0, curve: 'straight' },
      markers: { size: type === 'line' ? 4 : 0 },
      dataLabels: { enabled: false },
      legend: { position: 'bottom' },
      tooltip: {
        shared: type !== 'line', intersect: type === 'line',
        y: { formatter: function(value) {
          return value == null ? 'No transactions' : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
        } },
      },
      grid: { borderColor: '#e9ecef' },
    });
    chart.render();
    DashboardCharts.addVisibilityToggle(chart, el, series.map(function(s) { return s.name; }));
  }

  renderChart('foodTagSpendChart', data.spend, 'bar', true, colors);
})();
