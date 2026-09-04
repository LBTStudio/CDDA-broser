// ロード経路の実測。init.cpp:195 の pump_events() は「JSONオブジェクトごと」に
// 呼ばれる（コア data/json だけで約60142個）。pump_events 側のスロットルが
// 効いていても、emscripten_get_now() の呼び出しと分岐が60142回走る。
// さらに 250ms スロットルは「50ms 相当の描画停止」を意味し、ロード画面の
// プログレス表示が止まる。各構成のコストを比較する。
#include <emscripten.h>
#include <stdio.h>

EM_JS( double, now_ms, (), { return performance.now(); } );

// MessageChannel ベースの高速 yield（FACTS.md F-01 で採用した実装）
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

// 1 JSONオブジェクトのパース相当（軽い CPU 作業）
static void parse_one( void )
{
    double a = 1.0;
    for( int k = 0; k < 12000; ++k ) {
        a += k * 0.25;
    }
    sink = a;
}

#define N_OBJECTS 60142

// mode 0: yieldなし（基準）
// mode 1: emscripten_get_now() のスロットル判定のみ（実際にyieldしない）
// mode 2: 250ms スロットル + emscripten_sleep(0)   ← 現状
// mode 3: 16ms スロットル + MessageChannel          ← 提案
static double run( int mode, int *out_yields )
{
    double t0 = now_ms();
    double last = t0;
    int yields = 0;
    for( int i = 0; i < N_OBJECTS; ++i ) {
        parse_one();
        if( mode == 0 ) {
            continue;
        }
        double n = now_ms();
        if( mode == 1 ) {
            if( n - last >= 250.0 ) { last = n; }
            continue;
        }
        double budget = ( mode == 2 ) ? 250.0 : 16.0;
        if( n - last >= budget ) {
            if( mode == 2 ) {
                emscripten_sleep( 0 );
            } else {
                yield_msgchannel();
            }
            last = now_ms();
            yields++;
        }
    }
    *out_yields = yields;
    return now_ms() - t0;
}

int main( void )
{
    int y;
    double t;
    t = run( 0, &y ); printf( "RESULT mode0_no_yield=%.0fms yields=%d\n", t, y );
    t = run( 1, &y ); printf( "RESULT mode1_throttle_check_only=%.0fms yields=%d\n", t, y );
    t = run( 2, &y ); printf( "RESULT mode2_sleep0_250ms=%.0fms yields=%d\n", t, y );
    t = run( 3, &y ); printf( "RESULT mode3_msgchannel_16ms=%.0fms yields=%d\n", t, y );
    printf( "DONE\n" );
    return 0;
}
