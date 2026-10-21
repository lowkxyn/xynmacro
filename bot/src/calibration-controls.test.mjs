import assert from 'node:assert/strict';
import test from 'node:test';
import './calibration-controls.js';

test('calibration keeps command payloads and user messages', async () => {
  const commands = [];
  const toasts = [];
  const controls = XynMacroCalibrationControls.create({
    document: {},
    invoke: async () => {},
    sendCommand: async (...args) => {
      commands.push(args);
      return args[0] === 'calibrate_button_begin' ? { ok: true } : { ok: false, msg: 'Region unavailable' };
    },
    showToast: (...args) => toasts.push(args),
  });

  await controls.calibBtn('Ki Control');
  await controls.calibRegion('health_box');
  assert.deepEqual(commands, [
    ['calibrate_button_begin', { stat: 'Ki Control' }],
    ['calibrate_region_begin', { region: 'health_box' }],
  ]);
  assert.deepEqual(toasts, [
    ['Click the Ki Control trait button in Roblox', 'ok'],
    ['Region unavailable', 'err'],
  ]);
});

test('leaving a calibration view stops previews and ignores a late capture', async () => {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        style: {}, src: '', textContent: '',
        classList: {
          add: (name) => classes.add(name),
          remove: (name) => classes.delete(name),
          contains: (name) => classes.has(name),
        },
      });
    }
    return elements.get(id);
  };
  const document = { getElementById: element };
  const requests = [];
  let finishCapture;
  const invoke = (...args) => {
    requests.push(args);
    return new Promise((resolve) => { finishCapture = resolve; });
  };
  const controls = XynMacroCalibrationControls.create({
    document, invoke, sendCommand: async () => {}, showToast: () => {},
  });

  controls.togglePreview('health_box');
  assert.deepEqual(requests, [['proxy_get', { path: '/preview?region=health_box' }]]);
  assert.equal(element('previewBtnHealth').classList.contains('active'), true);
  assert.equal(element('previewRowHealth').style.display, '');

  controls.stopAllPreviews();
  assert.equal(element('previewBtnHealth').classList.contains('active'), false);
  assert.equal(element('previewRowHealth').style.display, 'none');
  finishCapture({ ok: true, image: 'late-image', left: 1, top: 2, width: 3, height: 4 });
  await new Promise(setImmediate);
  assert.equal(element('previewImgHealth').src, '');
});
