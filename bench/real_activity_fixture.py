#!/usr/bin/env python3
"""Generate a TEST-ONLY full-game sleep/reading page beside a downloaded bundle.

Usage: python3 bench/real_activity_fixture.py BUNDLE_DIR sleep|read [ZOMBIES]
Optional ZOMBIES (0..200) enables a cloaked population comparison fixture.
Compare explicit 0 with explicit N; the original baseline has no debug cloak.
Never install these debug effects in the distributed game. A new perf_* profile
is used. The real WASM runs all turns; only initial conditions are altered.
Screenshots/results POST to the isolated test server's capture endpoints.
"""
import json
import sys
from pathlib import Path

bundle = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
assert mode in ('sleep', 'read')
population = int(sys.argv[3]) if len(sys.argv) > 3 else None
assert population is None or 0 <= population <= 200
variant = mode if population is None else f'{mode}_z{population}'
workspace = Path(__file__).resolve().parents[1]
assert bundle.is_relative_to(workspace)
html = (bundle / 'index.html').read_text()
common = [
    {'u_add_trait': 'DEAF'},  # quiet baseline; no changes to production safety checks
    {'u_lose_trait': 'ILLITERATE'},
    {'u_lose_trait': 'MYOPIC'},
    {'u_lose_trait': 'HYPEROPIC'},
    {'math': ["u_val('intelligence_base') = 8"]},
    {'math': ["u_val('sleepiness') = 0"]},
    {'u_transform_radius': 0, 'ter_furn_transform': 'PERF_TEST_BED'},
]
if population is not None:
    # Test-only invisibility avoids combat; AI still runs, but target acquisition
    # differs from normal play. Apply the same cloak to the zero-monster control.
    common += [{'u_add_trait': 'DEBUG_CLOAK'}]
    if population:
        common += [{'u_spawn_monster': 'mon_zombie', 'real_count': population,
                    'min_radius': 20, 'max_radius': 35}]
if mode == 'sleep':
    common += [
        {'math': ["u_val('sleepiness') = 700"]},
        {'u_add_effect': 'sleep', 'duration': '4 hours'},
        {'u_message': '[FIXTURE] Four-hour sleep started on a bed (sleepiness 700).'},
    ]
else:
    common += [
        {'u_add_trait': 'PERF_TEST_LIGHT'},
        {'math': ["u_skill('mechanics') = 0"]},
        {'u_spawn_item': 'manual_mechanics'},
        {'u_message': '[FIXTURE] Skill book, light and mechanics 0 prepared.'},
    ]
fixture = [
    {'type': 'ter_furn_transform', 'id': 'PERF_TEST_BED', 'furniture': [
        {'result': 'f_bed', 'valid_furniture': ['f_null', 'f_chair', 'f_bench'], 'valid_flags': []}
    ]},
    {'type': 'mutation', 'id': 'PERF_TEST_LIGHT', 'name': 'Test reading light',
     'description': 'Isolated benchmark light, not a gameplay feature.',
     'points': 0, 'valid': False, 'debug': True, 'lumination': [['head', 60]]},
    {'type': 'effect_on_condition', 'id': 'PERF_TEST_START', 'eoc_type': 'EVENT',
     'required_event': 'game_start', 'effect': common},
]
script = r'''
<script>
(async function() {
    const mode = MODE, variant = VARIANT, population = POPULATION;
    const pause = ms => new Promise(r => setTimeout(r, ms));
    const send = (name, body) => fetch('/capture-' + name, { method: 'POST', body }).catch(() => {});
    const shot = () => send('screen', document.querySelector('canvas').toDataURL('image/png'));
    const key = k => {
        const code = k === 'Enter' ? 13 : k === 'F5' ? 116 : k === '.' ? 190 : k.toUpperCase().charCodeAt(0);
        const physical = k === 'Enter' || k === 'F5' ? k : k === '.' ? 'Period' : /[0-9]/.test(k) ? 'Digit' + k : 'Key' + k.toUpperCase();
        for (const type of ['keydown', 'keypress', 'keyup']) {
            if (type === 'keypress' && k.length !== 1) continue;
            document.dispatchEvent(new KeyboardEvent(type, { key: k, code: physical,
                keyCode: code, charCode: k.length === 1 ? k.charCodeAt(0) : 0, which: code,
                shiftKey: k.length === 1 && /[A-Z]/.test(k), bubbles: true }));
        }
    };
    Module.arguments = ['--seed', 'cdda-real-activity-fixture'];
    // Data package extraction finishes after preRun, before this callback.
    Module.onRuntimeInitialized = function() {
        const fs = Module.FS || FS;
        console.log('fixture:runtime', fs.cwd(), fs.readdir('/data/json').length);
        fs.writeFile('data/json/perf_test_fixture.json', JSON.stringify(FIXTURE));
        console.log('fixture:new data installed');
        const scenarios = JSON.parse(fs.readFile('data/json/scenarios.json', { encoding: 'utf8' }));
        const evacuee = scenarios.find(s => s.id === 'evacuee');
        evacuee.professions = ['unemployed'];
        evacuee.flags = Array.from(new Set([...(evacuee.flags || []), 'LONE_START']));
        // Replace read-only LZ4 file nodes with writable MEMFS test copies.
        fs.unlink('data/json/scenarios.json');
        fs.writeFile('data/json/scenarios.json', JSON.stringify(scenarios));
        const bindings = JSON.parse(fs.readFile('data/raw/keybindings.json', { encoding: 'utf8' }));
        bindings.find(b => b.id === 'quicksave').bindings = [{ input_method: 'keyboard_any', key: 'F5' }];
        fs.unlink('data/raw/keybindings.json');
        fs.writeFile('data/raw/keybindings.json', JSON.stringify(bindings));
        console.log('fixture:data ready');
    };
    const oldMount = window.CDDA_ON_IDBFS_MOUNTED;
    window.CDDA_ON_IDBFS_MOUNTED = function(...args) {
        if (oldMount) oldMount(...args);
        const fs = Module.FS || FS, dir = '/home/perf_' + variant + '/.cataclysm-dda/config';
        fs.mkdirTree(dir);
        const file = dir + '/options.json';
        let options = [];
        try { options = JSON.parse(fs.readFile(file, { encoding: 'utf8' })); } catch (_) {}
        // Isolate baseline noise/AI and use the normal renderer and tileset.
        for (const [name, value] of Object.entries({ SPAWN_DENSITY: '0.00', CITY_SIZE: '8' })) {
            const entry = options.find(o => o.name === name);
            if (entry) entry.value = value; else options.push({ name, value });
        }
        fs.writeFile(file, JSON.stringify(options));
        return true;
    };
    document.querySelector('#profile-input').value = 'perf_' + variant;
    document.querySelector('#profile-start').click();
    await pause(8000); await shot(); key('D');
    console.log('fixture:newgame', mode);
    await pause(30000); await shot();
    if (mode === 'read') {
        key('F5'); await pause(1200);
        // The spawned skill book is the only book in the starting inventory.
        key('R'); await pause(500); key('Enter'); await pause(2500); await shot();
        // First read identifies the book. Open it again for actual chapter work.
        key('R'); await pause(500); key('Enter'); await pause(700); key('Enter');
        console.log('fixture:reading requested');
    }
    // Populated reading takes longer than the original quiet baseline. This is
    // a bounded observation window, not a completion assertion; verify saves.
    for (let i = 0; i < 15; ++i) {
        await pause(4000); await shot();
        console.log('fixture:sample', mode, i, HEAPU8.length);
    }
    // Debug-started sleep does not increment ordinary action save bookkeeping.
    // One normal wait after waking makes quicksave eligible. Inspect the saved
    // sleep effect/activity: if still asleep, this run is incomplete, not a pass.
    if (mode === 'sleep') { key('.'); await pause(700); }
    key('F5'); await pause(3000); await shot();
    const fs = Module.FS || FS;
    const saves = {}, base = '/home/perf_' + variant + '/.cataclysm-dda/save';
    try {
        for (const world of fs.readdir(base).filter(n => n !== '.' && n !== '..')) {
            const dir = base + '/' + world;
            console.log('fixture:world files', fs.readdir(dir));
            for (const name of fs.readdir(dir)) {
                if (name.endsWith('.sav') || name === 'worldoptions.json') {
                    const bytes = fs.readFile(dir + '/' + name);
                    if (bytes.length < 500000) saves[world + '/' + name] = Array.from(bytes);
                }
            }
        }
    } catch (e) { console.log('fixture:save snapshot unavailable', String(e)); }
    const error = document.querySelector('#error-detail')?.textContent || '';
    console.log('fixture:snapshot', Object.keys(saves), error);
    await send('result', JSON.stringify({ mode, variant, population, saves, heap: HEAPU8.length, error }));
    const done = document.createElement('i'); done.id = 'fixture-finished'; document.body.appendChild(done);
})();
</script>
'''.replace('MODE', json.dumps(mode)).replace('VARIANT', json.dumps(variant)).replace(
    'POPULATION', json.dumps(population)).replace('FIXTURE', json.dumps(fixture))
output = bundle / ('fixture-' + variant + '.html')
output.write_text(html.replace('</body>', script + '</body>'))
print(output)
