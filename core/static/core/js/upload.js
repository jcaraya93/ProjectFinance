(function() {
  var config = document.getElementById('js-config').dataset;
  var fileInput = document.getElementById('file-input');
  var importBtn = document.getElementById('import-btn');
  var progressSection = document.getElementById('progress-section');
  var progressBar = document.getElementById('progress-bar');
  var progressLabel = document.getElementById('progress-label');
  var progressCount = document.getElementById('progress-count');
  var results = document.getElementById('results');
  var summary = document.getElementById('summary');
  var summaryContent = document.getElementById('summary-content');
  var csrfToken = config.csrfToken;
  var uploadUrl = config.uploadUrl;
  var statementListUrl = config.statementListUrl;

  fileInput.addEventListener('change', function() {
    importBtn.disabled = fileInput.files.length === 0;
    results.innerHTML = '';
    summary.classList.add('d-none');
    progressSection.classList.add('d-none');
  });

  importBtn.addEventListener('click', async function() {
    var files = Array.from(fileInput.files);
    if (!files.length) return;

    importBtn.disabled = true;
    fileInput.disabled = true;
    progressSection.classList.remove('d-none');
    results.innerHTML = '';
    summary.classList.add('d-none');

    var imported = 0, skipped = 0, failed = 0, totalTxns = 0;

    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      var pct = Math.round(((i) / files.length) * 100);
      progressBar.style.width = pct + '%';
      progressLabel.textContent = 'Importing ' + file.name + '...';
      progressCount.textContent = (i + 1) + ' / ' + files.length;

      // Add a pending row
      var row = document.createElement('div');
      row.className = 'alert alert-light py-2 px-3 mb-2 d-flex align-items-center';
      row.innerHTML =
        '<div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>' +
        '<span class="fw-semibold">' + file.name + '</span>' +
        '<span class="ms-auto text-muted small">importing...</span>';
      results.appendChild(row);

      try {
        var formData = new FormData();
        formData.append('file', file);

        var resp = await fetch(uploadUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
          body: formData,
        });

        var data = await resp.json();

        if (!resp.ok) {
          row.className = 'alert alert-danger py-2 px-3 mb-2';
          row.innerHTML = '<span class="fw-semibold">' + file.name + '</span>' +
            '<span class="ms-auto text-danger small">' + (data.error || 'Import failed') + '</span>';
          failed++;
        } else if (data.status === 'skipped') {
          var reason = data.reason === 'duplicate' ? 'Already imported' : 'No transactions';
          row.className = 'alert alert-warning py-2 px-3 mb-2';
          row.innerHTML = '<span class="fw-semibold">' + file.name + '</span>' +
            '<span class="ms-auto text-warning small">' + reason + '</span>';
          skipped++;
        } else {
          row.className = 'alert alert-success py-2 px-3 mb-2';
          var warnings = data.warnings && data.warnings.length
            ? '<br><small class="text-warning">' + data.warnings.join('<br>') + '</small>' : '';
          row.innerHTML = '<span class="fw-semibold">' + file.name + '</span>' +
            '<span class="ms-auto text-success small">' +
              data.transaction_count + ' txns &middot; ' + data.classified_count + ' classified &middot; ' + data.elapsed_ms + 'ms' +
            '</span>' + warnings;
          imported++;
          totalTxns += data.transaction_count;
        }
      } catch (err) {
        row.className = 'alert alert-danger py-2 px-3 mb-2';
        row.innerHTML = '<span class="fw-semibold">' + file.name + '</span>' +
          '<span class="ms-auto text-danger small">Network error</span>';
        failed++;
      }
    }

    // Done
    progressBar.style.width = '100%';
    progressBar.classList.add(failed ? 'bg-warning' : 'bg-success');
    progressLabel.textContent = 'Done!';

    summary.classList.remove('d-none');
    summaryContent.innerHTML =
      '<div>' +
        '<strong>' + totalTxns + '</strong> transactions from <strong>' + imported + '</strong> file' + (imported !== 1 ? 's' : '') +
        (skipped ? ' &middot; ' + skipped + ' skipped' : '') +
        (failed ? ' &middot; <span class="text-danger">' + failed + ' failed</span>' : '') +
      '</div>' +
      '<a href="' + statementListUrl + '" class="btn btn-sm btn-outline-primary">' +
        'View Statements &rarr;' +
      '</a>';

    fileInput.disabled = false;
    fileInput.value = '';
    importBtn.disabled = true;
  });
})();
