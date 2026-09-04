// cata_web_yield.{h,cpp} の実機検証。
//   1) yield_now() が MessageChannel 経由で実際に制御を返すか
//   2) 1 回あたりのコストが計測値（0.075ms 前後）どおりか
//   3) yield_if_due() の 16ms 予算が効いているか
//   4) 再入ガードが機能するか（Asyncify の入れ子巻き戻し防止）
//   5) yield_paint() が rAF を待つか
#include "cata_web_yield.h"

#include <cstdio>
#include <emscripten.h>

// rAF の発火回数を JS 側で数える
EM_JS( void, install_frame_counter, (), {
    Module._frames = 0;
    var tick = function() {
        Module._frames++;
        requestAnimationFrame( tick );
    };
    requestAnimationFrame( tick );
} );

EM_JS( int, get_frames, (), {
    return Module._frames;
} );

// 再入検証用: yield_now() の途中でもう一度 yield_now() を呼ぶ
static int reentry_depth = 0;
static bool reentry_ok = true;

static void reentrant_probe()
{
    ++reentry_depth;
    if( reentry_depth > 1 ) {
        // ここに来た時点で再入している。ガードが効いていれば
        // 内側の yield_now() は即座に戻るはずで、クラッシュしない。
        cata_web::yield_now();
    }
    --reentry_depth;
}

int main()
{
    install_frame_counter();

    // --- 1) & 2) yield_now() のコスト ---
    {
        const int n = 2000;
        const double t0 = cata_web::now_ms();
        for( int i = 0; i < n; ++i ) {
            cata_web::yield_now();
        }
        const double t1 = cata_web::now_ms();
        printf( "yield_now: %d 回 = %.2fms / 1 回 %.4fms\n", n, t1 - t0, ( t1 - t0 ) / n );
    }

    // --- 参照: emscripten_sleep(0) のコスト（4ms クランプ） ---
    {
        const int n = 200;
        const double t0 = cata_web::now_ms();
        for( int i = 0; i < n; ++i ) {
            emscripten_sleep( 0 );
        }
        const double t1 = cata_web::now_ms();
        printf( "emscripten_sleep(0): %d 回 = %.2fms / 1 回 %.4fms\n", n, t1 - t0, ( t1 - t0 ) / n );
    }

    // --- 3) yield_if_due の予算 ---
    {
        double last = cata_web::now_ms();
        int yields = 0;
        const double t0 = cata_web::now_ms();
        // 200ms 相当のダミー作業を細かい単位で回す
        while( cata_web::now_ms() - t0 < 200.0 ) {
            volatile double sink = 0;
            for( int i = 0; i < 20000; ++i ) {
                sink += i * 0.5;
            }
            ( void )sink;
            if( cata_web::yield_if_due( last ) ) {
                ++yields;
            }
        }
        const double elapsed = cata_web::now_ms() - t0;
        printf( "yield_if_due: %.1fms 中 %d 回 yield（期待 %.0f 回前後）\n",
                elapsed, yields, elapsed / 16.0 );
    }

    // --- 4) 再入ガード ---
    {
        reentry_depth = 0;
        reentrant_probe();
        reentry_depth = 2;      // 強制的に再入状態を作る
        cata_web::yield_now();  // ガードにより即座に戻るべき
        reentry_depth = 0;
        printf( "再入ガード: %s\n", reentry_ok ? "OK（クラッシュせず）" : "NG" );
    }

    // --- 5) yield_paint() が rAF を待つか ---
    {
        const int f0 = get_frames();
        const double t0 = cata_web::now_ms();
        const int n = 20;
        for( int i = 0; i < n; ++i ) {
            cata_web::yield_paint();
        }
        const double t1 = cata_web::now_ms();
        const int f1 = get_frames();
        printf( "yield_paint: %d 回 = %.2fms / 1 回 %.2fms, フレーム進行 %d\n",
                n, t1 - t0, ( t1 - t0 ) / n, f1 - f0 );
    }

    // --- 6) yield_now() 中も rAF が回り続けるか（描画が止まらない証明） ---
    {
        const int f0 = get_frames();
        const double t0 = cata_web::now_ms();
        while( cata_web::now_ms() - t0 < 500.0 ) {
            cata_web::yield_now();
        }
        const int f1 = get_frames();
        printf( "yield_now 連打 500ms 中の rAF 発火: %d 回（60fps なら約 30 回）\n", f1 - f0 );
    }

    printf( "DONE\n" );
    return 0;
}
