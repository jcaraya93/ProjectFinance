(function () {
  // Date range presets
  document.querySelectorAll('.date-preset').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var range = this.dataset.range;
      var now = new Date();
      var start = '', end = '';
      var y = now.getFullYear(), m = now.getMonth(), d = now.getDate();

      function fmt(dt) {
        return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
      }

      if (range === 'this-month') {
        start = fmt(new Date(y, m, 1));
        end = fmt(now);
      } else if (range === 'last-month') {
        start = fmt(new Date(y, m - 1, 1));
        end = fmt(new Date(y, m, 0));
      } else if (range === '3-months') {
        start = fmt(new Date(y, m - 2, 1));
        end = fmt(now);
      } else if (range === '6-months') {
        start = fmt(new Date(y, m - 5, 1));
        end = fmt(now);
      } else if (range === 'ytd') {
        start = fmt(new Date(y, 0, 1));
        end = fmt(now);
      } else if (range === 'last-year') {
        start = fmt(new Date(y - 1, 0, 1));
        end = fmt(new Date(y - 1, 11, 31));
      } else if (range === 'all') {
        start = '';
        end = '';
      }

      document.getElementById('startDate').value = start;
      document.getElementById('endDate').value = end;
      document.getElementById('filterForm').submit();
    });
  });

  // Auto-submit for non-dropdown inputs (date pickers)
  document.querySelectorAll('.auto-submit').forEach(function (el) {
    if (!el.closest('.dropdown-menu')) {
      el.addEventListener('change', function () {
        document.getElementById('filterForm').submit();
      });
    }
  });

  // Submit when a checkbox dropdown closes
  document.querySelectorAll('.dropdown').forEach(function (dd) {
    dd.addEventListener('hidden.bs.dropdown', function () {
      document.getElementById('filterForm').submit();
    });
  });

  // All/None buttons — toggle checkboxes but don't submit (dropdown close will submit)
  document.querySelectorAll('.check-all').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      this.closest('.dropdown-menu').querySelectorAll('.form-check-input').forEach(function (cb) { cb.checked = true; });
    });
  });
  document.querySelectorAll('.check-none').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      this.closest('.dropdown-menu').querySelectorAll('.form-check-input').forEach(function (cb) { cb.checked = false; });
    });
  });

  // Group header toggle — click to select/deselect all categories in that group
  document.querySelectorAll('.group-toggle').forEach(function (header) {
    header.addEventListener('click', function () {
      var items = this.nextElementSibling;
      if (!items || !items.classList.contains('category-group-items')) return;
      var checkboxes = items.querySelectorAll('.form-check-input');
      var allChecked = Array.from(checkboxes).every(function (cb) { return cb.checked; });
      checkboxes.forEach(function (cb) { cb.checked = !allChecked; });
    });
  });

  // Column visibility toggle
  var config = document.getElementById('js-config').dataset;
  var txnTable = document.querySelector('.table-responsive table');
  function toggleColumn(colIdx, show) {
    txnTable.querySelectorAll('tr').forEach(function (row) {
      var cells = row.querySelectorAll('th, td');
      if (cells[colIdx]) {
        cells[colIdx].style.display = show ? '' : 'none';
      }
    });
  }

  // Restore saved column preferences from server
  var savedCols = JSON.parse(config.savedColumns);
  var saveTimer = null;
  function persistColumns() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      fetch(config.saveUrl, {
        method: 'POST',
        headers: {'X-CSRFToken': getCookie('csrftoken'), 'Content-Type': 'application/json'},
        body: JSON.stringify(savedCols),
      });
    }, 400);
  }
  document.querySelectorAll('.col-toggle').forEach(function (cb) {
    var col = parseInt(cb.dataset.col);
    if (savedCols[col] === false) {
      cb.checked = false;
      toggleColumn(col, false);
    }
    cb.addEventListener('change', function () {
      toggleColumn(col, this.checked);
      savedCols[col] = this.checked;
      persistColumns();
    });
  });

  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[2]) : null;
  }


})();

// Track advanced search open state
var advPanel = document.getElementById('advancedSearch');
var advField = document.getElementById('advOpenField');
advPanel.addEventListener('shown.bs.collapse', function() { advField.value = '1'; });
advPanel.addEventListener('hidden.bs.collapse', function() { advField.value = ''; });

// Bulk selection
(function() {
  var selectAll = document.getElementById('selectAll');
  var checkboxes = document.querySelectorAll('.txn-select');
  var bulkBar = document.getElementById('bulkBar');
  var bulkIds = document.getElementById('bulkIds');
  var bulkCount = document.getElementById('bulkCount');

  function updateBulkBar() {
    var checked = document.querySelectorAll('.txn-select:checked');
    if (checked.length > 0) {
      bulkBar.classList.remove('d-none');
      bulkCount.textContent = checked.length + ' selected';
      bulkIds.innerHTML = '';
      checked.forEach(function(cb) {
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'txn_ids';
        input.value = cb.value;
        bulkIds.appendChild(input);
      });
    } else {
      bulkBar.classList.add('d-none');
    }
  }

  selectAll.addEventListener('change', function() {
    checkboxes.forEach(function(cb) { cb.checked = selectAll.checked; });
    updateBulkBar();
  });

  checkboxes.forEach(function(cb) {
    cb.addEventListener('change', function() {
      if (!cb.checked) selectAll.checked = false;
      else if (document.querySelectorAll('.txn-select:checked').length === checkboxes.length) selectAll.checked = true;
      updateBulkBar();
    });
  });

  document.getElementById('bulkCancel').addEventListener('click', function() {
    selectAll.checked = false;
    checkboxes.forEach(function(cb) { cb.checked = false; });
    updateBulkBar();
  });
})();
