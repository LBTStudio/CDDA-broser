// 起動時の IDBFS 復元コスト（syncfs(true)）を実測する。
// これはゲームのロード時間に直接乗る固定費で、セーブツリーの
// ファイル数に比例する。1ファイルに束ねた場合との比較も行う。
#include <emscripten.h>
#include <stdio.h>
#include <string.h>

EM_ASYNC_JS( double, mount_and_restore, ( const char *mp ), {
    var p = UTF8ToString( mp );
    FS.mkdirTree( p );
    FS.mount( IDBFS, {}, p );
    var t0 = performance.now();
    await new Promise( function( r, j ) { FS.syncfs( true, function( e ) { e ? j( e ) : r(); } ); } );
    return performance.now() - t0;
} );

EM_ASYNC_JS( double, syncfs_push, (), {
    var t0 = performance.now();
    await new Promise( function( r ) { FS.syncfs( false, function() { r(); } ); } );
    return performance.now() - t0;
} );

EM_JS( int, idb_count, ( const char *mp ), {
    var p = UTF8ToString( mp );
    var n = 0;
    function walk( d ) {
        var e = FS.readdir( d );
        for( var i = 0; i < e.length; ++i ) {
            if( e[i] === '.' || e[i] === '..' ) { continue; }
            var f = d + '/' + e[i];
            var st = FS.stat( f );
            if( FS.isDir( st.mode ) ) { walk( f ); } else { n++; }
        }
    }
    walk( p );
    return n;
} );

static char payload[4096];
static char big[2 * 1024 * 1024];

int main( void )
{
    memset( payload, 'x', sizeof( payload ) - 1 );
    memset( big, 'y', sizeof( big ) - 1 );

    // シナリオA: 多数の小ファイル（非圧縮セーブ = 1 quad ごとに1ファイル）
    double t_mount_a = mount_and_restore( "/many" );
    printf( "RESULT A_mount_empty=%.1fms\n", t_mount_a );
    for( int i = 0; i < 2000; ++i ) {
        char path[128];
        snprintf( path, sizeof( path ), "/many/q_%d.map", i );
        FILE *f = fopen( path, "wb" );
        fwrite( payload, 1, sizeof( payload ) - 1, f );
        fclose( f );
    }
    printf( "RESULT A_push_2000_files=%.1fms count=%d\n", syncfs_push(), idb_count( "/many" ) );

    // シナリオB: 同じ総バイト数を1ファイルに束ねる（maps.zzip 相当）
    double t_mount_b = mount_and_restore( "/one" );
    printf( "RESULT B_mount_empty=%.1fms\n", t_mount_b );
    {
        FILE *f = fopen( "/one/maps.zzip", "wb" );
        // 2000 * 4KB = 約8MB 相当を 4 回書く
        for( int i = 0; i < 4; ++i ) {
            fwrite( big, 1, sizeof( big ) - 1, f );
        }
        fclose( f );
    }
    printf( "RESULT B_push_1_file_8MB=%.1fms count=%d\n", syncfs_push(), idb_count( "/one" ) );

    // 変更ゼロの再同期コストを両者で比較
    printf( "RESULT A_or_B_noop_after=%.2fms\n", syncfs_push() );

    printf( "DONE\n" );
    return 0;
}
