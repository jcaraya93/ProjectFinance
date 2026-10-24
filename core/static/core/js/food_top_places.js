(function() {
  var form = document.getElementById('foodTopPlacesForm');
  if (!form) return;
  var results = document.getElementById('foodTopPlacesResults');
  var status = document.getElementById('foodTopPlacesStatus');
  var select = form.elements.merchant_category;
  var button = form.querySelector('button[type="submit"]');
  var sortKey = 'total';
  var sortDirection = 'descending';

  function sortPlaces() {
    var tbody = results.querySelector('tbody');
    var rows = Array.from(tbody.querySelectorAll('tr[data-total]'));
    rows.sort(function(a, b) {
      var difference = Number(a.dataset[sortKey]) - Number(b.dataset[sortKey]);
      if (difference) return sortDirection === 'ascending' ? difference : -difference;
      return a.cells[0].textContent.localeCompare(b.cells[0].textContent);
    });
    rows.forEach(function(row) { tbody.appendChild(row); });
    results.querySelectorAll('[data-place-sort]').forEach(function(control) {
      var active = control.dataset.placeSort === sortKey;
      control.closest('th').setAttribute('aria-sort', active ? sortDirection : 'none');
      control.querySelector('[data-sort-indicator]').textContent = active ?
        (sortDirection === 'ascending' ? '\u2191' : '\u2193') : '\u2195';
    });
  }

  results.addEventListener('click', function(event) {
    var control = event.target.closest('[data-place-sort]');
    if (!control) return;
    var key = control.dataset.placeSort;
    sortDirection = key === sortKey && sortDirection === 'descending' ? 'ascending' : 'descending';
    sortKey = key;
    sortPlaces();
    status.textContent = 'Places sorted by ' + control.textContent.replace(/[\u2191\u2193\u2195]/g, '').trim() +
      ', ' + sortDirection + '.';
  });

  function updateLinks(selected) {
    document.querySelectorAll('a[href]').forEach(function(link) {
      var url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname !== window.location.pathname ||
          !url.searchParams.has('display_currency')) return;
      if (selected === 'all') url.searchParams.delete('merchant_category');
      else url.searchParams.set('merchant_category', selected);
      link.href = url.href;
    });
  }

  form.addEventListener('submit', async function(event) {
    event.preventDefault();
    if (button.disabled) return;
    var url = new URL(form.action);
    url.search = new URLSearchParams(new FormData(form)).toString();
    var requestUrl = new URL(url);
    requestUrl.hash = '';
    requestUrl.searchParams.set('partial', 'top-places');
    button.disabled = true;
    select.disabled = true;
    results.setAttribute('aria-busy', 'true');
    status.classList.remove('text-danger');
    status.textContent = 'Updating places...';
    try {
      var response = await fetch(requestUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      if (response.redirected) throw new Error('Your session may have expired. Reload the page and sign in again.');
      if (!response.ok) throw new Error('The server returned HTTP ' + response.status + '. Please try again.');
      var html = await response.text();
      var content = new DOMParser().parseFromString(html, 'text/html');
      var table = content.querySelector('[data-selected-category]');
      if (!table) throw new Error('The server returned an unexpected response. Please reload the page.');
      results.replaceChildren(table);
      sortPlaces();
      select.value = table.dataset.selectedCategory;
      if (select.value === 'all') url.searchParams.delete('merchant_category');
      else url.searchParams.set('merchant_category', select.value);
      window.history.replaceState(window.history.state, '', url);
      updateLinks(select.value);
      status.textContent = 'Places updated.';
    } catch (error) {
      select.value = results.querySelector('[data-selected-category]').dataset.selectedCategory;
      status.classList.add('text-danger');
      status.textContent = 'Could not update places. ' + error.message;
    } finally {
      button.disabled = false;
      select.disabled = false;
      results.removeAttribute('aria-busy');
    }
  });
})();
