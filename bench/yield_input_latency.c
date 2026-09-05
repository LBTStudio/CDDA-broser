// bench/yield_input_latency.c
//
// ==================================================================
// 何を測るのか
// ==================================================================
// 「行動中（粉砕・読書・製作）に yield を挟んでいる最中、
//   プレイヤーの入力はどれくらい待たされるのか」を測る。
//
// これは判断基準 B「入力応答 100ms 以内」に直結する。
// bench/yield_kinds.c は「yield 1 回あたりの所要時間」を測ったが、
// それは【スループット】の指標であって【応答性】の指標ではない。
//
// yield が速いことと、入力が速く届くことは別問題である。
// 例えば MessageChannel は 1 回 0.07ms と非常に速いが、
// もし「MessageChannel のタスクが入力イベントより先に処理される」
// 性質があるなら、yield を高頻度で回している間は入力が
// 後回しにされ続けて、応答が悪化しうる。
//
// MDN の Scheduler.yield の記述によれば、scheduler.yield() の継続は
// 「同一優先度の postTask より前、上位優先度の postTask より後」に
// 入る昇格キューに積まれる。つまりブラウザのスケジューラが
// 入力との優先関係を理解した上で並べてくれる。
// 一方 MessageChannel は単なるマクロタスクで、優先度の概念がない。
//
// この差が実際の入力遅延に出るのかを測る。
//
// ==================================================================
// 測り方
// ==================================================================
// (1) JS 側で、ある時刻に「入力イベント相当」をキューに積む。
//     実際のキー入力を合成するのは headless では不安定なので、
//     入力と同じ優先度で処理される経路を使う:
//       ・postMessage による message イベント（MessageChannel と同格）
//       ・scheduler.postTask( 'user-blocking' )（入力と同格の優先度）
//     後者が「入力イベント」の代理として妥当である。
//     Chrome の scheduling API では user-blocking が
//     「ユーザーがブロックされている＝入力応答」の優先度と定義される。
//
// (2) C 側は yield を挟みながらループを回し続ける。
//
// (3) (1) を積んでから実際に呼ばれるまでの経過時間を測る。
//     これが「行動中にキーを押したとき、何ms後に処理が始まるか」
//     に相当する。
//
// 100ms を超えるなら基準 B に抵触する。
#include <emscripten.h>
#include <stdio.h>

// ------------------------------------------------------------------
// 各 yield 方式。cata_web_yield.cpp の実装と同一の形にする。
// ------------------------------------------------------------------
EM_ASYNC_JS( void, y_msgchannel, (), {
    if( !Module._bChan ) {
        var ch = new MessageChannel();
        var pending = [];
        ch.port1.onmessage = function() {
            var q = pending;
            pending = [];
            for( var i = 0; i < q.length; ++i ) { q[i](); }
        };
        Module._bChan = { push: function( r ) {
            pending.push( r );
            ch.port2.postMessage( 0 );
        } };
    }
    await new Promise( function( resolve ) { Module._bChan.push( resolve ); } );
} );

EM_ASYNC_JS( void, y_scheduler, (), {
    await scheduler.yield();
} );

EM_ASYNC_JS( void, y_sleep0, (), {
    await new Promise( function( resolve ) { setTimeout( resolve, 0 ); } );
} );

// ------------------------------------------------------------------
// 「入力相当」のタスクを積む。
// user-blocking は Chrome のスケジューリング仕様上、
// 入力応答と同じ最上位優先度である。
// ------------------------------------------------------------------
EM_ASYNC_JS( void, probe_arm, (), {
    Module._probeArmedAt = performance.now();
    Module._probeFiredAt = -1;
    scheduler.postTask( function() {
        Module._probeFiredAt = performance.now();
    }, { priority: 'user-blocking' } );
} );

EM_JS( double, probe_latency_ms, (), {
    if( Module._probeFiredAt < 0 ) { return -1; }
    return Module._probeFiredAt - Module._probeArmedAt;
} );

EM_JS( int, has_scheduler, (), {
    return ( typeof scheduler !== 'undefined' && scheduler
             && typeof scheduler.yield === 'function'
             && typeof scheduler.postTask === 'function' ) ? 1 : 0;
} );

// ------------------------------------------------------------------
// 1 ターン相当の計算負荷。
// 実測（F-20）で 1 ターン約 0.86ms だったので、それに近い量にする。
// ------------------------------------------------------------------
static double sink = 0.0;
static void turn_work( void )
{
    for( int i = 0; i < 20000; ++i ) {
        sink += ( double )i * 1.000001;
    }
}

// ------------------------------------------------------------------
// yield を挟みながらループし、途中で入力相当を積んで遅延を測る。
// ------------------------------------------------------------------
static double measure( int kind, int iters )
{
    // 十分回してからプローブを積む（スケジューラを定常状態にする）
    for( int i = 0; i < 20; ++i ) {
        turn_work();
        switch( kind ) {
            case 0: y_sleep0(); break;
            case 1: y_msgchannel(); break;
            case 2: y_scheduler(); break;
        }
    }

    probe_arm();

    // プローブを積んだ後もループを続ける。
    // ここで入力が割り込めるかどうかが測定対象である。
    for( int i = 0; i < iters; ++i ) {
        turn_work();
        switch( kind ) {
            case 0: y_sleep0(); break;
            case 1: y_msgchannel(); break;
            case 2: y_scheduler(); break;
        }
        if( probe_latency_ms() >= 0.0 ) {
            // 割り込めた。その時点の遅延を返す。
            return probe_latency_ms();
        }
    }
    // iters 回まわっても割り込めなかった
    return -1.0;
}

int main( void )
{
    printf( "RESULT scheduler_api_available=%d\n", has_scheduler() );
    if( !has_scheduler() ) {
        printf( "ERR scheduler API 無し。この測定は Chrome 129+ が必要\n" );
        printf( "DONE\n" );
        return 0;
    }

    const char *names[] = { "setTimeout(0)", "MessageChannel", "scheduler.yield" };

    // 各方式を 3 回測って中央値的な傾向を見る
    for( int kind = 0; kind < 3; ++kind ) {
        for( int rep = 0; rep < 3; ++rep ) {
            double lat = measure( kind, 2000 );
            if( lat < 0.0 ) {
                printf( "RESULT kind=%s rep=%d input_latency_ms=NEVER(2000回中に割り込めず)\n",
                        names[kind], rep );
            } else {
                printf( "RESULT kind=%s rep=%d input_latency_ms=%.3f\n",
                        names[kind], rep, lat );
            }
        }
    }

    printf( "RESULT sink=%.0f\n", sink );
    printf( "DONE\n" );
    return 0;
}
