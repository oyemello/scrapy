(function() {
  try {
    var root = document.documentElement;
    root.setAttribute('data-bs-theme', 'dark');
    var hlLight = document.getElementById('hljs-light');
    var hlDark = document.getElementById('hljs-dark');
    if (hlLight) hlLight.disabled = true;
    if (hlDark) hlDark.disabled = false;
  } catch (e) {
    console.warn('Dark mode init failed:', e);
  }
})();
