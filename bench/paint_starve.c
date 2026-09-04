#include <emscripten.h>
#include <stdio.h>

// Does a hot loop that yields only through MessageChannel still let the
// browser paint?  If rAF callbacks keep firing at ~60Hz while we spin,
// the fast yield is safe to use as the *only* cooperative primitive.
// If they starve, a periodic rAF yield must be interleaved.

EM_JS( void, install_raf_counter, (), {
    Module._rafCount = 0;
    Module._rafLastGap = 0;
    Module._rafMaxGap = 0;
    var last = performance.now();
    function tick() {
        var now = performance.now();
        var gap = now - last;
        last = now;
        Module._rafCount++;
        Module._rafLastGap = gap;
        if( gap > Module._rafMaxGap ) { Module._rafMaxGap = gap; }
        requestAnimationFrame( tick );
    }
    requestAnimationFrame( tick );
} );

EM_JS( int, raf_count, (), { return Module._rafCount; } );
EM_JS( double, raf_max_gap, (), { return Module._rafMaxGap; } );
EM_JS( void, raf_reset, (), { Module._rafCount = 0; Module._rafMaxGap = 0; } );

EM_ASYNC_JS( void, yield_fast, (), {
    if( !Module._ch ) {
        var ch = new MessageChannel();
        var q = [];
        ch.port1.onmessage = function() { var p = q; q = []; for( var i = 0; i < p.length; ++i ) { p[i](); } };
        Module._ch = { push: function( r ) { q.push( r ); ch.port2.postMessage( 0 ); } };
    }
    await new Promise( function( r ) { Module._ch.push( r ); } );
} );

EM_ASYNC_JS( void, yield_sched, (), {
    if( typeof scheduler !== 'undefined' && scheduler.yield ) { await scheduler.yield(); }
    else { await new Promise( function( r ) { setTimeout( r, 0 ); } ); }
} );

static volatile double sink = 0;
static void burn( double ms )
{
    double t0 = emscripten_get_now();
    while( emscripten_get_now() - t0 < ms ) { sink += 1.0; }
}

// Simulate: N game turns, each costing `turn_ms` of CPU, yielding every
// `yield_every_ms` of accumulated work, with the given primitive.
static void run( const char *name, int kind, double turn_ms, double yield_every_ms, double total_ms )
{
    raf_reset();
    double t0 = emscripten_get_now();
    double last_yield = t0;
    int turns = 0;
    while( emscripten_get_now() - t0 < total_ms ) {
        burn( turn_ms );
        ++turns;
        const double now = emscripten_get_now();
        if( now - last_yield >= yield_every_ms ) {
            if( kind == 0 ) { emscripten_sleep( 0 ); }
            else if( kind == 1 ) { yield_fast(); }
            else { yield_sched(); }
            last_yield = emscripten_get_now();
        }
    }
    double elapsed = emscripten_get_now() - t0;
    printf( "RESULT prim=%s yield_every_ms=%.0f turns=%d elapsed_ms=%.0f turns_per_sec=%.1f raf_frames=%d raf_max_gap_ms=%.1f\n",
            name, yield_every_ms, turns, elapsed, turns * 1000.0 / elapsed, raf_count(), raf_max_gap() );
}

int main( void )
{
    install_raf_counter();
    emscripten_sleep( 100 );
    const double turn_ms = 3.0;   // a plausible per-turn CPU cost on a 4GB Chromebook
    const double total = 2000.0;
    run( "sleep0",   0, turn_ms, 250.0, total );  // current CDDA-broser behavior
    run( "sleep0",   0, turn_ms, 16.0,  total );  // what frequent yielding would cost today
    run( "msgchan",  1, turn_ms, 16.0,  total );
    run( "msgchan",  1, turn_ms, 4.0,   total );
    run( "schedyld", 2, turn_ms, 16.0,  total );
    run( "schedyld", 2, turn_ms, 4.0,   total );
    printf( "RESULT sink=%.0f\n", sink );
    printf( "DONE\n" );
    return 0;
}
