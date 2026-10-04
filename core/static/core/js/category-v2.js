(function () {
  function $(id) { return document.getElementById(id); }
  function all(root, sel) { return Array.prototype.slice.call(root.querySelectorAll(sel)); }

  var tabs = $('categoryGroupTabs');
  var tabButtons = all(tabs, '[data-bs-toggle="tab"]');
  var storageKey = 'category-group-tab-' + tabs.dataset.user;

  function rememberTab(id) {
    try {
      sessionStorage.setItem(storageKey, id);
    } catch (error) {
      console.warn('Could not remember the selected category group.', error);
    }
    history.replaceState(null, '', location.pathname + location.search + '#' + id);
  }

  tabButtons.forEach(function (button) {
    button.addEventListener('shown.bs.tab', function () { rememberTab(button.id); });
  });
  var savedTab = location.hash.slice(1);
  if (!savedTab) {
    try {
      savedTab = sessionStorage.getItem(storageKey);
    } catch (error) {
      console.warn('Could not restore the selected category group.', error);
    }
  }
  var selectedTab = tabButtons.find(function (button) { return button.id === savedTab; });
  if (selectedTab) bootstrap.Tab.getOrCreateInstance(selectedTab).show();

  all(document, '.cat-v2-transaction-count').forEach(function (link) {
    link.addEventListener('click', function () {
      var destination = new URL(link.href);
      var activeTab = tabs.querySelector('[aria-selected="true"]');
      destination.searchParams.set('return_to', location.pathname + location.search + '#' + activeTab.id);
      link.href = destination.toString();
    });
  });

  // ---- Add / edit dialog ------------------------------------------------
  var modal = new bootstrap.Modal($('catV2Modal'));
  var parentSelect = $('catV2Parent');

  // Only offer parents from the same group, excluding the edited node and its descendants.
  function filterParents(group, editingId) {
    all(parentSelect, 'option').forEach(function (opt) {
      if (!opt.value) return;
      var ancestors = (opt.dataset.ancestors || '').split(',');
      var hidden = opt.dataset.group !== group || (editingId && ancestors.indexOf(editingId) !== -1);
      opt.hidden = hidden;
      opt.disabled = hidden;
    });
  }

  function openForm(opts) {
    $('catV2Title').textContent = opts.title;
    $('catV2Id').value = opts.id || '';
    $('catV2Group').value = opts.group;
    $('catV2Name').value = opts.name || '';
    $('catV2Color').value = opts.color || '#6c757d';
    $('catV2IncomeRoleWrap').classList.toggle('d-none', opts.group !== 'income');
    $('catV2IncomeRole').value = opts.incomeRole || '';
    filterParents(opts.group, opts.id);
    parentSelect.value = opts.parent || '';
    // When adding under a known parent, show it as text instead of asking again.
    var fixedParent = !!opts.parentLocked;
    $('catV2ParentWrap').classList.toggle('d-none', fixedParent);
    $('catV2ParentNote').classList.toggle('d-none', !fixedParent);
    $('catV2ParentNoteName').textContent = opts.parentName || '';
    modal.show();
  }

  function rowOf(el) { return el.closest('tr.cat-v2-row'); }

  all(document, '.cat-v2-add').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openForm({ title: 'Add Category', group: this.dataset.group });
    });
  });

  function editRow(row) {
    openForm({
      title: 'Edit Category', id: row.dataset.id, group: row.dataset.group,
      name: row.dataset.name, color: row.dataset.color, parent: row.dataset.parent,
      incomeRole: row.dataset.incomeRole,
    });
  }

  // ---- Move dialog ------------------------------------------------------
  var moveModal = new bootstrap.Modal($('catV2MoveModal'));
  var moveParent = $('catV2MoveParent');

  function listNames(names) {
    return names.length <= 3 ? names.join(', ') : names.slice(0, 3).join(', ') + ' and ' + (names.length - 3) + ' more';
  }

  function hiddenInputs(container, ids) {
    container.innerHTML = '';
    ids.forEach(function (id) {
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'ids';
      input.value = id;
      container.appendChild(input);
    });
  }

  function openMove(group, items) {
    var ids = items.map(function (i) { return i.id; });
    var currentParent = items[0].parent;
    // Offer only same-group parents that are not selected, nor beneath a selected node.
    all(moveParent, 'option').forEach(function (opt) {
      if (!opt.value) return;
      var ancestors = (opt.dataset.ancestors || '').split(',');
      var blocked = opt.dataset.group !== group ||
        ancestors.some(function (id) { return ids.indexOf(id) !== -1; }) ||
        opt.value === currentParent;
      opt.hidden = blocked;
      opt.disabled = blocked;
    });
    var top = moveParent.options[0];
    top.disabled = top.hidden = currentParent === '';
    var firstOpen = all(moveParent, 'option').filter(function (o) { return !o.disabled; })[0];
    moveParent.value = firstOpen ? firstOpen.value : '';
    $('catV2MoveSubmit').disabled = !firstOpen;
    $('catV2MoveNames').textContent = listNames(items.map(function (i) { return i.name; }));
    hiddenInputs($('catV2MoveIds'), ids);
    moveModal.show();
  }

  // ---- Group dialog -----------------------------------------------------
  var groupModal = new bootstrap.Modal($('catV2GroupModal'));

  function openGroup(items) {
    hiddenInputs($('catV2GroupIds'), items.map(function (i) { return i.id; }));
    $('catV2GroupNames').textContent = listNames(items.map(function (i) { return i.name; }));
    $('catV2GroupName').value = '';
    groupModal.show();
  }

  // ---- Delete dialog ----------------------------------------------------
  var deleteModal = new bootstrap.Modal($('catV2DeleteModal'));

  function openDelete(items) {
    var blocked = items.filter(function (i) { return i.children > 0; });
    var ids = $('catV2DeleteIds');
    $('catV2DeleteId').value = '';
    hiddenInputs(ids, items.map(function (i) { return i.id; }));
    $('catV2DeleteTitle').textContent = items.length > 1 ? 'Delete categories' : 'Delete category';
    var text = $('catV2DeleteText');
    text.textContent = '';
    if (blocked.length) {
      text.append(listNames(blocked.map(function (i) { return i.name; })) +
        (blocked.length > 1 ? ' have' : ' has') + ' subcategories. Move or delete them first.');
    } else if (items.length === 1) {
      text.append('Delete "', items[0].name, '"? This cannot be undone.');
    } else {
      text.append('Delete ' + items.length + ' categories (' + listNames(items.map(function (i) { return i.name; })) +
        ')? This cannot be undone.');
    }
    $('catV2DeleteSubmit').disabled = blocked.length > 0;
    deleteModal.show();
  }

  function rowItem(row) {
    return { id: row.dataset.id, name: row.dataset.name, parent: row.dataset.parent,
             children: parseInt(row.dataset.children, 10) || 0 };
  }

  // ---- Row actions ------------------------------------------------------
  all(document, '.cat-v2-menu-btn').forEach(function (btn) {
    // Fixed positioning keeps the menu from being clipped by the scrolling table wrapper.
    new bootstrap.Dropdown(btn, { popperConfig: { strategy: 'fixed' } });
  });

  all(document, 'tr.cat-v2-row').forEach(function (row) {
    row.querySelector('.cat-v2-act-edit').addEventListener('click', function () { editRow(row); });
    row.querySelector('.cat-v2-act-add').addEventListener('click', function () {
      openForm({
        title: 'Add Subcategory', group: row.dataset.group, parent: row.dataset.id,
        parentLocked: true, parentName: row.dataset.name,
      });
    });
    row.querySelector('.cat-v2-act-move').addEventListener('click', function () {
      openMove(row.dataset.group, [{ id: row.dataset.id, name: row.dataset.name, parent: row.dataset.parent }]);
    });
    row.querySelector('.cat-v2-act-delete').addEventListener('click', function () { openDelete([rowItem(row)]); });
  });

  // ---- Selection --------------------------------------------------------
  var cards = all(document, '.cat-v2-card').map(function (card) {
    var table = card.querySelector('table');
    var rows = all(table, 'tr.cat-v2-row');
    if (!rows.length) return null;

    var selectAll = card.querySelector('.cat-v2-select-all');
    var bar = card.querySelector('.cat-v2-bar');
    var groupBtn = card.querySelector('.cat-v2-group-btn');
    var moveBtn = card.querySelector('.cat-v2-move-btn');

    function boxOf(row) { return row.querySelector('.cat-v2-select'); }
    function checkedRows() { return rows.filter(function (r) { return boxOf(r).checked; }); }
    function itemsOf(list) {
      return list.map(rowItem);
    }

    function refresh() {
      var checked = checkedRows();
      var parent = checked.length ? checked[0].dataset.parent : null;
      var parentName = checked.length ? checked[0].dataset.parentName : '';

      rows.forEach(function (r) {
        var locked = parent !== null && r.dataset.parent !== parent;
        boxOf(r).disabled = locked;
        r.classList.toggle('cat-v2-locked', locked);
        r.classList.toggle('cat-v2-selected', boxOf(r).checked);
        r.title = locked ? 'Select categories that share the same parent' : '';
      });

      bar.classList.toggle('d-none', !checked.length);
      bar.classList.toggle('d-flex', checked.length > 0);
      card.querySelector('.cat-v2-bar-count').textContent = checked.length;
      card.querySelector('.cat-v2-bar-where').textContent = checked.length
        ? (parent === '' ? ' at the top level' : ' under ' + parentName) : '';

      groupBtn.disabled = checked.length < 2;
      groupBtn.title = checked.length < 2 ? 'Select at least 2 categories to group them' : '';

      var level = rows.filter(function (r) { return parent !== null && r.dataset.parent === parent; });
      selectAll.checked = checked.length > 0 && checked.length === level.length;
      selectAll.indeterminate = checked.length > 0 && checked.length < level.length;
    }

    rows.forEach(function (r) { boxOf(r).addEventListener('change', refresh); });

    // Select all siblings of the current selection, or the top level when nothing is selected.
    selectAll.addEventListener('change', function () {
      var checked = checkedRows();
      var parent = checked.length ? checked[0].dataset.parent : '';
      var checkIt = this.checked || this.indeterminate;
      rows.forEach(function (r) { boxOf(r).checked = checkIt && r.dataset.parent === parent; });
      if (!this.checked) rows.forEach(function (r) { boxOf(r).checked = false; });
      refresh();
    });

    card.querySelector('.cat-v2-clear-btn').addEventListener('click', function () {
      rows.forEach(function (r) { boxOf(r).checked = false; });
      refresh();
    });
    moveBtn.addEventListener('click', function () { openMove(card.dataset.group, itemsOf(checkedRows())); });
    groupBtn.addEventListener('click', function () { openGroup(itemsOf(checkedRows())); });
    card.querySelector('.cat-v2-delete-btn').addEventListener('click', function () { openDelete(itemsOf(checkedRows())); });

    refresh();
    return { clear: function () { rows.forEach(function (r) { boxOf(r).checked = false; }); refresh(); } };
  }).filter(Boolean);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !document.querySelector('.modal.show')) {
      cards.forEach(function (c) { c.clear(); });
    }
  });

  ['catV2Modal', 'catV2GroupModal'].forEach(function (id) {
    $(id).addEventListener('shown.bs.modal', function () {
      var input = $(id === 'catV2Modal' ? 'catV2Name' : 'catV2GroupName');
      input.focus();
    });
  });
})();
