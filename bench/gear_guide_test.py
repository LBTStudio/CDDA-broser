#!/usr/bin/env python3
"""Compile the shipped numeric advisor and guard its actual UI integration.
Usage: python3 bench/gear_guide_test.py /path/to/patched/cdda
No whole-game performance claim: inputs below are deterministic fixtures.
"""
from pathlib import Path
import json
import re
import subprocess
import sys

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / 'out/gear-guide'
out.mkdir(parents=True, exist_ok=True)
ui = (source / 'src/armor_layers.cpp').read_text()
start = ui.index('// Read-only, on-demand advice.')
end = ui.index('void outfit::sort_armor(', start)
guide = ui[start:end]
assert '++visited > 512' in guide
assert 'candidates.size() >= cata_gear::max_items' in guide
assert 'guy.can_wear( *it, true ).success()' in guide
assert 'it.resist( types[d], false, bp, 50 )' in guide
for forbidden in ['wear_item', 'takeoff', 'mod_moves', 'assign_activity', 'rng', 'one_in', 'update_bodytemp']:
    assert not re.search(r'\b' + forbidden + r'\s*\(', guide), forbidden
assert 'if( !power_armor &&' in guide
assert 'special( it ) || !it.empty()' in guide
assert 'std::vector<const item *>' in guide
# Existing equip/remove/sort handlers remain byte-identical to upstream.
rest = ui[:start] + ui[end:]
for inc in ['cata_gear_guide.h', 'calendar.h', 'weather.h', 'ret_val.h', 'visitable.h']:
    rest = rest.replace('#include "' + inc + '"\n', '')
rest = rest.replace('    ctxt.register_action( "GEAR_GUIDE" );\n', '')
rest = rest.replace('std::string header_title = _( "Sort Armor" ) + string_format( " [%s] 装備ナビ", ctxt.get_desc( "GEAR_GUIDE" ) );', 'std::string header_title = _( "Sort Armor" );')
rest = rest.replace('        } else if( action == "GEAR_GUIDE" ) {\n            show_gear_guide( guy, worn );\n', '')
old = subprocess.check_output(['git', '-C', str(source), 'show', 'HEAD:src/armor_layers.cpp'], text=True)
assert rest == old
bindings = json.loads((source / 'data/raw/keybindings.json').read_text())
entries = [b for b in bindings if b.get('category') == 'SORT_ARMOR' and b.get('id') == 'GEAR_GUIDE']
assert len(entries) == 1 and entries[0]['bindings'] == [{'input_method': 'keyboard_any', 'key': 'F2'}]
assert ui.count('show_gear_guide( guy, worn );') == 1

program = r'''
#include "cata_gear_guide.h"
#include <cassert>
#include <chrono>
#include <iostream>
#include <random>
using namespace cata_gear;
static candidate gear(double cover, double armor, double enc, double warmth, bool worn = false) {
    candidate c; c.worn = worn; c.parts.resize(1);
    c.parts[0].coverage = cover; c.parts[0].resist = {armor,armor,armor};
    c.parts[0].encumbrance = enc; c.parts[0].warmth = warmth;
    c.parts[0].layers = 1; return c;
}
static void exclusive(std::vector<candidate> &v) {
    for(std::size_t i=0;i<v.size();++i) for(std::size_t j=0;j<v.size();++j)
        if(i!=j) v[i].conflicts |= bit(j);
}
int main() {
    environment e; e.weights={1};
    auto light=gear(95,1,1,0,true), heavy=gear(95,10,15,0);
    std::vector<candidate> v={light,heavy}; exclusive(v);
    assert(recommend(v,e,1).chosen==bit(1)); // defense priority
    assert(recommend(v,e,2).chosen==bit(0)); // mobility priority
    v[0].locked=true; assert(recommend(v,e,1).chosen==bit(0));
    v[0].locked=false; v[0].storage=20;
    assert(recommend(v,e,1).chosen==bit(0));
    e.preserve_storage=false; assert(recommend(v,e,1).chosen==bit(1));
    // Temperature, not a hard-coded season, determines warmth preference.
    v={gear(95,1,1,0,true),gear(95,1,1,60)}; exclusive(v);
    e.celsius=-12; assert(recommend(v,e,0).chosen==bit(1));
    e.celsius=30; assert(recommend(v,e,0).chosen==bit(0));
    e.celsius=18; e.preserve_storage=true;
    // High resistance with poor coverage cannot strip an adequately covered part.
    v={light,gear(30,100,0,0)}; exclusive(v);
    assert(recommend(v,e,1).chosen==bit(0));
    // Equal choices keep the current item; no churn, RNG or random tie breaks.
    v={light,light}; v[1].worn=false; exclusive(v);
    assert(recommend(v,e,0).chosen==bit(0));
    assert(recommend({},e,0).chosen==0);
    auto bad=gear(95,10,1,0); bad.parts.clear();
    assert(recommend({bad},e,0).capped);
    assert(recommend(std::vector<candidate>(49,light),e,0).capped);
    environment too_many; too_many.weights.resize(33,1);
    assert(recommend({},too_many,0).capped);
    // Combined recommendations, not merely one best item per report.
    environment two; two.weights={1,1};
    candidate a=gear(95,10,1,0), b=a;
    a.parts.push_back(part{}); b.parts.insert(b.parts.begin(),part{});
    assert(recommend({a,b},two,0).chosen==(bit(0)|bit(1)));
    // Overlapping protection never gets advertised as additive coverage.
    auto coverage=assess({light,light},3,e,0);
    assert(coverage.parts[0].coverage==95);
    assert(coverage.encumbrance>2); // same-layer penalty is included
    auto breathable=gear(95,1,1,30), sealed=breathable;
    sealed.parts[0].breathability=0; e.celsius=30;
    assert(assess({sealed},1,e,0).thermal>assess({breathable},1,e,0).thermal);
    // Exercise the advertised maximum, including deliberate search truncation.
    environment maximum; maximum.weights.resize(max_parts,1);
    candidate useful=gear(95,10,1,0); useful.parts.resize(max_parts,useful.parts[0]);
    std::vector<candidate> largest(max_items,useful);
    const auto full=recommend(largest,maximum,1);
    assert(full.evaluations==int(max_items)*max_passes && full.capped);
    std::cout << "PASS maximum fixture: 48 candidates / 32 parts / " << full.evaluations << " evaluations\n";
    // Deterministic randomized input checks; the test RNG is not in production.
    std::mt19937 gen(617);
    int scenarios=0;
    auto begin=std::chrono::steady_clock::now();
    for(int trial=0;trial<180;++trial) {
        const int n=1+gen()%48; const int parts=1+gen()%8;
        environment env; env.celsius=int(gen()%71)-30; env.weights.resize(parts,1);
        env.preserve_storage=gen()%2;
        std::vector<candidate> items(n); selection initial=0, locked=0;
        for(int i=0;i<n;++i) {
            auto &c=items[i]; c.parts.resize(parts); c.worn=i<n/3;
            c.locked=c.worn && gen()%3==0; c.storage=gen()%15;
            if(c.worn) initial|=bit(i);
            if(c.locked) locked|=bit(i);
            for(auto &p:c.parts) {
                p.coverage=gen()%101; p.resist={double(gen()%25),double(gen()%25),double(gen()%25)};
                p.encumbrance=gen()%30; p.warmth=gen()%80; p.breathability=gen()%101;
                p.layers=1U<<(gen()%5);
            }
        }
        for(int i=0;i<n;++i) for(int j=i+1;j<n;++j) if(gen()%5==0) {
            items[i].conflicts|=bit(j);items[j].conflicts|=bit(i);
        }
        const auto original=items;
        for(int mode=0;mode<3;++mode) {
            auto r=recommend(items,env,mode), again=recommend(items,env,mode);
            auto before=assess(items,initial,env,mode), after=assess(items,r.chosen,env,mode);
            assert(r.chosen==again.chosen && r.evaluations==again.evaluations);
            assert((r.chosen&locked)==locked && after.score+0.001>=before.score);
            assert(r.evaluations<=int(max_items)*max_passes);
            assert(!env.preserve_storage || after.storage+0.001>=before.storage);
            for(int b=0;b<parts;++b) assert(after.parts[b].coverage>=std::min(80.0,before.parts[b].coverage));
            for(int i=0;i<n;++i) {
                assert(items[i].worn==original[i].worn && items[i].locked==original[i].locked);
                assert(items[i].conflicts==original[i].conflicts && items[i].parts[0].warmth==original[i].parts[0].warmth);
                if((r.chosen&bit(i)) && !(initial&bit(i))) assert(!(r.chosen&items[i].conflicts));
            }
            ++scenarios;
        }
    }
    const auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-begin).count();
    std::cout << "PASS " << scenarios << " deterministic randomized scenarios plus directed constraints; fixture ms=" << elapsed << "\n";
}
'''
(out / 'test.cpp').write_text(program)
subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                '-I' + str(source / 'src'), str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
subprocess.run([str(out / 'test')], check=True)
print('PASS guide integration: F2 binding, no gameplay mutations, bounded scan, original armor actions unchanged')
