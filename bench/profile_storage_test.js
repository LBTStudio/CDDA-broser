// Exercise profile-list recovery using the actual shell code.
const assert = require('node:assert/strict');
const fsNode = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fsNode.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const start = html.indexOf('/* Per-player save isolation:');
const end = html.indexOf('/* ---- Persistent asset cache', start);
assert(start >= 0 && end > start, 'find actual profile storage code');
const source = html.slice(start, end);

function element() {
  return {
    children: [],
    style: {},
    value: '',
    placeholder: '',
    appendChild(child) { this.children.push(child); },
    addEventListener(name, handler) { this['on_' + name] = handler; },
    set innerHTML(value) { this.children = []; this._innerHTML = value; },
    get innerHTML() { return this._innerHTML || ''; },
  };
}

function scenario({ profileValue = null, lastValue = null, storageThrows = false } = {}) {
  const data = new Map();
  if (profileValue !== null) data.set('cdda_web_profiles', profileValue);
  if (lastValue !== null) data.set('cdda_web_last_profile', lastValue);
  const storage = {
    getItem(key) {
      if (storageThrows) throw new Error('storage blocked');
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      if (storageThrows) throw new Error('storage blocked');
      data.set(key, String(value));
    },
  };
  const profileInput = element();
  const profileList = element();
  const profileStart = element();
  const profileScreen = element();
  const loadingMessage = element();
  const gameModule = { ENV: {} };
  const window = { Module: gameModule };
  let boots = 0;
  const context = {
    localStorage: storage,
    window,
    Module: gameModule,
    profileInput,
    profileList,
    profileStart,
    profileScreen,
    loadingMessage,
    document: { createElement: () => element() },
    boot: () => { boots++; },
    console: { warn() {} },
  };
  vm.runInNewContext(source, context);
  return { profileInput, profileList, profileStart, profileScreen, loadingMessage, window, data, boots: () => boots };
}

{
  const e = scenario({ profileValue: '{"not":"an array"}', lastValue: 'PLAYER ONE' });
  assert.equal(e.profileList.children.length, 0);
  assert.equal(e.profileInput.value, 'playerone');
}
{
  const e = scenario({ profileValue: 'not valid JSON' });
  assert.equal(e.profileList.children.length, 0);
}
{
  const e = scenario({
    profileValue: JSON.stringify(['Player One', 'Valid', 7, '../evil', 'PLAYER_ONE']),
  });
  assert.deepEqual(e.profileList.children.map(chip => chip.textContent), ['playerone', 'valid']);
}
{
  const e = scenario({ storageThrows: true });
  assert.equal(e.profileInput.value, '');
  e.profileInput.value = 'New User';
  assert.doesNotThrow(() => e.profileStart.onclick());
  assert.equal(e.window.CDDA_PROFILE, 'newuser');
  assert.equal(e.boots(), 1);
}
console.log('PASS profiles: corrupt JSON, wrong shape, invalid entries, normalization, and blocked storage');
