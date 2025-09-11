(function() {
  function addTocNumbers() {
    var toc = document.querySelector('nav.md-nav--secondary');
    if (!toc) return;

    // Determine optional page prefix from H1 (e.g., "3.1 Title")
    var pagePrefix = '';
    var h1 = document.querySelector('main h1, article h1, .md-content h1');
    if (h1) {
      var m = (h1.textContent || '').trim().match(/^(\d+(?:\.\d+)*)\s+/);
      if (m) pagePrefix = m[1];
    }

    var topList = toc.querySelector(':scope .md-nav__list');
    if (!topList) return;

    function numberList(listEl, prefixParts) {
      var idx = 1;
      listEl.querySelectorAll(':scope > .md-nav__item').forEach(function(li) {
        var link = li.querySelector(':scope > .md-nav__link');
        if (link) {
          var newParts = prefixParts.concat(idx);
          var base = newParts.join('.');
          var finalNumber = (pagePrefix ? pagePrefix + '.' : '') + base;
          var text = (link.textContent || '').trim();
          // Always enforce numbering on TOC entries
          link.textContent = finalNumber + ' ' + text.replace(/^\d+(?:\.\d+)*\s+/, '');
        }
        var childList = li.querySelector(':scope > .md-nav__list');
        if (childList) numberList(childList, prefixParts.concat(idx));
        idx++;
      });
    }

    numberList(topList, []);
  }

  try {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', addTocNumbers);
    } else {
      addTocNumbers();
    }
  } catch (e) {
    console.warn('TOC numbering failed:', e);
  }
})();
