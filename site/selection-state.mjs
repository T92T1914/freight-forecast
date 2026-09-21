// Use the visible label so saved links survive example reordering.
export function selectedIndex(search, labels, parameter = 'example') {
  const wanted = new URLSearchParams(search).get(parameter);
  const index = labels.indexOf(wanted);
  return index < 0 ? 0 : index;
}

export function selectionHref(href, label, parameter = 'example') {
  const url = new URL(href);
  url.searchParams.set(parameter, label);
  return url.href;
}

export function bindSelection(select, link, render, parameter = 'example') {
  const labels = [...select.options].map(option => option.textContent);
  if (!labels.length) return;
  const refresh = () => {
    render();
    link.href = selectionHref(location.href, labels[select.selectedIndex], parameter);
  };
  const restore = () => {
    select.selectedIndex = selectedIndex(location.search, labels, parameter);
    refresh();
  };
  select.addEventListener('change', () => {
    refresh();
    if (link.href !== location.href) {
      try { history.pushState(null, '', link.href); }
      catch { /* The selection and share link still work in restricted contexts. */ }
    }
  });
  addEventListener('popstate', restore);
  restore();
}
