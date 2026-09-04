// 実測: セーブが 250ms のデバウンス窓を超えて続く場合に、IDBFS 同期が
// セーブ途中で起動してしまうか。mapbuffer::save() は zzip 無効時に
// quad ごとに write_to_file + assure_dir_exist を呼ぶので、write 1件
// あたりの実コストはもっと高い。ここでは JSON 直列化相当の CPU 負荷を
// 混ぜて、現実的なセーブ時間（数秒）を再現する。
#include <emscripten.h>
#include <stdio.h>
#include <string.h>

EM_ASYNC_JS( void, mount_idb, (), {
    FS.mkdirTree( '/persist' );
    FS.mount( IDBFS, {}, '/persist' );
    await new Promise( function( r, j ) { FS.syncfs( true, function( e ) { e ? j( e ) : r(); } ); } );
    var needs = false, inflight = false, timer = null;
    window.__syncCount = 0; window.__syncTotalMs = 0; window.__syncLog = [];
    function doSync() {
        if( inflight || !needs ) { return; }
        needs = false; inflight = true;
        var t0 = performance.now();
        FS.syncfs( false, function() {
            inflight = false;
            var d = performance.now() - t0;
            window.__syncCount++; window.__syncTotalMs += d;
            window.__syncLog.push( Math.round( d ) );
            if( needs ) { window.setFsNeedsSync(); }
        } );
    }
    window.setFsNeedsSync = function() {
        needs = true;
        if( inflight || timer !== null ) { return; }
        timer = setTimeout( function() { timer = null; requestAnimationFrame( doSync ); }, 250 );
    };
} );

EM_JS( void, mark_dirty, (), { window.setFsNeedsSync(); } );
EM_JS( int, sync_count, (), { return window.__syncCount; } );
EM_JS( double, sync_total, (), { return window.__syncTotalMs; } );
EM_JS( double, now_ms, (), { return performance.now(); } );
EM_JS( void, reset_stats, (), { window.__syncCount = 0; window.__syncTotalMs = 0; window.__syncLog = []; } );
EM_JS( void, dump_log, (), { console.log( "RESULT sync_durations=" + JSON.stringify( window.__syncLog ) ); } );

static char payload[8192];
static volatile double sink = 0;

// 1 quad あたりの JSON 直列化 CPU 負荷を模擬（実測で約1ms相当）
static void cpu_work( void )
{
    double a = 1.0;
    for( int k = 0; k < 200000; ++k ) {
        a += k * 0.5;
    }
    sink = a;
}

static void seed( int n )
{
    for( int i = 0; i < n; ++i ) {
        char path[128];
        snprintf( path, sizeof( path ), "/persist/seed_%d.map", i );
        FILE *f = fopen( path, "wb" );
        fwrite( payload, 1, sizeof( payload ) - 1, f );
        fclose( f );
        mark_dirty();
    }
}

// quads: 保存する quad 数, yield_ms: yield 間隔, sleep0: 0=MessageChannel相当なし
static double simulate_save( int quads, int yield_ms, int base )
{
    reset_stats();
    double t0 = now_ms();
    double last = t0;
    for( int i = 0; i < quads; ++i ) {
        cpu_work();
        char path[128];
        snprintf( path, sizeof( path ), "/persist/sv%d_%d.map", base, i );
        FILE *f = fopen( path, "wb" );
        fwrite( payload, 1, sizeof( payload ) - 1, f );
        fclose( f );
        mark_dirty();
        double n = now_ms();
        if( n - last >= yield_ms ) {
            emscripten_sleep( 0 );
            last = now_ms();
        }
    }
    return now_ms() - t0;
}

int main( void )
{
    memset( payload, 'x', sizeof( payload ) - 1 );
    mount_idb();
    printf( "MOUNTED\n" );
    seed( 1500 );
    emscripten_sleep( 2500 );
    printf( "RESULT seed syncs=%d total=%.0fms\n", sync_count(), sync_total() );

    // 現状: 250ms ごとに yield、1200 quad（実プレイのセーブ規模）
    double t1 = simulate_save( 1200, 250, 1 );
    int c1 = sync_count(); double s1 = sync_total();
    printf( "RESULT case1_yield250 save_wall=%.0fms mid_save_syncs=%d mid_sync_ms=%.0f\n", t1, c1, s1 );
    dump_log();
    emscripten_sleep( 3000 );
    printf( "RESULT case1_after_total_syncs=%d total_sync_ms=%.0f\n", sync_count(), sync_total() );

    printf( "DONE\n" );
    return 0;
}
