(function() {
  var config = document.getElementById('js-config').dataset;
  var fileInput = document.getElementById('import-file');
  var importBtn = document.getElementById('import-btn');
  var progress = document.getElementById('import-progress');
  var progressBar = document.getElementById('import-progress-bar');
  var result = document.getElementById('import-result');
  var csrfToken = config.csrfToken;
  var importUrl = config.importUrl;
  var dashboardUrl = config.dashboardUrl;

  fileInput.addEventListener('change', function() {
    importBtn.disabled = fileInput.files.length === 0;
    result.innerHTML = '';
  });

  importBtn.addEventListener('click', async function() {
    var file = fileInput.files[0];
    if (!file) return;

    importBtn.disabled = true;
    fileInput.disabled = true;
    progress.classList.remove('d-none');
    result.innerHTML = '';

    try {
      var formData = new FormData();
      formData.append('file', file);

      var resp = await fetch(importUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: formData,
      });

      var data = await resp.json();

      if (!resp.ok) {
        progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
        progressBar.classList.add('bg-danger');
        result.innerHTML =
          '<div class="alert alert-danger py-2 px-3">' +
            '<strong>Import failed:</strong> ' + (data.error || 'Unknown error') +
          '</div>';
      } else {
        progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
        progressBar.classList.add('bg-success');
        var c = data.counts;
        result.innerHTML =
          '<div class="alert alert-success py-2 px-3">' +
            '<strong>Import successful!</strong> ' + data.elapsed_ms + 'ms<br>' +
            '<span class="small">' +
              c.categories + ' categories, ' +
              c.rules + ' rules, ' +
              c.accounts + ' accounts, ' +
              c.statements + ' statements, ' +
              c.logical_transactions + ' transactions, ' +
              c.exchange_rates + ' exchange rates' +
            '</span>' +
          '</div>' +
          '<a href="' + dashboardUrl + '" class="btn btn-sm btn-outline-primary">Go to Dashboard &rarr;</a>';
      }
    } catch (err) {
      progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
      progressBar.classList.add('bg-danger');
      result.innerHTML =
        '<div class="alert alert-danger py-2 px-3">' +
          '<strong>Network error.</strong> Please check your connection and try again.' +
        '</div>';
    }

    fileInput.disabled = false;
  });
})();
