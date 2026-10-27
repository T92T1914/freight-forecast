/* Resolve explicit choices before paint; no script or storage is needed for Obscur. */
(function () {
  'use strict';
  const key = 'freight-forecast.appearance.v1';
  const root = document.documentElement;
  let preference = 'obscur';
  try {
    const saved = localStorage.getItem(key);
    if (['auto', 'clair', 'obscur'].includes(saved)) preference = saved;
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
        localStorage.setItem(key, preference);
      } catch (_) { /* Switching remains usable when persistence is unavailable. */ }
    });
  });
})();
