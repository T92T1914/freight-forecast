import assert from 'node:assert/strict';
import test from 'node:test';
import {selectedIndex, selectionHref} from '../site/selection-state.mjs';

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
