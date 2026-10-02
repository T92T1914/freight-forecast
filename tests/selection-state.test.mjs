import assert from 'node:assert/strict';
import test from 'node:test';
import {bindSelection, selectedIndex, selectionHref} from '../site/selection-state.mjs';

test('example label survives reordering and special characters', () => {
  const labels = ['First', '8 + 5 + 3 against T', 'A & B / C'];
  const url = new URL(selectionHref('https://example.test/demo/', labels[2]));
  assert.equal(selectedIndex(url.search, labels), 2);
  assert.equal(selectedIndex(url.search, [...labels].reverse()), 0);
  assert.equal(url.searchParams.get('example'), labels[2]);
});
test('missing or stale examples safely use the first available result', () => {
  assert.equal(selectedIndex('', ['Known']), 0);
  assert.equal(selectedIndex('?example=Unknown', ['Known']), 0);
  assert.equal(selectedIndex('?example=%3Cscript%3E', ['Known']), 0);
});
test('sharing preserves unrelated query state, subdirectory and anchor', () => {
  const url = new URL(selectionHref('https://example.test/project/?slice=Winter#interactive', '2024-07'));
  assert.equal(url.pathname, '/project/');
  assert.equal(url.searchParams.get('slice'), 'Winter');
  assert.equal(url.hash, '#interactive');
  assert.equal(url.searchParams.get('example'), '2024-07');
});
test('independent selectors can share one URL without replacing each other', () => {
  const first = selectionHref('https://example.test/', '2024-07');
  const second = selectionHref(first, 'Winter', 'slice');
  assert.equal(selectedIndex(new URL(second).search, ['2024-01','2024-07']), 1);
  assert.equal(selectedIndex(new URL(second).search, ['All','Winter'], 'slice'), 1);
});

function bindings(href = 'https://example.test/project/?other=keep#evidence') {
  const view = new EventTarget();
  view.location = new URL(href);
  const pushed = [];
  view.history = {pushState(_state, _title, next) {
    pushed.push(next);
    view.location = new URL(next);
  }};
  const document = {defaultView:view};
  const make = labels => {
    const select = new EventTarget();
    Object.assign(select, {ownerDocument:document, options:labels.map(textContent => ({textContent})),
      selectedIndex:0});
    return select;
  };
  const primary = make(['2023-05','2023-10']);
  const comparison = make(['All 24 months','2023','2024']);
  const unrelated = make(['Unchanged','Other']);
  const links = [{href:''},{href:''}];
  const rendered = [];
  bindSelection(primary, links[0], () => rendered.push(['primary',primary.selectedIndex]));
  bindSelection(comparison, links[1], () => rendered.push(['comparison',comparison.selectedIndex]), 'backtest');
  return {view, primary, comparison, unrelated, links, rendered, pushed};
}

test('changing either bound selector updates both links without touching other controls', () => {
  const state = bindings();
  state.comparison.selectedIndex = 2;
  state.comparison.dispatchEvent(new Event('change'));
  state.primary.selectedIndex = 1;
  state.primary.dispatchEvent(new Event('change'));
  for (const link of state.links) {
    const url = new URL(link.href);
    assert.equal(url.searchParams.get('example'), '2023-10');
    assert.equal(url.searchParams.get('backtest'), '2024');
    assert.equal(url.searchParams.get('other'), 'keep');
    assert.equal(url.hash, '#evidence');
  }
  assert.equal(state.unrelated.selectedIndex, 0);
  assert.deepEqual(state.rendered, [['primary',0],['comparison',0],['comparison',2],['primary',1]]);
  assert.equal(state.pushed.length, 2);
});

test('Back restores declared selectors and stale inputs produce honest default links', () => {
  const state = bindings('https://example.test/?example=2023-10&backtest=2024');
  assert.equal(state.primary.selectedIndex, 1);
  assert.equal(state.comparison.selectedIndex, 2);
  state.view.location = new URL('https://example.test/?example=Unknown&backtest=9999&other=keep');
  state.view.dispatchEvent(new Event('popstate'));
  assert.equal(state.primary.selectedIndex, 0);
  assert.equal(state.comparison.selectedIndex, 0);
  assert.equal(state.unrelated.selectedIndex, 0);
  for (const link of state.links) {
    const url = new URL(link.href);
    assert.equal(url.searchParams.get('example'), '2023-05');
    assert.equal(url.searchParams.get('backtest'), 'All 24 months');
    assert.equal(url.searchParams.get('other'), 'keep');
  }
  assert.equal(state.pushed.length, 0);
});

test('blocked history retains both current selections in each usable link', () => {
  const state = bindings();
  const original = state.view.location.href;
  state.view.history.pushState = () => { throw Error('Restricted history'); };
  state.comparison.selectedIndex = 2;
  state.comparison.dispatchEvent(new Event('change'));
  state.primary.selectedIndex = 1;
  state.primary.dispatchEvent(new Event('change'));
  assert.equal(state.view.location.href, original);
  for (const link of state.links) {
    const url = new URL(link.href);
    assert.equal(url.searchParams.get('example'), '2023-10');
    assert.equal(url.searchParams.get('backtest'), '2024');
  }
});

test('rebinding a selector replaces its callback without duplicate listeners', () => {
  const state = bindings();
  const replacements = [];
  const next = {href:''};
  bindSelection(state.primary, next, () => replacements.push(state.primary.selectedIndex));
  state.rendered.length = 0;
  replacements.length = 0;
  state.primary.selectedIndex = 1;
  state.primary.dispatchEvent(new Event('change'));
  assert.deepEqual(replacements, [1]);
  assert.deepEqual(state.rendered, []);
  assert.equal(state.pushed.length, 1);
  assert.equal(new URL(next.href).searchParams.get('backtest'), 'All 24 months');
  assert.throws(() => bindSelection(state.unrelated, {href:''}, () => {}, 'backtest'), /already bound/);
  state.comparison.selectedIndex = 2;
  state.comparison.dispatchEvent(new Event('change'));
  assert.equal(new URL(next.href).searchParams.get('backtest'), '2024');
  assert.equal(state.unrelated.selectedIndex, 0);
});
