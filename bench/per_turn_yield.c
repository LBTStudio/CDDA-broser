#include <emscripten.h>
#include <stdio.h>

// The realistic worst case in CDDA-broser today: a long activity (pulping,
// crafting, waiting) runs many cheap turns, and each turn passes through
// ui_manager::redraw_invalidated + input_manager::pump_events, i.e. it can
// hit a yield *every turn*.  Compare the wall-clock cost of 400 such turns.

EM_ASYNC_JS( void, yield_fast, (), {
    if( !Module._ch ) {
        var ch = new MessageChannel();
        var q = [];
        ch.port1.onmessage = function() { var p = q; q = []; for( var i = 0; i < p.length; ++i ) { p[i](); } };
        Module._ch = { push: function( r ) { q.push( r ); ch.port2.postMessage( 0 ); } };
    }
    await new Promise( function( r ) { Module._ch.push( r ); } );
} );

static volatile double sink = 0;
static void burn( double ms )
{
    double t0 = emscripten_get_now();
    while( emscripten_get_now() - t0 < ms ) { sink += 1.0; }
}

static void run( const char *name, int kind, int turns, double turn_ms )
{
    double t0 = emscripten_get_now();
    for( int i = 0; i < turns; ++i ) {
        burn( turn_ms );
        if( kind == 0 ) { emscripten_sleep( 0 ); }
        else if( kind == 1 ) { emscripten_sleep( 1 ); }
        else { yield_fast(); }
    }
    double e = emscripten_get_now() - t0;
    printf( "RESULT scenario=yield_every_turn prim=%s turns=%d turn_cpu_ms=%.1f total_ms=%.0f per_turn_ms=%.3f\n",
            name, turns, turn_ms, e, e / turns );
}

int main( void )
{
    // 400 turns is roughly one corpse-pulping session.
    run( "sleep0",  0, 400, 1.0 );
    run( "sleep1",  1, 400, 1.0 );
    run( "msgchan", 2, 400, 1.0 );
    printf( "RESULT sink=%.0f\n", sink );
    printf( "DONE\n" );
    return 0;
}
