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

function scenario({
  profileValue = null,
  lastValue = null,
  storageThrows = false,
  lockState = new Set(),
  lockSupport = true,
  lockFailure = false,
} = {}) {
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
  const notices = [];
  const context = {
    localStorage: storage,
    window,
    navigator: lockSupport ? {
      locks: {
        request(name, options, callback) {
          if (lockFailure) return Promise.reject(new Error('lock API failed'));
          if (options.ifAvailable && lockState.has(name)) return Promise.resolve(callback(null));
          lockState.add(name);
          return Promise.resolve(callback({ name }));
        },
      },
    } : {},
    Module: gameModule,
    profileInput,
    profileList,
    profileStart,
    profileScreen,
    loadingMessage,
    document: { createElement: () => element() },
    boot: () => { boots++; },
    noticeMessage: message => notices.push(message),
    console: { warn() {} },
  };
  vm.runInNewContext('let booted = false;\n' + source, context);
  return { profileInput, profileList, profileStart, profileScreen, loadingMessage, window, data, notices, lockState, boots: () => boots };
}

async function run() {
{
  const e = scenario({ profileValue: '{"not":"an array"}', lastValue: 'playerone' });
  assert.equal(e.profileList.children.length, 0);
  assert.equal(e.profileInput.value, 'playerone');
}
{
  const e = scenario({ profileValue: 'not valid JSON' });
  assert.equal(e.profileList.children.length, 0);
}
{
  const e = scenario({ lastValue: '../evil' });
  assert.equal(e.profileInput.value, '', 'non-canonical last profile must not map to another save name');
}
{
  const e = scenario({
    profileValue: JSON.stringify(['playerone', 'valid', 7, '../evil', 'PLAYER_ONE', 'playerone']),
  });
  assert.deepEqual(e.profileList.children.map(chip => chip.textContent), ['playerone', 'valid']);
}
{
  const e = scenario({ storageThrows: true });
  assert.equal(e.profileInput.value, '');
  e.profileInput.value = 'New User';
  await e.profileStart.onclick();
  assert.equal(e.window.CDDA_PROFILE, undefined, 'spaces must not be stripped into another save ID');
  assert.equal(e.boots(), 0, 'ambiguous profile name must not start a game');
  assert(e.notices.some(message => message.includes('save-profile collisions')));
  e.profileInput.value = 'NewUser';
  await e.profileStart.onclick();
  assert.equal(e.window.CDDA_PROFILE, 'newuser');
  assert.equal(e.boots(), 1, 'valid canonicalizable names still work when storage is blocked');
}
{
  const e = scenario({ profileValue: JSON.stringify(['ab']) });
  e.profileInput.value = 'a!b';
  await e.profileStart.onclick();
  assert.equal(e.window.CDDA_PROFILE, undefined, 'punctuation must not alias the existing "ab" save directory');
  assert.equal(e.boots(), 0);
  assert.deepEqual(JSON.parse(e.data.get('cdda_web_profiles')), ['ab']);
  e.profileInput.value = 'a'.repeat(24) + 'b';
  await e.profileStart.onclick();
  assert.equal(e.window.CDDA_PROFILE, undefined, 'overlong names must not truncate onto a 24-character profile');
  assert.equal(e.boots(), 0);
  e.profileInput.value = 'AB';
  await e.profileStart.onclick();
  assert.equal(e.window.CDDA_PROFILE, 'ab', 'case folding remains an intentional canonicalization');
  assert.equal(e.boots(), 1);
}
// Independent page contexts share an origin-wide lock registry.
{
  const lockState = new Set();
  const first = scenario({ lockState });
  first.profileInput.value = 'playerone';
  await first.profileStart.onclick();
  assert.equal(first.boots(), 1, 'first tab acquires the profile lock and starts');

  const second = scenario({
    profileValue: JSON.stringify(['playerone']),
    lockState,
  });
  second.profileInput.value = 'playerone';
  await second.profileStart.onclick();
  assert.equal(second.boots(), 0, 'second tab must not start a profile already held elsewhere');
  assert(second.notices.some(message => message.includes('already open in another tab')));

  const other = scenario({ lockState });
  other.profileInput.value = 'playertwo';
  await other.profileStart.onclick();
  assert.equal(other.boots(), 1, 'different profiles may run in different tabs');
}
{
  for (const options of [{ lockSupport: false }, { lockFailure: true }]) {
    const e = scenario(options);
    e.profileInput.value = 'playerone';
    await e.profileStart.onclick();
    assert.equal(e.boots(), 0, 'fail closed when exclusive save locks cannot be established');
    assert(e.notices.some(message => message.includes('Exclusive save locks are unavailable')));
  }
}
console.log('PASS profiles: canonical IDs, collision prevention, cross-tab exclusive locks and recovery');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
