// IDBFS 同期コストと MEMFS 書き込みコストの実測。
// mapbuffer::save() は数百ファイルを書き、filesystem.cpp は書き込みごとに
// setFsNeedsSync() を呼ぶ。同期1回あたりの実コストを測る。
#include <emscripten.h>
#include <stdio.h>
#include <string.h>

EM_ASYNC_JS( void, mount_idb, (), {
    FS.mkdirTree( '/persist' );
    FS.mount( IDBFS, {}, '/persist' );
    await new Promise( function( r, j ) { FS.syncfs( true, function( e ) { e ? j( e ) : r(); } ); } );
} );

EM_ASYNC_JS( double, syncfs_once, (), {
    var t0 = performance.now();
    await new Promise( function( r ) { FS.syncfs( false, function() { r(); } ); } );
    return performance.now() - t0;
} );

EM_JS( double, now_ms, (), { return performance.now(); } );

static char payload[4096];

int main( void )
{
    memset( payload, 'x', sizeof( payload ) - 1 );
    mount_idb();
    printf( "MOUNTED\n" );

    // 1) MEMFS への純粋な書き込みコスト（submap quad 相当 4KB × 200）
    double t0 = now_ms();
    for( int i = 0; i < 200; ++i ) {
        char path[128];
        snprintf( path, sizeof( path ), "/persist/quad_%d.map", i );
        FILE *f = fopen( path, "wb" );
        fwrite( payload, 1, sizeof( payload ) - 1, f );
        fclose( f );
    }
    double t_write = now_ms() - t0;
    printf( "RESULT memfs_write_200x4KB=%.1fms per_file=%.3fms\n", t_write, t_write / 200.0 );

    // 2) 全変更をまとめて1回同期（デバウンス後の理想形）
    double t_sync_batch = syncfs_once();
    printf( "RESULT syncfs_batched_200files=%.1fms\n", t_sync_batch );

    // 3) 変更なしの空同期コスト（デバウンスが漏れた場合の無駄）
    double t_empty = 0.0;
    for( int i = 0; i < 5; ++i ) {
        t_empty += syncfs_once();
    }
    printf( "RESULT syncfs_noop_avg=%.2fms\n", t_empty / 5.0 );

    // 4) 書き込み1件ごとに同期した場合（デバウンスなしの最悪ケース）
    t0 = now_ms();
    for( int i = 0; i < 20; ++i ) {
        char path[128];
        snprintf( path, sizeof( path ), "/persist/sync_%d.map", i );
        FILE *f = fopen( path, "wb" );
        fwrite( payload, 1, sizeof( payload ) - 1, f );
        fclose( f );
        syncfs_once();
    }
    double t_persync = now_ms() - t0;
    printf( "RESULT syncfs_per_write_20files=%.1fms per_file=%.2fms\n", t_persync, t_persync / 20.0 );

    printf( "DONE\n" );
    return 0;
}
