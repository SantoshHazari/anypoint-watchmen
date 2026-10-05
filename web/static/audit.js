/* Audit table: user filter, search filter, sortable columns */

/* Dropdown filters (user, bg) */
function applyFilter(param, value) {
  var url = new URL(window.location.href);
  if (value) {
    url.searchParams.set(param, value);
  } else {
    url.searchParams.delete(param);
  }
  window.location.href = url.toString();
}

/* Search / filter */
(function() {
  var input = document.getElementById('audit-search');
  if (!input) return;
  input.addEventListener('input', function() {
    var query = input.value.toLowerCase();
    var rows = document.querySelectorAll('#audit-table tbody tr');
    var visible = 0;
    rows.forEach(function(row) {
      var text = row.textContent.toLowerCase();
      var match = !query || text.indexOf(query) !== -1;
      row.classList.toggle('hidden-row', !match);
      if (match) visible++;
    });
    var counter = document.getElementById('audit-match-count');
    if (query) {
      counter.textContent = visible + ' of ' + rows.length + ' events';
    } else {
      counter.textContent = '';
    }
  });
})();

/* Sortable columns */
(function() {
  var currentCol = -1;
  var currentDir = '';

  document.querySelectorAll('#audit-table th.sortable').forEach(function(th) {
    th.addEventListener('click', function() {
      var col = parseInt(th.getAttribute('data-col'));
      var type = th.getAttribute('data-type');
      var tbody = document.querySelector('#audit-table tbody');
      var rows = Array.from(tbody.querySelectorAll('tr'));

      /* Toggle direction */
      var dir;
      if (currentCol === col) {
        dir = currentDir === 'asc' ? 'desc' : 'asc';
      } else {
        dir = type === 'date' ? 'desc' : 'asc';
      }
      currentCol = col;
      currentDir = dir;

      /* Clear all arrows */
      document.querySelectorAll('#audit-table .sort-arrow').forEach(function(s) {
        s.className = 'sort-arrow';
      });
      th.querySelector('.sort-arrow').className = 'sort-arrow ' + dir;

      rows.sort(function(a, b) {
        var aVal, bVal;
        if (type === 'date') {
          aVal = a.getAttribute('data-sort-date') || '';
          bVal = b.getAttribute('data-sort-date') || '';
        } else {
          aVal = (a.cells[col] ? a.cells[col].textContent.trim().toLowerCase() : '');
          bVal = (b.cells[col] ? b.cells[col].textContent.trim().toLowerCase() : '');
        }
        if (aVal < bVal) return dir === 'asc' ? -1 : 1;
        if (aVal > bVal) return dir === 'asc' ? 1 : -1;
        return 0;
      });

      rows.forEach(function(row) { tbody.appendChild(row); });
    });
  });
})();
