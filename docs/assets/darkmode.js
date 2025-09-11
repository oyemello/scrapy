/**
 * Dark Mode Initialization
 * Sets Bootstrap theme and manages syntax highlighting styles
 */
(function() {
    'use strict';
    
    try {
        // Set dark theme on root element
        const root = document.documentElement;
        root.setAttribute('data-bs-theme', 'dark');
        
        // Toggle syntax highlighting themes
        const hlLight = document.getElementById('hljs-light');
        const hlDark = document.getElementById('hljs-dark');
        
        if (hlLight) {
            hlLight.disabled = true;
        }
        
        if (hlDark) {
            hlDark.disabled = false;
        }
        
    } catch (error) {
        // Only log errors in development
        if (console && console.warn) {
            console.warn('Dark mode initialization failed:', error);
        }
    }
})();

