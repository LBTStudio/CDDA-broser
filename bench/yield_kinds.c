#include <emscripten.h>
#include <stdio.h>

// Compare yield primitives available to an ASYNCIFY build.
// All of them unwind/rewind the Asyncify stack identically; the only
// difference is how the continuation is scheduled by the browser.

// 1. Stock emscripten_sleep(0)  -> setTimeout(0) -> clamped to ~4ms
// 2. MessageChannel postMessage -> a real macrotask, NOT clamped
// 3. scheduler.yield()          -> Chrome 129+, priority-aware, not clamped
// 4. requestAnimationFrame      -> paced to the display (~16.7ms), paints

EM_ASYNC_JS( void, yield_msgchannel, (), {
    if( !Module._cddaYieldChannel ) {
        var ch = new MessageChannel();
        var pending = [];
        ch.port1.onmessage = function() {
            var q = pending;
            pending = [];
            for( var i = 0; i < q.length; ++i ) { q[i](); }
        };
        Module._cddaYieldChannel = { ch: ch, push: function( r ) {
            pending.push( r );
            ch.port2.postMessage( 0 );
        } };
    }
    await new Promise( function( resolve ) { Module._cddaYieldChannel.push( resolve ); } );
} );

EM_ASYNC_JS( void, yield_scheduler, (), {
    if( typeof scheduler !== 'undefined' && scheduler && typeof scheduler.yield === 'function' ) {
        await scheduler.yield();
    } else {
        Module._noSchedulerYield = 1;
        await new Promise( function( resolve ) { setTimeout( resolve, 0 ); } );
    }
} );

EM_ASYNC_JS( void, yield_raf, (), {
    await new Promise( function( resolve ) { requestAnimationFrame( function() { resolve(); } ); } );
} );

EM_ASYNC_JS( int, has_scheduler_yield, (), {
    return ( typeof scheduler !== 'undefined' && scheduler && typeof scheduler.yield === 'function' ) ? 1 : 0;
} );

static double sink = 0.0;

static void deep( int depth, int n, int kind )
{
    if( depth > 0 ) {
        volatile double pad[8];
        for( int i = 0; i < 8; ++i ) { pad[i] = depth + i; }
        deep( depth - 1, n, kind );
        for( int i = 0; i < 8; ++i ) { sink += pad[i]; }
        return;
    }
    for( int i = 0; i < n; ++i ) {
        switch( kind ) {
            case 0: emscripten_sleep( 0 ); break;
            case 1: yield_msgchannel(); break;
            case 2: yield_scheduler(); break;
            case 3: yield_raf(); break;
        }
    }
}

int main( void )
{
    const char *names[] = { "emscripten_sleep(0)", "MessageChannel", "scheduler.yield", "requestAnimationFrame" };
    printf( "RESULT scheduler_yield_available=%d\n", has_scheduler_yield() );
    for( int kind = 0; kind < 4; ++kind ) {
        int n = kind == 3 ? 60 : 300;
        for( int d = 0; d <= 1024; d = d ? d * 32 : 256 ) {
            double t0 = emscripten_get_now();
            deep( d, n, kind );
            double t1 = emscripten_get_now();
            printf( "RESULT kind=%s depth=%d n=%d per_yield_ms=%.4f\n",
                    names[kind], d, n, ( t1 - t0 ) / n );
            if( d == 0 ) { continue; }
        }
    }
    printf( "RESULT sink=%.0f\n", sink );
    printf( "DONE\n" );
    return 0;
}
