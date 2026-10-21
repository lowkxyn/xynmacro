import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const viewer = readFileSync(new URL('./log-viewer.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('./main.js', import.meta.url), 'utf8');

test('Copy Log uses the backend redaction instead of raw viewer text', () => {
  assert.match(viewer, /path: '\/copy_log'/);
  assert.doesNotMatch(viewer, /innerText/);
});

/* Runs the real bug-report code from main.js against a fake DOM, fake timers and a
   fake backend. Nothing native, no browser and no network. */
function bugReportHarness() {
  const start = main.indexOf('  const BUG_SECTIONS = [');
  const end = main.indexOf('  function closeChangelog()', start);
  assert.ok(start > 0 && end > start, 'bug report section not found in main.js');
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        id, checked: false, value: '', textContent: '', disabled: false, listeners: {},
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c),
                     contains: (c) => classes.has(c) },
        addEventListener(name, callback) { this.listeners[name] = callback; },
        setAttribute() {}, scrollIntoView() {},
      });
    }
    return elements.get(id);
  };
  const clock = { now: 0 };
  const timers = new Map();
  let nextTimer = 0;
  const previews = [];        // resolvers of pending bug_report requests, in order
  const opened = [];
  const context = vm.createContext({
    document: { getElementById: element },
    navigator: { userAgent: 'synthetic', clipboard: { writeText: async () => {} } },
    window: {}, UI_ERROR_RING: [], Date: { now: () => clock.now },
    showToast() {}, _focusModal() {}, _restoreModalFocus() {},
    setTimeout(callback, ms) { timers.set(++nextTimer, { callback, ms }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(callback, ms) { timers.set(++nextTimer, { callback, ms, repeat: true }); return nextTimer; },
    clearInterval(id) { timers.delete(id); },
    sendCommand(action, value) {
      if (action === 'bug_report') return new Promise((resolve) => previews.push(resolve));
      assert.equal(action, 'bug_report_open');
      opened.push(value);
      return Promise.resolve({ ok: true });
    },
  });
  vm.runInContext(main.slice(start, end)
    + '\nglobalThis.bug = { refresh: _refreshBugPreview, confirm: openBugConfirm };', context);
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  return {
    element, clock, previews, opened, bug: context.bug, settle,
    // Elapse the countdown, then give three properly spaced clicks.
    async postThroughConfirmation() {
      clock.now += 6000;
      for (let tick = 0; tick < 5; tick += 1) {
        for (const [id, t] of [...timers]) if (t.repeat && timers.has(id)) t.callback();
      }
      for (let click = 0; click < 3; click += 1) {
        await element('bugConfirmGo').onclick();
        clock.now += 1100;
        for (const [id, t] of [...timers]) if (!t.repeat) { timers.delete(id); t.callback(); }
      }
    },
  };
}

const reportA = { ok: true, markdown: 'report A, reviewed', report_id: 'reviewed-A' };
const reportB = { ok: true, markdown: 'report B, never reviewed', report_id: 'unreviewed-B' };

test('a preview finishing behind the confirmation cannot replace the reviewed report', async () => {
  const h = bugReportHarness();
  const first = h.bug.refresh();
  h.previews.shift()(reportA);
  await first;
  assert.equal(h.element('bugPreview').textContent, reportA.markdown);

  const pending = h.bug.refresh();          // e.g. a section ticked just before Open
  h.bug.confirm();
  assert.ok(h.element('bugConfirmOverlay').classList.contains('open'));
  h.previews.shift()(reportB);
  await pending;
  assert.equal(h.element('bugPreview').textContent, reportA.markdown);

  h.bug.refresh();                           // a debounced edit firing mid-countdown
  await h.settle();
  assert.equal(h.previews.length, 0, 'no preview may start while confirming');

  await h.postThroughConfirmation();
  assert.deepEqual(h.opened.map((v) => v.report_id), ['reviewed-A']);
});

test('a preview held back by a cancelled confirmation is shown afterwards', async () => {
  const h = bugReportHarness();
  const first = h.bug.refresh();
  h.previews.shift()(reportA);
  await first;
  const pending = h.bug.refresh();
  h.bug.confirm();
  h.previews.shift()(reportB);
  await pending;
  h.element('bugConfirmCancel').onclick();
  assert.equal(h.previews.length, 1, 'the held-back preview is requested again');
  h.previews.shift()(reportB);
  await h.settle();
  assert.equal(h.element('bugPreview').textContent, reportB.markdown);

  h.bug.confirm();
  await h.postThroughConfirmation();
  // B was on screen when this confirmation opened, so B is now the reviewed report.
  assert.deepEqual(h.opened.map((v) => v.report_id), ['unreviewed-B']);
});

test('without a reviewed report the confirmation does not open', () => {
  const h = bugReportHarness();
  h.bug.confirm();
  assert.ok(!h.element('bugConfirmOverlay').classList.contains('open'));
  assert.equal(h.element('bugConfirmGo').onclick, undefined);
});
