(function() {
  try {
    var h1 = document.querySelector('main h1, article h1, .md-content h1');
    if (!h1) return;
    var m = (h1.textContent || '').trim().match(/^(\d+(?:\.\d+)*)\s+/);
    if (!m) return;
    var prefix = m[1];
    var toc = document.querySelector('nav.md-nav--secondary');
    if (!toc) return;

    // Number top-level TOC entries and their immediate children
    var topItems = toc.querySelectorAll(':scope .md-nav__list > .md-nav__item');
    var i = 1;
    topItems.forEach(function(item) {
      var link = item.querySelector(':scope > .md-nav__link');
      if (link) {
        var t = (link.textContent || '').trim();
        if (!/^\d+(?:\.\d+)*\s+/.test(t)) {
          link.textContent = prefix + '.' + i + ' ' + t;
        }
      }
      // Child level
      var children = item.querySelectorAll(':scope .md-nav__item > .md-nav__link');
      var j = 1;
      children.forEach(function(cl) {
        var ct = (cl.textContent || '').trim();
        if (!/^\d+(?:\.\d+)*\s+/.test(ct)) {
          cl.textContent = prefix + '.' + i + '.' + j + ' ' + ct;
        }
        j++;
      });
      i++;
    });
  } catch (e) {
    console.warn('TOC numbering failed:', e);
  }
})();

