// Mechanism probe, NOT CDDA gameplay or a 4GB-device performance guarantee.
// The previous probe had no suspending import at all: Asyncify could remove
// its instrumentation, so it could not establish that instrumentation is free.
// Build this SAME source/toolchain/options as Asyncify, JSPI and PROBE_SYNC.
// Compare checksums, binary sizes, warm CPU work and actual suspend/resume.
#include <emscripten.h>
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>

#ifndef PROBE_BACKEND
#define PROBE_BACKEND "unknown"
#endif

#ifdef PROBE_SYNC
static void suspend_probe() {}
#else
EM_ASYNC_JS( void, suspend_probe, (), {
    if( !Module.probeChannel ) {
        var channel = new MessageChannel();
        Module.probeChannel = channel;
        channel.port1.onmessage = function() {
            var resolve = Module.probeResolve;
            Module.probeResolve = null;
            resolve();
        };
    }
    await new Promise( function( resolve ) {
        Module.probeResolve = resolve;
        Module.probeChannel.port2.postMessage( 0 );
    } );
} );
#endif

static unsigned suspend_interval = 0;
static unsigned suspend_count = 0;
struct critter {
    explicit critter( unsigned id_ ) : id( id_ ) {}
    virtual ~critter() = default;
    virtual uint32_t act( uint32_t turn ) = 0;
    uint32_t hp = 100;
    unsigned id;
    void maybe_suspend( uint32_t turn ) {
        if( id == 0 && suspend_interval && turn % suspend_interval == 0 ) {
            ++suspend_count;
            suspend_probe();
        }
    }
};
struct zombie : critter {
    using critter::critter;
    uint32_t act( uint32_t turn ) override {
        maybe_suspend( turn );
        hp += ( turn % 7 ) - 3;
        return hp ^ turn;
    }
};
struct dog : critter {
    using critter::critter;
    uint32_t act( uint32_t turn ) override {
        maybe_suspend( turn );
        hp += ( turn % 5 ) - 2;
        return hp + ( turn >> 2 );
    }
};

static uint32_t grid[132][132];
struct result { double ms; uint32_t checksum; unsigned suspends; };
static result run( unsigned interval )
{
    std::vector<std::unique_ptr<critter>> pop;
    for( unsigned i = 0; i < 400; ++i ) {
        if( i % 2 ) { pop.push_back( std::make_unique<zombie>( i ) ); }
        else { pop.push_back( std::make_unique<dog>( i ) ); }
    }
    std::memset( grid, 0, sizeof( grid ) );
    suspend_interval = interval;
    suspend_count = 0;
    uint32_t checksum = 0; // unsigned wrap is defined, unlike the old signed sum
    const double start = emscripten_get_now();
    for( uint32_t turn = 1; turn <= 800; ++turn ) {
        for( auto &c : pop ) { checksum += c->act( turn ); }
        for( int y = 1; y < 131; ++y ) {
            for( int x = 1; x < 131; ++x ) {
                grid[y][x] = ( grid[y - 1][x] + grid[y][x - 1] + turn ) & 0xFFFF;
            }
        }
        checksum ^= grid[130][130];
    }
    return { emscripten_get_now() - start, checksum, suspend_count };
}

int main()
{
    // C++ exceptions/RAII must still work across real suspension. Disabling
    // exceptions to make JSPI run would not preserve CDDA semantics.
    bool destroyed = false;
    struct guard {
        bool &destroyed;
        ~guard() { destroyed = true; }
    };
    try {
        guard live{ destroyed };
        suspend_probe();
        throw 7;
    } catch( int value ) {
        assert( value == 7 && destroyed );
    }
    puts( "PASS suspension followed by exception and RAII cleanup" );
    for( unsigned interval : {0u, 16u, 1u} ) {
        const result warm = run( interval );
        std::vector<double> samples;
        for( int repeat = 0; repeat < 7; ++repeat ) {
            const result measured = run( interval );
            assert( measured.checksum == warm.checksum );
            assert( measured.suspends == ( interval ? 800 / interval : 0 ) );
            samples.push_back( measured.ms );
        }
        std::sort( samples.begin(), samples.end() );
        printf( "RESULT backend=%s interval=%u turns=800 checksum=%u suspends=%u median_ms=%.3f min_ms=%.3f max_ms=%.3f\n",
                PROBE_BACKEND, interval, warm.checksum, warm.suspends,
                samples[3], samples.front(), samples.back() );
    }
    puts( "DONE mechanism-only; not game speed, memory, or Chromebook results" );
    return 0;
}
