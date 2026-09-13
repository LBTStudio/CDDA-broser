#!/usr/bin/env python3
"""Differential active-item snapshot tests; NOT full-game/Chromebook benchmarks.

Usage: item_snapshot_test.py PATCHED_0I [native|sanitized|wasm]
Source HEAD must be pristine 0.I. Compiles the actual cache declarations and
complete implementations, with real safe_reference.{h,cpp}. Item, pocket and
coordinate dependencies are controlled fixtures. Each command is resource
monitored; no full game build or browser is launched.
"""
from pathlib import Path
import os
import re
import shlex
import signal
import subprocess
import sys
import time

source = Path(sys.argv[1]).resolve()
backend = sys.argv[2] if len(sys.argv) > 2 else 'native'
assert backend in ('native', 'sanitized', 'wasm')
out = Path(__file__).resolve().parent / 'out' / 'item-snapshot'
out.mkdir(parents=True, exist_ok=True)


def original(name):
    return subprocess.check_output(
        ['git', '-C', str(source), 'show', 'HEAD:src/' + name], text=True)


def block(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


old_h, old_cpp = (original('active_item_cache.' + ext) for ext in ('h', 'cpp'))
new_h, new_cpp = ((source / 'src' / ('active_item_cache.' + ext)).read_text()
                  for ext in ('h', 'cpp'))
# The collection, expiry, rotation and recursive add logic must remain exact.
start = 'bool active_item_cache::add('
old_tail, new_tail = old_cpp[old_cpp.index(start):], new_cpp[new_cpp.index(start):]
reservation = '''        const size_t count = kv.second.size();
        const size_t quota = count / static_cast<size_t>( kv.first );
        // The loop below includes quota == 0: retain that upstream scheduling
        // exactly, but reserve enough space to avoid growing a second time.
        return prev + quota + static_cast<size_t>( quota < count );'''
assert new_tail.replace(reservation,
    '        return prev + kv.second.size() / static_cast<size_t>( kv.first );') == old_tail
for path in (source / 'src').glob('*'):
    if path.suffix in ('.h', '.cpp') and path.name not in ('active_item_cache.h', 'active_item_cache.cpp'):
        assert 'pocket_chain' not in path.read_text(), f'unreviewed chain consumer: {path}'

program = r'''
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <list>
#include <memory>
#include <new>
#include <numeric>
#include <string>
#include <tuple>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>
#include "safe_reference.h"
static bool counting = false, fail_new = false;
static size_t allocations = 0, bytes = 0;
[[gnu::noinline]] void *operator new(std::size_t n) {
    if (fail_new) throw std::bad_alloc();
    if (counting) { ++allocations; bytes += n; }
    if (void *p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void *p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void *p, std::size_t) noexcept { std::free(p); }
struct point_rel_ms {
    int xx = 0, yy = 0;
    int &x() { return xx; } int &y() { return yy; }
    int x() const { return xx; } int y() const { return yy; }
    point_rel_ms raw() const { return *this; }
    point_rel_ms rotate(int turns, point_rel_ms dim) const {
        auto p = *this;
        while (turns-- > 0) p = {dim.yy - 1 - p.yy, p.xx};
        return p;
    }
    point_rel_ms &operator-=(point_rel_ms b) { xx -= b.xx; yy -= b.yy; return *this; }
};
struct point_sm_ms { int x, y; };
point_rel_ms rebase_rel(point_sm_ms p) { return {p.x, p.y}; }
struct item;
static std::vector<int> pocket_reads;
static bool trace_reads = false;
struct item_pocket {
    int id = 0;
    float spoil = 1.0f;
    bool sealed = false;
    std::vector<item *> contents;
    float spoil_multiplier() const {
        if (trace_reads) pocket_reads.push_back(id * 2);
        return spoil;
    }
    bool can_contain_liquid(bool allow) const {
        assert(!allow);
        if (trace_reads) pocket_reads.push_back(id * 2 + 1);
        return sealed;
    }
    const std::vector<item *> &all_items_top() const { return contents; }
};
struct item {
    static constexpr int NO_PROCESSING = -1;
    int id = 0, speed = 1;
    bool corpse = false, explosive = false;
    safe_reference_anchor anchor;
    std::vector<item_pocket *> pockets;
    safe_reference<item> get_safe_reference() { return anchor.reference_to(this); }
    int processing_speed() const { return speed; }
    bool can_revive() const { return corpse; }
    bool get_use(const char *) const { return explosive; }
    const std::vector<item_pocket *> &get_all_standard_pockets() const { return pockets; }
};
'''
for namespace, header, cpp in [('baseline', old_h, old_cpp), ('candidate', new_h, new_cpp)]:
    declarations = '\n'.join(block(header, signature) + ';' for signature in
        ('struct item_reference', 'enum class special_item_type', 'class active_item_cache\n'))
    implementation = re.sub(r'^#include[^\n]*\n', '', cpp, flags=re.M)
    program += f'\nnamespace {namespace} {{\n{declarations}\n{implementation}\n}}\n'

program += r'''
using row = std::tuple<int, int, int, int, float, bool>;
template<class Refs> std::vector<row> describe(const Refs &refs) {
    std::vector<row> result;
    for (const auto &ref : refs) {
        if (!ref.item_ref) { result.emplace_back(-1, 0, 0, 0, 0, false); continue; }
        const float spoil = ref.spoil_multiplier();
        const bool sealed = ref.has_watertight_container();
        result.emplace_back(ref.item_ref->id, ref.location.x(), ref.location.y(),
                            ref.parent ? ref.parent->id : -1, spoil, sealed);
    }
    return result;
}
template<class A, class B> void equal(const A &a, const B &b) {
    trace_reads = true; pocket_reads.clear();
    const auto av = describe(a); const auto reads = pocket_reads;
    pocket_reads.clear();
    assert(av == describe(b)); assert(reads == pocket_reads);
    trace_reads = false; pocket_reads.clear();
}
static unsigned comparisons = 0;
void compare_cache(baseline::active_item_cache &a, candidate::active_item_cache &b) {
    assert(a.empty() == b.empty());
    equal(a.get(), b.get());
    equal(a.get_special(baseline::special_item_type::corpse),
          b.get_special(candidate::special_item_type::corpse));
    equal(a.get_special(baseline::special_item_type::explosive),
          b.get_special(candidate::special_item_type::explosive));
    equal(a.get_for_processing(), b.get_for_processing());
    equal(a.get(), b.get()); // rotation and expiry after processing
    ++comparisons;
}
void boundaries() {
    for (int speed : {1, 2, 600}) for (int count : {0, 1, 2, 599, 600, 601}) {
        baseline::active_item_cache a; candidate::active_item_cache b;
        std::vector<std::unique_ptr<item>> owners;
        for (int i = 0; i < count; ++i) {
            auto it = std::make_unique<item>(); it->id = i; it->speed = speed;
            a.add(*it, point_sm_ms{1, 2}); b.add(*it, point_sm_ms{1, 2});
            owners.push_back(std::move(it));
        }
        for (int tick = 0; tick < 7; ++tick) compare_cache(a, b);
        if (count) owners[count / 2].reset();
        equal(a.get_for_processing(), b.get_for_processing()); // expired quota path, before get()
        compare_cache(a, b);
        owners.clear(); equal(a.get_for_processing(), b.get_for_processing());
        compare_cache(a, b);
    }
}
void mutations() {
    baseline::active_item_cache a; candidate::active_item_cache b;
    item outer, inner, food, sibling, added;
    outer.id=10; inner.id=11; food.id=12; sibling.id=13; added.id=14;
    outer.speed=item::NO_PROCESSING; inner.speed=600;
    food.corpse=true; food.explosive=true; sibling.speed=2;
    item_pocket p, q, r;
    p.id=1; q.id=2; r.id=3; p.spoil=0.5f; q.spoil=0.25f;
    p.contents={&inner}; q.contents={&food}; r.contents={&sibling};
    outer.pockets={&p, &r}; inner.pockets={&q};
    assert(a.add(outer, point_rel_ms{2,3}) == b.add(outer, point_rel_ms{2,3}));
    compare_cache(a,b);
    // Preserve upstream recursive pocket accumulation, including sibling order.
    auto old_a=a.get_for_processing(); auto old_b=b.get_for_processing();
    a.add(added, point_rel_ms{4,5}); b.add(added, point_rel_ms{4,5});
    a.add(food, point_rel_ms{9,9}); b.add(food, point_rel_ms{9,9}); // duplicate
    compare_cache(a,b); equal(old_a,old_b); // nested snapshots remain independent
    p.spoil=0.125f; q.spoil=0.75f; r.sealed=true;
    equal(old_a,old_b); compare_cache(a,b); // state is LIVE, not memoized
    a.subtract_locations({1,1}); b.subtract_locations({1,1});
    a.rotate_locations(1,{12,12}); b.rotate_locations(1,{12,12});
    a.mirror({12,12},true); b.mirror({12,12},true);
    a.mirror({12,12},false); b.mirror({12,12},false);
    compare_cache(a,b); equal(old_a,old_b); // old snapshot positions don't change
    auto ca=a; auto cb=b; compare_cache(ca,cb); // copy the entire cache
    auto ma=std::move(ca); auto mb=std::move(cb); compare_cache(ma,mb);
    food.anchor = safe_reference_anchor(); // real safe-reference invalidation
    equal(old_a,old_b); compare_cache(a,b);
    a.add(food,point_rel_ms{7,8}); b.add(food,point_rel_ms{7,8});
    compare_cache(a,b); // reinsert at same address after expiry
    a={}; b={}; equal(old_a,old_b); compare_cache(a,b);
    // Retained refs must not own the item or its pockets.
    auto dying=std::make_unique<item>(); dying->id=20;
    a.add(*dying,point_rel_ms{}); b.add(*dying,point_rel_ms{});
    old_a=a.get(); old_b=b.get(); dying.reset(); equal(old_a,old_b);
    compare_cache(a,b);
}
void ancestry_lifetime() {
    item it, parent; item_pocket p, q; p.id=1; q.id=2;
    p.spoil=0.5f; q.spoil=0.25f;
    std::vector<const item_pocket *> chain{&p,&q,&p};
    baseline::item_reference a{{1,2},it.get_safe_reference(),&parent,chain};
    candidate::item_reference b{{1,2},it.get_safe_reference(),&parent,chain};
    chain.clear(); // construction takes a snapshot, never borrows caller storage
    std::vector<baseline::item_reference> aa{a,a};
    std::vector<candidate::item_reference> bb{b,b};
    equal(aa,bb); p.sealed=true; q.spoil=0.125f; equal(aa,bb);
    static_assert(std::is_nothrow_move_constructible_v<candidate::item_reference>);
    a={}; b={}; equal(aa,bb);
    auto moved_a=std::move(aa); auto moved_b=std::move(bb); equal(moved_a,moved_b);
    candidate::item_reference empty;
    assert(empty.spoil_multiplier()==1.0f && !empty.has_watertight_container());
    // An allocation failure during chain construction must leave caller state usable.
    chain.push_back(&p); bool caught=false; fail_new=true;
    try { candidate::item_reference failed{{},it.get_safe_reference(),nullptr,chain}; }
    catch(const std::bad_alloc &) { caught=true; }
    fail_new=false; assert(caught);
    candidate::item_reference retried{{},it.get_safe_reference(),nullptr,chain};
    assert(retried.spoil_multiplier()==p.spoil);
    // Copying a nonempty chain must not allocate (including while allocations fail).
    fail_new=true; auto copy=retried; fail_new=false;
    assert(copy.spoil_multiplier()==p.spoil);
}
struct result { size_t alloc, bytes; unsigned long long checksum; double ms; };
template<class Cache> result measure(Cache &cache, int repeats) {
    allocations=bytes=0; unsigned long long sum=0;
    const auto begin=std::chrono::steady_clock::now(); counting=true;
    for (int i=0;i<repeats;++i) {
        auto refs=cache.get_for_processing();
        for (const auto &ref:refs) {
            assert(ref.item_ref);
            sum=sum*33+ref.item_ref->id+static_cast<unsigned>(ref.spoil_multiplier()*1024);
        }
    }
    counting=false;
    return {allocations,bytes,sum,
        std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count()};
}
template<class Cache> result register_items(Cache &cache,
        const std::vector<std::unique_ptr<item>> &owners,
        const std::vector<const item_pocket *> &chain) {
    allocations=bytes=0; counting=true;
    for (const auto &it:owners) cache.add(*it,point_rel_ms{},nullptr,chain);
    counting=false;
    return {allocations,bytes,0,0};
}
void benchmark() {
    std::printf("sizeof item_reference: %zu -> %zu\n",sizeof(baseline::item_reference),sizeof(candidate::item_reference));
    for (int count : {0,32,512,2048}) for (int depth : {0,1,4}) for (int speed : {1,600}) {
        baseline::active_item_cache a; candidate::active_item_cache b;
        item_pocket pocket; pocket.spoil=0.5f;
        std::vector<const item_pocket *> chain(depth,&pocket);
        std::vector<std::unique_ptr<item>> owners;
        for (int i=0;i<count;++i) {
            auto it=std::make_unique<item>(); it->id=i+1; it->speed=speed;
            owners.push_back(std::move(it));
        }
        const auto add_a=register_items(a,owners,chain), add_b=register_items(b,owners,chain);
        std::printf("registration N=%d depth=%d speed=%d allocations=%zu->%zu cumulative_bytes=%zu->%zu\n",
                    count,depth,speed,add_a.alloc,add_b.alloc,add_a.bytes,add_b.bytes);
        auto before=measure(a,512), after=measure(b,512);
        assert(before.checksum==after.checksum);
        assert(after.alloc==static_cast<size_t>(count ? 512 : 0));
        assert(after.alloc<=before.alloc && after.bytes<=before.bytes);
        std::printf("N=%d depth=%d speed=%d iterations=512 allocations=%zu->%zu cumulative_bytes=%zu->%zu ms=%.3f->%.3f checksum=%llu\n",
                    count,depth,speed,before.alloc,after.alloc,before.bytes,after.bytes,before.ms,after.ms,after.checksum);
    }
}
int main() {
    boundaries(); mutations(); ancestry_lifetime();
#ifndef CATA_ITEM_SANITIZER
    benchmark();
#endif
    std::printf("PASS: %u differential cache checkpoints, live ancestry, lifetime, failure and allocation checks\n",comparisons);
}
'''
program_path = out / ('test-' + backend + '.cpp')
program_path.write_text(program)


def run_guarded(command):
    """Linux process-group RSS monitor; do not impose virtual-address limits on ASan."""
    print('+', shlex.join(map(str, command)), flush=True)
    proc = subprocess.Popen(list(map(str, command)), start_new_session=True)
    started, peak = time.monotonic(), 0
    try:
        while proc.poll() is None:
            rss = 0
            for path in Path('/proc').glob('[0-9]*/stat'):
                try:
                    fields = path.read_text().rsplit(')', 1)[1].split()
                    if int(fields[2]) == proc.pid:
                        rss += int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
                except (OSError, ValueError, IndexError):
                    pass
            peak = max(peak, rss)
            mem = Path('/proc/meminfo').read_text()
            available = int(re.search(r'MemAvailable:\s+(\d+)', mem)[1]) * 1024
            if rss > 620 * 1024**2 or available < 150 * 1024**2 or time.monotonic()-started > 180:
                raise RuntimeError(f'resource guard stopped command: RSS={rss}, available={available}')
            time.sleep(0.1)
        assert proc.returncode == 0, f'command exited {proc.returncode}'
    finally:
        # Also clean descendants if the parent exits unexpectedly.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        print(f'group peak sampled RSS: {peak / 1024**2:.1f} MiB', flush=True)


compiler = shlex.split(os.environ.get('CDDA_EMXX', 'em++') if backend == 'wasm'
                       else os.environ.get('CXX', 'g++'))
flags = ['-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror']
if backend == 'sanitized':
    # ASan quarantines freed blocks; millions of benchmark allocations are a
    # memory stress test of ASan, not of this cache. Keep all semantic/lifetime
    # cases here; allocation throughput is measured separately on native/Wasm.
    flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g',
              '-DCATA_ITEM_SANITIZER']
if backend == 'wasm':
    flags += ['-fwasm-exceptions', '-sENVIRONMENT=node', '-sALLOW_MEMORY_GROWTH=1',
              '-sINITIAL_MEMORY=33554432', '-sMAXIMUM_MEMORY=134217728']
exe = out / ('test-' + backend + ('.js' if backend == 'wasm' else ''))
run_guarded(compiler + flags + ['-I' + str(source / 'src'), program_path,
             source / 'src/safe_reference.cpp', '-o', exe])
run_guarded((['node'] if backend == 'wasm' else []) + [exe])
