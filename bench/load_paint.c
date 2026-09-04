// ロード中の「プログレス表示がどれだけ止まるか」を実測する。
// ユーザーはプログレスバーを要求しているが、250ms スロットルでは
// バーが 250ms 以上更新されない = 実質フリーズに見える。
#include <emscripten.h>
#include <stdio.h>

EM_JS( double, now_ms, (), { return performance.now(); } );

EM_JS( void, install_raf, (), {
    Module._rafCount = 0;
    Module._rafMaxGap = 0;
    var last = performance.now();
    function tick() {
        var n = performance.now();
        var gap = n - last;
        if( gap > Module._rafMaxGap ) { Module._rafMaxGap = gap; }
        last = n;
        Module._rafCount++;
        requestAnimationFrame( tick );
    }
    requestAnimationFrame( tick );
} );
EM_JS( void, reset_raf, (), { Module._rafCount = 0; Module._rafMaxGap = 0; } );
EM_JS( int, raf_count, (), { return Module._rafCount; } );
EM_JS( double, raf_max_gap, (), { return Module._rafMaxGap; } );

EM_ASYNC_JS( void, yield_msgchannel, (), {
    if( !Module._cddaYieldChannel ) {
        var ch = new MessageChannel();
        var pending = [];
        ch.port1.onmessage = function() {
            var q = pending; pending = [];
            for( var i = 0; i < q.length; ++i ) { q[i](); }
        };
        Module._cddaYieldChannel = { ch: ch, push: function( r ) {
            pending.push( r ); ch.port2.postMessage( 0 );
        } };
    }
    await new Promise( function( resolve ) { Module._cddaYieldChannel.push( resolve ); } );
} );

static volatile double sink = 0;
static void parse_one( void )
{
    double a = 1.0;
    for( int k = 0; k < 12000; ++k ) { a += k * 0.25; }
    sink = a;
}

#define N_OBJECTS 60142

static void run( const char *label, int mode, double budget )
{
    reset_raf();
    double t0 = now_ms();
    double last = t0;
    int yields = 0;
    for( int i = 0; i < N_OBJECTS; ++i ) {
        parse_one();
        double n = now_ms();
        if( n - last >= budget ) {
            if( mode == 2 ) { emscripten_sleep( 0 ); } else { yield_msgchannel(); }
            last = now_ms();
            yields++;
        }
    }
    double wall = now_ms() - t0;
    printf( "RESULT %s wall=%.0fms yields=%d raf_frames=%d max_paint_gap=%.1fms\n",
            label, wall, yields, raf_count(), raf_max_gap() );
}

int main( void )
{
    install_raf();
    emscripten_sleep( 200 );
    run( "current_sleep0_250ms", 2, 250.0 );
    emscripten_sleep( 200 );
    run( "proposed_msgchan_16ms", 3, 16.0 );
    emscripten_sleep( 200 );
    run( "proposed_msgchan_8ms", 3, 8.0 );
    printf( "DONE\n" );
    return 0;
}
