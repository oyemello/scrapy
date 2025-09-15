// Auto-open <details> drawers when linking to anchors like #child-<id>
(function() {
  function openFromHash() {
    try {
      var hash = decodeURIComponent(location.hash || '').replace(/^#/, '');
      if (!hash) return;
      var el = document.getElementById(hash);
      if (el && el.tagName && el.tagName.toLowerCase() === 'details') {
        el.open = true;
        // Scroll into view a bit after opening to ensure layout settled
        setTimeout(function(){ el.scrollIntoView({behavior: 'smooth', block: 'start'}); }, 0);
      }
    } catch (e) { /* noop */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', openFromHash);
  } else {
    openFromHash();
  }
  window.addEventListener('hashchange', openFromHash);
})();

