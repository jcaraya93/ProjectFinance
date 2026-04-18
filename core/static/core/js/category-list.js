document.querySelectorAll('.add-category-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var group = this.dataset.group;
    var name = prompt('New category name:');
    if (name && name.trim()) {
      document.getElementById('addCatGroup').value = group;
      document.getElementById('addCatName').value = name.trim();
      document.getElementById('addCategoryForm').submit();
    }
  });
});

document.querySelectorAll('.rename-category-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var group = this.dataset.group;
    var oldName = this.dataset.name;
    var newName = prompt('Rename "' + oldName + '" to:', oldName);
    if (newName && newName.trim() && newName.trim() !== oldName) {
      document.getElementById('renameCatGroup').value = group;
      document.getElementById('renameCatOldName').value = oldName;
      document.getElementById('renameCatNewName').value = newName.trim();
      document.getElementById('renameCategoryForm').submit();
    }
  });
});
