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

// Coordinate only selectors explicitly bound in this document.
const groups = new WeakMap();

function groupFor(document) {
  let group = groups.get(document);
  if (group) return group;
  const view = document.defaultView;
  const records = new Map();
  const refreshLinks = () => {
    let href = view.location.href;
    for (const {select, labels, parameter} of records.values()) {
      href = selectionHref(href, labels[select.selectedIndex], parameter);
    }
    for (const {link} of records.values()) link.href = href;
    return href;
  };
  const restore = () => {
    for (const {select, labels, parameter, render} of records.values()) {
      select.selectedIndex = selectedIndex(view.location.search, labels, parameter);
      render();
    }
    refreshLinks();
  };
  view.addEventListener('popstate', restore);
  group = {view, records, refreshLinks};
  groups.set(document, group);
  return group;
}

export function bindSelection(select, link, render, parameter = 'example') {
  const labels = [...select.options].map(option => option.textContent);
  if (!labels.length) return;
  const group = groupFor(select.ownerDocument);
  for (const record of group.records.values()) {
    if (record.select !== select && record.parameter === parameter) {
      throw Error('Selection query parameter is already bound');
    }
  }
  let record = group.records.get(select);
  if (record) Object.assign(record, {link, render, parameter, labels});
  else {
    record = {select, link, render, parameter, labels};
    group.records.set(select, record);
    select.addEventListener('change', () => {
      record.render();
      const href = group.refreshLinks();
      if (href !== group.view.location.href) {
        try { group.view.history.pushState(null, '', href); }
        catch { /* Current selections still have a usable shared link. */ }
      }
    });
  }
  select.selectedIndex = selectedIndex(group.view.location.search, labels, parameter);
  render();
  group.refreshLinks();
}
