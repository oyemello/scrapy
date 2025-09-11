(function() {
  function stripExistingNumbers(listEl) {
    listEl.querySelectorAll('.md-nav__link').forEach(function(link) {
      var t = (link.textContent || '').trim();
      link.textContent = t.replace(/^\d+(?:\.\d+)*\s+/, '');
    });
  }

  function numberTocOnce() {
    var toc = document.querySelector('nav[data-md-component="toc"]') || document.querySelector('nav.md-nav--secondary');
    if (!toc) return;

    var topList = toc.querySelector(':scope .md-nav__list');
    if (!topList) return;

    // Optional page prefix from H1 (e.g., "3.1 Title")
    var pagePrefix = '';
    var h1 = document.querySelector('main h1, article h1, .md-content h1');
    if (h1) {
      var m = (h1.textContent || '').trim().match(/^(\d+(?:\.\d+)*)\s+/);
      if (m) pagePrefix = m[1];
    }

    // Clear any previous numbering to be idempotent across client-side page reloads
    stripExistingNumbers(topList);

    function walk(listEl, parts) {
      var idx = 1;
      listEl.querySelectorAll(':scope > .md-nav__item').forEach(function(li) {
        var link = li.querySelector(':scope > .md-nav__link');
        if (link) {
          var curr = parts.concat(idx);
          var base = curr.join('.');
          var number = (pagePrefix ? pagePrefix + '.' : '') + base;
          var text = (link.textContent || '').trim();
          link.textContent = number + ' ' + text;
        }
        var child = li.querySelector(':scope > .md-nav__list');
        if (child) walk(child, parts.concat(idx));
        idx++;
      });
    }

    walk(topList, []);
  }

  function schedule() {
    try { numberTocOnce(); } catch (e) { console.warn('TOC numbering failed:', e); }
  }

  // Integrate with Material for MkDocs instant navigation
  if (window.document$ && window.document$.subscribe) {
    window.document$.subscribe(function() { setTimeout(schedule, 0); });
  } else {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', schedule);
    } else {
      schedule();
    }
    window.addEventListener('hashchange', schedule);
  }
})();
