// IDBFS syncfs のコストが「変更量」ではなく「マウント全体のファイル数」に
// 比例することを検証する。長時間プレイしたワールド（セーブツリーが数千
// ファイル）でセーブが重くなるかどうかを決定する事実。
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

static char payload[4096];

static void write_files( int base, int count )
{
    for( int i = 0; i < count; ++i ) {
        char path[128];
        snprintf( path, sizeof( path ), "/persist/f_%d.map", base + i );
        FILE *f = fopen( path, "wb" );
        fwrite( payload, 1, sizeof( payload ) - 1, f );
        fclose( f );
    }
}

int main( void )
{
    memset( payload, 'x', sizeof( payload ) - 1 );
    mount_idb();
    printf( "MOUNTED\n" );

    int total = 0;
    const int steps[] = { 100, 400, 500, 1000, 2000 };
    for( int s = 0; s < 5; ++s ) {
        write_files( total, steps[s] );
        total += steps[s];
        // 変更を反映（この同期は「変更あり」）
        double dirty = syncfs_once();
        // 直後の同期は「変更ゼロ」なので純粋な走査コスト
        double noop1 = syncfs_once();
        double noop2 = syncfs_once();
        printf( "RESULT total_files=%d dirty_sync=%.1fms noop_sync=%.2fms (%.2f/%.2f)\n",
                total, dirty, ( noop1 + noop2 ) / 2.0, noop1, noop2 );
    }

    // 2000+ ファイルのマウントで「1バイトだけ変更」した場合のコスト
    write_files( 999000, 1 );
    double one = syncfs_once();
    printf( "RESULT one_file_changed_in_%d=%.2fms\n", total, one );

    printf( "DONE\n" );
    return 0;
}
