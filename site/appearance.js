/* This runs before styles paint. No storage or script is required for Auto. */
(function () {
  'use strict';
  const key = 'freight-forecast.appearance.v1';
  const root = document.documentElement;
  let preference = 'auto';
  try {
    const saved = localStorage.getItem(key);
    if (saved === 'clair' || saved === 'obscur') preference = saved;
  } catch (_) { /* A blocked store leaves a working in-memory preference. */ }
  root.dataset.appearance = preference;
  document.addEventListener('DOMContentLoaded', () => {
    const control = document.getElementById('appearance');
    if (!control) return;
    control.value = preference;
    control.disabled = false;
    control.addEventListener('change', () => {
      preference = ['clair', 'obscur'].includes(control.value) ? control.value : 'auto';
      root.dataset.appearance = preference;
      try {
        if (preference === 'auto') localStorage.removeItem(key);
        else localStorage.setItem(key, preference);
      } catch (_) { /* Switching remains usable when persistence is unavailable. */ }
    });
  });
})();
