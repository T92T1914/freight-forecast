import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const script = await readFile(new URL('../site/appearance.js', import.meta.url), 'utf8');
function fixture(saved, blocked = false) {
  const values = new Map(saved ? [['freight-forecast.appearance.v1', saved]] : []);
  const root = {dataset: {}};
  let ready, change;
  const control = {disabled: true, addEventListener: (_, listener) => { change = listener; }};
  vm.runInNewContext(script, {
    document: {documentElement: root, addEventListener: (_, listener) => { ready = listener; },
      getElementById: () => control},
    localStorage: {
      getItem: key => { if (blocked) throw Error('blocked'); return values.get(key); },
      setItem: (key, value) => { if (blocked) throw Error('blocked'); values.set(key, value); },
      removeItem: key => { if (blocked) throw Error('blocked'); values.delete(key); }
    }
  });
  ready();
  return {root, control, values, select(value) { control.value = value; change(); }};
}
test('explicit appearance persists; Auto clears only the project preference', () => {
  const f = fixture('obscur');
  assert.equal(f.root.dataset.appearance, 'obscur');
  assert.equal(f.control.disabled, false);
  f.values.set('unrelated', 'keep');
  f.select('clair');
  assert.equal(f.values.get('freight-forecast.appearance.v1'), 'clair');
  f.select('auto');
  assert.equal(f.root.dataset.appearance, 'auto');
  assert.equal(f.values.get('unrelated'), 'keep');
  assert.equal(f.values.has('freight-forecast.appearance.v1'), false);
});
test('unknown stored values use Auto and failed storage still allows switching', () => {
  assert.equal(fixture('arbitrary').root.dataset.appearance, 'auto');
  const f = fixture(null, true);
  f.select('obscur');
  assert.equal(f.root.dataset.appearance, 'obscur');
  f.select('clair');
  assert.equal(f.root.dataset.appearance, 'clair');
});
