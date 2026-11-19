/* Shared node harness for frontend behaviour tests.
   Loads the same scripts index.html loads (in the same order) into a vm context, so tests can
   call real render functions instead of asserting on source strings. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const STATIC = path.join(ROOT, 'static');

function scriptFiles() {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  return [...html.matchAll(/<script src="\/static\/([^"]+\.js)"><\/script>/g)].map(m => m[1]);
}
function read(file) {
  return fs.readFileSync(path.join(STATIC, file), 'utf8');
}
// The app's own event wiring and bootstrap live at the end of app.js; drop them so tests can
// load the bundle without binding DOM listeners or starting network calls.
function coreSource() {
  const parts = [];
  for (const file of scriptFiles()) {
    let code = read(file);
    if (file === 'app.js') code = code.slice(0, code.indexOf("document.addEventListener('click'"));
    parts.push('/* ' + file + ' */\n' + code);
  }
  return parts.join('\n');
}
function bundle() {
  return scriptFiles().map(file => '/* ' + file + ' */\n' + read(file)).join('\n');
}
// Slice by markers across the whole bundle (file layout independent).
function slice(startMarker, endMarker) {
  const code = bundle();
  const start = code.indexOf(startMarker);
  if (start < 0) throw new Error('start marker not found: ' + startMarker);
  const end = endMarker ? code.indexOf(endMarker, start + startMarker.length) : code.length;
  if (end <= start) throw new Error('end marker not found: ' + endMarker);
  return code.slice(start, end);
}
function boot(stubs = {}) {
  return vm.createContext(Object.assign({ console }, stubs));
}
function run(ctx, code) {
  return vm.runInContext(code, ctx);
}
function load(ctx, ...sources) {
  run(ctx, sources.join('\n'));
  return ctx;
}
// top-level `const`/`let` stay in the script's lexical scope; publish the ones under test.
function publish(ctx, ...names) {
  run(ctx, names.map(name => 'this.' + name + ' = ' + name + ';').join('\n'));
  return ctx;
}
function loadCore(ctx, extra = '') {
  const code = coreSource() + '\n' + extra + '\n' + namesPrelude;
  return run(ctx, code);
}
const namesPrelude = '';

module.exports = { ROOT, STATIC, scriptFiles, read, bundle, coreSource, slice, boot, run, load, publish, loadCore };
