import assert from 'node:assert/strict';
import { classifyRequestFailures, extractWasmHeapSamples, validateActiveRenderer, validateFixtureOutcome } from './measurement_contract.mjs';

function base({ scenario = 'read4', renderer = 'game', population = 0 } = {}) {
  const sleep = scenario === 'sleep';
  const savedGameState = {
    turn: sleep ? 5227201 : 5276017,
    activity: 'ACT_NULL',
    activityMessages: sleep
      ? [{ message: '眠りに落ちました。' }, { message: '目が覚めました。' }]
      : [{ message: '機械整備に関する理論的知識がレベル5に上昇しました。' }, { message: '読み終えました。' }],
    perfMessages: sleep
      ? [{ message: '[perf] sleep: 14400 turns in 3337 ms (0.232 ms/turn)' }]
      : [{ message: '[perf] 読書中: 63069 turns in 12429 ms (0.197 ms/turn)' }]
  };
  const fixtureResult = {
    mode: sleep ? 'sleep' : 'read',
    variant: scenario + '_z' + population,
    renderer,
    population,
    heap: 668205056,
    error: '',
    saves: { 'world/player.sav': population > 0
      ? Array.from(new TextEncoder().encode(JSON.stringify({
          monsters: Array.from({ length: population }, () => ({ typeid: 'mon_zombie' }))
        })))
      : [1, 2, 3] },
    reading: sleep ? null : {
      initialLevel: 4,
      initialTurn: 5212947,
      finalTurn: 5276017,
      finalKnowledge: 5,
      reachedLevel5: true,
      finalActivity: { type: 'ACT_NULL' }
    }
  };
  return { scenario, renderer, population, fixtureResult, savedGameState };
}

for (const scenario of ['read4', 'sleep']) {
  const state = base({ scenario });
  const result = validateFixtureOutcome(state);
  assert.equal(result.ok, true, JSON.stringify(result.failures));
  assert.ok(result.checks.every(check => check.passed));
}

{
  const state = base({ scenario: 'read4' });
  state.fixtureResult.reading.finalKnowledge = 4;
  state.fixtureResult.reading.reachedLevel5 = false;
  const result = validateFixtureOutcome(state);
  assert.equal(result.ok, false);
  assert.ok(result.failures.some(item => item.name === 'reading-reached-level-five'));
}

{
  const state = base({ scenario: 'sleep' });
  state.savedGameState.activityMessages = [{ message: '眠りに落ちました。' }];
  const result = validateFixtureOutcome(state);
  assert.equal(result.ok, false);
  assert.ok(result.failures.some(item => item.name === 'sleep-wake-message'));
}

{
  const state = base({ scenario: 'read4' });
  state.fixtureResult.error = 'fatal game error';
  state.fixtureResult.renderer = 'software';
  const result = validateFixtureOutcome(state);
  assert.equal(result.ok, false);
  assert.ok(result.failures.some(item => item.name === 'game-error-overlay-empty'));
  assert.ok(result.failures.some(item => item.name === 'fixture-renderer'));
}

{
  const result = validateFixtureOutcome({
    scenario: 'read4', renderer: 'game', population: 0,
    fixtureResult: null, savedGameState: null
  });
  assert.equal(result.ok, false);
  assert.ok(result.failures.some(item => item.name === 'fixture-result-present'));
}

for (const count of [32, 64, 128, 200]) {
  const state = base({ scenario: 'sleep', renderer: 'game', population: count });
  const good = validateFixtureOutcome(state);
  assert.equal(good.ok, true, JSON.stringify(good.failures));
  assert.ok(good.checks.some(check => check.name === 'population-monster-count' && check.passed));
  state.fixtureResult.saves['world/player.sav'] = Array.from(new TextEncoder().encode(JSON.stringify({
    monsters: Array.from({ length: count - 1 }, () => ({ typeid: 'mon_zombie' }))
  })));
  const bad = validateFixtureOutcome(state);
  assert.equal(bad.ok, false);
  assert.ok(bad.failures.some(item => item.name === 'population-monster-count'));
}

{
  const profile = extractWasmHeapSamples([
    { text: 'fixture:sample sleep 0 512', atMs: 100 },
    { text: 'fixture:sample sleep 1 512', atMs: 4100 },
    { text: 'fixture:sample sleep 2 1024', atMs: 8100 },
    { text: 'fixture:sample sleep 3 2048', atMs: 12100, phase: 'reload-restore' }
  ]);
  assert.equal(profile.sampleCount, 3);
  assert.equal(profile.initialBytes, 512);
  assert.equal(profile.peakBytes, 1024);
  assert.equal(profile.finalBytes, 512);
  assert.equal(profile.growthTransitions.length, 2);
  assert.deepEqual(profile.distinctSizes, [512, 1024]);
}

{
  const goodSoftware = validateActiveRenderer({
    renderer: 'software',
    consoleMessages: [
      { text: 'INFO : Active renderer: 1/software' },
      { text: 'INFO : Active renderer: 0/opengles2', phase: 'reload-restore' }
    ]
  });
  assert.equal(goodSoftware.ok, true, JSON.stringify(goodSoftware.failures));

  const wrongRenderer = validateActiveRenderer({
    renderer: 'software',
    consoleMessages: [{ text: 'INFO : Active renderer: 0/opengles2' }]
  });
  assert.equal(wrongRenderer.ok, false);
  assert.ok(wrongRenderer.failures.some(item => item.name === 'active-renderer-matches-request'));

  const missingRenderer = validateActiveRenderer({ renderer: 'opengles2', consoleMessages: [] });
  assert.equal(missingRenderer.ok, false);
  assert.ok(missingRenderer.failures.some(item => item.name === 'active-renderer-observed'));
}

{
  const result = classifyRequestFailures([
    { url: 'http://127.0.0.1:1234/cataclysm-tiles.data', failure: 'net::ERR_ABORTED' },
    { url: 'http://127.0.0.1:1234/missing.wasm', failure: 'net::ERR_CONNECTION_RESET' }
  ]);
  assert.equal(result.ok, false);
  assert.equal(result.allowed.length, 1);
  assert.equal(result.unexpected.length, 1);
}
{
  const result = classifyRequestFailures([
    { url: 'http://127.0.0.1:1234/cataclysm-tiles.data', failure: 'net::ERR_ABORTED' }
  ]);
  assert.equal(result.ok, true);
  assert.equal(result.allowed.length, 1);
}
console.log('PASS measurement contracts: real-game state, population counts, active renderer, heap samples, and request-failure triage');
