// Acceptance contracts for real-game browser measurements. Keep these checks
// independent of Playwright so failure cases can be tested deterministically.
const ACT_NULL = 'ACT_NULL';

function hasText(messages, text) {
  return messages.some(entry => String(entry?.message ?? '').includes(text));
}

function hasPerfMessage(messages, expression) {
  return messages.some(entry => expression.test(String(entry?.message ?? '')));
}

export function validateActiveRenderer({ renderer, consoleMessages }) {
  const active = [];
  for (const entry of consoleMessages ?? []) {
    if (entry?.phase === 'reload-restore') continue;
    const message = String(entry?.text ?? entry?.message ?? '');
    const match = message.match(/Active renderer:\s*\d+\/([A-Za-z0-9_-]+)/i);
    if (match) active.push(match[1].toLowerCase());
  }
  const checks = [];
  const failures = [];
  const expect = (condition, name, detail) => {
    checks.push({ name, passed: Boolean(condition), detail });
    if (!condition) failures.push({ name, detail });
  };
  const expected = renderer === 'software' || renderer === 'opengles2' ? renderer : null;
  expect(active.length > 0, 'active-renderer-observed',
    active.length ? active.join(', ') : 'No initial-page Active renderer log was observed.');
  if (expected) {
    expect(active.length > 0 && active.every(value => value === expected), 'active-renderer-matches-request',
      `expected=${expected}; actual=${active.join(', ') || 'missing'}`);
  } else {
    expect(active.length > 0 && active.every(value => ['software', 'opengles2'].includes(value)),
      'default-renderer-valid', `actual=${active.join(', ') || 'missing'}`);
  }
  return { ok: failures.length === 0, requested: renderer, actual: active, checks, failures };
}

export function validateFixtureOutcome({ scenario, population, renderer, fixtureResult, savedGameState }) {
  const failures = [];
  const checks = [];
  const expect = (condition, name, detail) => {
    checks.push({ name, passed: Boolean(condition), detail });
    if (!condition) failures.push({ name, detail });
  };

  expect(['read4', 'read', 'sleep'].includes(scenario), 'scenario-supported', `scenario=${scenario}`);
  if (!fixtureResult) {
    expect(false, 'fixture-result-present', 'No fixture result payload was captured.');
    return { ok: false, scenario, checks, failures };
  }

  const expectedMode = scenario === 'read4' ? 'read' : scenario;
  expect(fixtureResult.mode === expectedMode, 'fixture-mode', `expected=${expectedMode}; actual=${fixtureResult.mode}`);
  expect(fixtureResult.renderer === renderer, 'fixture-renderer', `expected=${renderer}; actual=${fixtureResult.renderer}`);
  expect(Number(fixtureResult.population) === Number(population), 'fixture-population',
    `expected=${population}; actual=${fixtureResult.population}`);
  let savedZombieCount = 0;
  for (const [name, values] of Object.entries(fixtureResult.saves ?? {})) {
    if (!name.endsWith('.sav') || !Array.isArray(values)) continue;
    const raw = new TextDecoder().decode(Uint8Array.from(values));
    savedZombieCount += (raw.match(/"typeid"\s*:\s*"mon_zombie"/g) ?? []).length;
  }
  expect(savedZombieCount === Number(population), 'population-monster-count',
    `requested=${population}; serialized mon_zombie entries=${savedZombieCount}`);
  expect(Number.isFinite(fixtureResult.heap) && fixtureResult.heap > 0, 'wasm-heap-present',
    `heap=${fixtureResult.heap}`);
  expect(!String(fixtureResult.error ?? '').trim(), 'game-error-overlay-empty',
    String(fixtureResult.error ?? '').trim() || 'No game error overlay reported.');

  const saveNames = Object.keys(fixtureResult.saves ?? {});
  expect(saveNames.some(name => name.endsWith('.sav')), 'character-save-present',
    saveNames.filter(name => name.endsWith('.sav')).join(', ') || 'No character save captured.');
  expect(savedGameState && Number.isFinite(savedGameState.turn), 'saved-game-state-parsed',
    savedGameState?.parseError ?? `turn=${savedGameState?.turn}`);
  if (!savedGameState || !Number.isFinite(savedGameState.turn)) {
    return { ok: failures.length === 0, scenario, checks, failures };
  }
  if (scenario !== 'read') {
    expect(savedGameState.activity === ACT_NULL, 'activity-finished',
      `expected=${ACT_NULL}; actual=${savedGameState.activity}`);
  }

  const activityMessages = savedGameState.activityMessages ?? [];
  const perfMessages = savedGameState.perfMessages ?? [];
  if (scenario === 'read4') {
    const reading = fixtureResult.reading;
    expect(reading && reading.initialLevel === 4, 'reading-start-level-four',
      `initialLevel=${reading?.initialLevel}`);
    expect(reading && Number.isFinite(reading.initialTurn) && Number.isFinite(reading.finalTurn) &&
      reading.finalTurn > reading.initialTurn, 'reading-turns-advanced',
      `initial=${reading?.initialTurn}; final=${reading?.finalTurn}`);
    expect(reading?.reachedLevel5 === true && Number(reading.finalKnowledge) >= 5, 'reading-reached-level-five',
      `reachedLevel5=${reading?.reachedLevel5}; finalKnowledge=${reading?.finalKnowledge}`);
    expect(reading?.finalActivity?.type === ACT_NULL, 'reading-activity-finished',
      `activity=${reading?.finalActivity?.type}`);
    expect(hasText(activityMessages, 'レベル5に上昇しました。') || hasText(activityMessages, '読み終えました。'),
      'reading-completion-message', 'Expected a saved level-up or reading-completion message.');
    expect(hasPerfMessage(perfMessages, /^\[perf\] 読書中: \d+ turns in \d+ ms/),
      'reading-performance-log', 'Expected a valid [perf] reading turn/time log.');
  } else if (scenario === 'sleep') {
    expect(hasText(activityMessages, '眠りに落ちました。'), 'sleep-start-message',
      'Expected a saved sleep-start message.');
    expect(hasText(activityMessages, '目が覚めました。'), 'sleep-wake-message',
      'Expected a saved wake-up message.');
    expect(hasPerfMessage(perfMessages, /^\[perf\] sleep: 14400 turns in \d+ ms/),
      'sleep-four-hours-completed', 'Expected the 14,400-turn four-hour sleep performance log.');
  } else if (scenario === 'read') {
    expect(hasPerfMessage(perfMessages, /^\[perf\] 読書中: \d+ turns in \d+ ms/),
      'reading-performance-log', 'Expected a valid [perf] reading turn/time log.');
  }

  return { ok: failures.length === 0, scenario, checks, failures };
}

// The data package may cancel its initial fetch after Emscripten switches to the
// local cached/resource path. This one observed abort is allowlisted narrowly;
// any other failed request is a measurement failure and must be investigated.
export function classifyRequestFailures(requestFailures) {
  const allowed = [];
  const unexpected = [];
  for (const item of requestFailures ?? []) {
    let pathname = '';
    try { pathname = new URL(item.url).pathname; } catch (_) {}
    if (pathname.endsWith('/cataclysm-tiles.data') && item.failure === 'net::ERR_ABORTED') {
      allowed.push({ ...item, reason: 'known Emscripten data-package request abort; runtime and bundle checks must still pass' });
    } else {
      unexpected.push(item);
    }
  }
  return { ok: unexpected.length === 0, allowed, unexpected };
}
