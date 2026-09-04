// アクティビティ（読書・製作・分解など）の全経路を網羅的に計測する。
//
// 目的:
//   「読書がゲームにならないほど遅い」という報告の真因を特定し、
//   プレイに差し支えない基準（下記）に対して各経路がどれだけ
//   外れているかを数値で示す。
//
// ------------------------------------------------------------------
// プレイに差し支えない基準（この計測の判定軸）
// ------------------------------------------------------------------
//   A. 最大描画停止      <=  50ms   … これを超えるとカクつきを知覚する
//   B. 入力応答遅延      <= 100ms   … これを超えると「反応しない」と感じる
//   C. 進捗表示の更新    <= 500ms   … これを超えると「固まった」と感じる
//   D. 実描画フレーム率  >=  20fps  … 下回るとスクロールが飛ぶ
//
// 出典: Nielsen 1993 の応答時間しきい値
//   (0.1s = 即座に感じる / 1.0s = 思考が途切れない / 10s = 注意が切れる)
// を、ゲームの毎フレーム更新という文脈に合わせて厳しめに取ったもの。
//
// ------------------------------------------------------------------
// 実機ソースで確認した構造
// ------------------------------------------------------------------
// 寸法（src/map_scale_constants.h）:
//   MAPSIZE=11, SEEX=SEEY=12 → MAPSIZE_X=MAPSIZE_Y=132
//   OVERMAP_DEPTH=10, OVERMAP_HEIGHT=10 → OVERMAP_LAYERS=21
//   → リアリティバブル 1 層 = 132*132 = 17424 タイル
//   → z 有効時の全層 = 17424 * 21 = 365904 タイル
//
// 読書 1 冊（data/json/items/book の最頻値 "30 m"）:
//   30 ゲーム分 = 1800 ターン。read_speed 等で増減する。
//   read_activity_actor::do_turn 自体は非常に軽い（分岐と一部の乗算のみ）。
//
// しかし 1 ターンごとに do_turn() の【全パイプライン】が走る
// （src/do_turn.cpp:729-763）:
//   m.build_floor_caches()           全 21 層 × 17424 タイル
//   m.process_falling / vehmove
//   m.process_fields() / process_items()
//   sounds::process_sounds()
//   m.build_map_cache( levz, true )  outside/transparency/floor/vehicle/vision
//                                    の 5 パス × 層数
//   monmove()                        全モンスター AI
//   g->mon_info_update()             可視クリーチャ全走査 + sees() LOS
//   u.process_turn() / update_bodytemp / update_body_wetness
//
// 描画は 3 経路しかない:
//   do_turn.cpp:579  if( !u.activity && ... ) redraw()
//     → アクティビティ中は【実行されない】
//   do_turn.cpp:696  handle_key_blocking_activity()
//     → レート制限（0.I=100ms、本 PR で 16ms に変更済み）
//   do_turn.cpp:795  wait_popup の redraw
//     → wait_refresh_rate = 5_minutes（= ゲーム内 5 分 = 300 ターン）
//
// mon_info_update() は 613 / 696 / 762 行の【3 箇所】で呼ばれ、
// いずれもレート制限がない。ただしこれは safemode を駆動しており
// （game.cpp:4856-4875 で set_safe_mode / newseen / mostseen を更新）、
// 単純に間引くとモンスター接近の警告が遅れる。ゲーム性に影響する。

#include <emscripten.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ---- 実機の寸法 -------------------------------------------------------
#define MAPSIZE_X       132
#define MAPSIZE_Y       132
#define BUBBLE_TILES    ( MAPSIZE_X * MAPSIZE_Y )   /* 17424 */
#define OVERMAP_LAYERS  21

// ---- 描画フレーム計測（rAF を数える） --------------------------------
EM_JS( void, install_frame_counter, (), {
    Module._frames = 0;
    Module._lastFrameTs = performance.now();
    Module._maxGap = 0;
    const tick = function() {
        const now = performance.now();
        const gap = now - Module._lastFrameTs;
        if( gap > Module._maxGap ) { Module._maxGap = gap; }
        Module._lastFrameTs = now;
        Module._frames++;
        requestAnimationFrame( tick );
    };
    requestAnimationFrame( tick );
} );

EM_JS( void, reset_frame_counter, (), {
    Module._frames = 0;
    Module._maxGap = 0;
    Module._lastFrameTs = performance.now();
} );

EM_JS( int, get_frames, (), { return Module._frames; } );
EM_JS( double, get_max_gap, (), { return Module._maxGap; } );

// ---- yield プリミティブ ----------------------------------------------
EM_ASYNC_JS( void, yield_msgchan, (), {
    if( !Module._yieldChan ) {
        const ch = new MessageChannel();
        ch.port1.start();
        Module._yieldChan = ch;
    }
    const ch = Module._yieldChan;
    await new Promise( function( resolve ) {
        ch.port1.onmessage = function() { resolve(); };
        ch.port2.postMessage( 0 );
    } );
} );

EM_ASYNC_JS( void, yield_raf, (), {
    await new Promise( function( resolve ) { requestAnimationFrame( resolve ); } );
} );

static double now_ms( void )
{
    return emscripten_get_now();
}

// ---- 仕事の合成 -------------------------------------------------------
// 実機のタイル走査を模したメモリ+算術負荷。
// タイル配列を実際に触るのでキャッシュミスの効果も出る。
#define GRID_BYTES ( BUBBLE_TILES * 4 )
static unsigned char *grid_a;
static unsigned char *grid_b;
static volatile unsigned long sink = 0;

// 1 層ぶんのタイル走査（outside_cache / transparency_cache 相当）
static void scan_layer( int passes )
{
    unsigned long s = sink;
    for( int p = 0; p < passes; ++p ) {
        for( int i = 0; i < BUBBLE_TILES; ++i ) {
            unsigned char v = grid_a[i];
            grid_b[i] = ( unsigned char )( v ^ ( unsigned char )( i & 0xff ) );
            s += v;
        }
    }
    sink = s;
}

// 純粋な算術負荷（AI 判定・LOS 計算など、タイル配列を持たない処理）
static void burn( int units )
{
    unsigned long s = sink;
    for( int i = 0; i < units; ++i ) {
        s += ( unsigned long )sqrt( ( double )( i & 1023 ) + 1.0 );
    }
    sink = s;
}

// ------------------------------------------------------------------
// 毎ターン処理の内訳
// ------------------------------------------------------------------
// z-level 有効時、build_map_cache は全 21 層を回るが、
// 各層は dirty フラグでガードされている（map.cpp:9263）。
// 読書中はプレイヤーが動かないので大半の層は clean のまま。
// ただし現在層は視界・field・item の変化で毎ターン dirty になる。
//
// ここでは「現在層 + 上下 1 層が dirty」という現実的な想定を置く。
#define DIRTY_LAYERS 3

static void part_floor_caches( void )
{
    // build_floor_caches(): 全層ループ、dirty な層だけ走査
    scan_layer( DIRTY_LAYERS );
}

static void part_map_cache( void )
{
    // build_map_cache(): outside / transparency / floor / vehicle / vision
    // の 5 パス。dirty な層のみ。
    scan_layer( DIRTY_LAYERS * 5 );
}

static void part_process_fields( void ) { scan_layer( 1 ); }
static void part_process_items( void )  { burn( 20000 ); }
static void part_monmove( void )        { burn( 40000 ); }
static void part_mon_info( void )       { burn( 60000 ); }  /* sees() LOS 全走査 */
static void part_process_turn( void )   { burn( 25000 ); }
static void part_activity_doturn( void ) { burn( 400 ); }   /* 読書本体は激軽 */
static void part_misc( void )           { burn( 8000 ); }
static void part_redraw( void )         { scan_layer( 2 ); burn( 30000 ); }
static void part_input_ctx( void )      { burn( 15000 ); }  /* 146 register_action */

// 1 ターンの全パイプライン（描画を除く）
static void one_turn_pipeline( int with_mon_info )
{
    part_activity_doturn();
    part_floor_caches();
    part_process_fields();
    part_process_items();
    part_map_cache();
    part_monmove();
    if( with_mon_info ) {
        part_mon_info();
    }
    part_process_turn();
    part_misc();
}

// ---- 計測結果 ---------------------------------------------------------
typedef struct {
    double total_ms;
    int frames;
    double max_gap_ms;
    int redraws;
    double longest_block_ms;
} result_t;

#define CRIT_MAX_GAP_MS      50.0
#define CRIT_INPUT_MS       100.0
#define CRIT_MIN_FPS         20.0

static void report( const char *label, result_t r, int turns )
{
    double fps = r.total_ms > 0.0 ? ( r.frames * 1000.0 / r.total_ms ) : 0.0;
    printf( "%s turns=%d total_ms=%.0f frames=%d fps=%.1f[%s] "
            "max_gap_ms=%.1f[%s] block_ms=%.1f[%s] redraws=%d\n",
            label, turns, r.total_ms, r.frames,
            fps, ( fps >= CRIT_MIN_FPS ) ? "OK" : "NG",
            r.max_gap_ms, ( r.max_gap_ms <= CRIT_MAX_GAP_MS ) ? "OK" : "NG",
            r.longest_block_ms, ( r.longest_block_ms <= CRIT_INPUT_MS ) ? "OK" : "NG",
            r.redraws );
}

// ======================================================================
// シナリオ
// ======================================================================
// mode:
//   0 = 現行 0.I     (100ms 制限 / sleep(0) / ctxt毎回 / mon_info毎ターン)
//   1 = 本PR現状     ( 16ms 制限 / msgchan / ctxt再利用 / mon_info毎ターン)
//   2 = 案A          ( 16ms 制限 / msgchan / mon_info を描画時のみ)
//   3 = 案B          ( 16ms 制限 / msgchan / mon_info を N ターンごと)
//
// 案A は safemode の反応が描画間隔（16ms 相当のターン数）まで遅れる。
// 案B は「16 ターンごと」と上限を切ることで、CPU が速い環境でも
// safemode の遅延をターン数で保証する。
#define MON_INFO_EVERY_N_TURNS 16

// 案B の間隔 N を掃引するための可変値。mode==4 のときに使う。
static int mon_info_interval = MON_INFO_EVERY_N_TURNS;

static result_t run_activity( int turns, int mode, int slow_factor )
{
    result_t r;
    memset( &r, 0, sizeof( r ) );

    const int poll_ms = ( mode == 0 ) ? 100 : 16;

    reset_frame_counter();
    const double t0 = now_ms();
    double last_poll = t0;
    double last_yield_end = t0;
    double longest = 0.0;

    for( int t = 0; t < turns; ++t ) {
        int with_mon_info;
        switch( mode ) {
            case 0:
            case 1:
                with_mon_info = 1;
                break;
            case 2:
                with_mon_info = 0;      /* 描画時にまとめて実行 */
                break;
            case 4:
                with_mon_info = ( t % mon_info_interval ) == 0;
                break;
            default:
                with_mon_info = ( t % MON_INFO_EVERY_N_TURNS ) == 0;
                break;
        }

        for( int s = 0; s < slow_factor; ++s ) {
            one_turn_pipeline( with_mon_info );
        }

        const double now = now_ms();
        if( ( now - last_poll ) > poll_ms ) {
            if( mode == 0 ) {
                part_input_ctx();          /* 0.I は毎回作り直す */
            }
            if( mode == 2 ) {
                for( int s = 0; s < slow_factor; ++s ) {
                    part_mon_info();       /* 描画のタイミングで 1 回 */
                }
            }
            part_redraw();
            r.redraws++;

            const double blk = now_ms() - last_yield_end;
            if( blk > longest ) { longest = blk; }

            if( mode == 0 ) {
                emscripten_sleep( 0 );
            } else {
                yield_msgchan();
            }
            last_yield_end = now_ms();
            last_poll = last_yield_end;
        }
    }

    r.total_ms = now_ms() - t0;
    r.frames = get_frames();
    r.max_gap_ms = get_max_gap();
    r.longest_block_ms = longest;
    return r;
}

// ---- 部品単体のコスト ------------------------------------------------
static double measure_part( void ( *fn )( void ), int reps )
{
    for( int i = 0; i < 4; ++i ) { fn(); }
    const double t0 = now_ms();
    for( int i = 0; i < reps; ++i ) { fn(); }
    return ( now_ms() - t0 ) / reps;
}

static void part_full_turn( void ) { one_turn_pipeline( 1 ); }

int main( void )
{
    grid_a = ( unsigned char * )malloc( GRID_BYTES );
    grid_b = ( unsigned char * )malloc( GRID_BYTES );
    if( !grid_a || !grid_b ) {
        printf( "ERR malloc failed\nDONE\n" );
        return 1;
    }
    for( int i = 0; i < GRID_BYTES; ++i ) {
        grid_a[i] = ( unsigned char )( i * 7 );
    }

    install_frame_counter();

    printf( "=== アクティビティ全経路の網羅計測 ===\n" );
    printf( "寸法: バブル %d タイル/層 (132x132), 全 %d 層, dirty 想定 %d 層\n",
            BUBBLE_TILES, OVERMAP_LAYERS, DIRTY_LAYERS );
    printf( "基準: 最大描画停止<=%.0fms / 入力応答<=%.0fms / 実fps>=%.0f\n\n",
            CRIT_MAX_GAP_MS, CRIT_INPUT_MS, CRIT_MIN_FPS );

    printf( "--- [1] 毎ターン処理の内訳（1回あたり ms） ---\n" );
    printf( "[1a] activity actor do_turn (読書本体)  = %.4f\n", measure_part( part_activity_doturn, 200 ) );
    printf( "[1b] m.build_floor_caches()             = %.4f\n", measure_part( part_floor_caches, 100 ) );
    printf( "[1c] m.process_fields()                 = %.4f\n", measure_part( part_process_fields, 200 ) );
    printf( "[1d] m.process_items()                  = %.4f\n", measure_part( part_process_items, 200 ) );
    printf( "[1e] m.build_map_cache()                = %.4f\n", measure_part( part_map_cache, 60 ) );
    printf( "[1f] monmove()                          = %.4f\n", measure_part( part_monmove, 200 ) );
    printf( "[1g] mon_info_update()                  = %.4f\n", measure_part( part_mon_info, 200 ) );
    printf( "[1h] u.process_turn()                   = %.4f\n", measure_part( part_process_turn, 200 ) );
    printf( "[1i] ui_manager::redraw()+refresh       = %.4f\n", measure_part( part_redraw, 100 ) );
    printf( "[1j] get_default_mode_input_context()   = %.4f\n", measure_part( part_input_ctx, 200 ) );
    printf( "[1k] 1ターン全パイプライン合計           = %.4f\n", measure_part( part_full_turn, 60 ) );
    printf( "\n" );

    // 技能書 "30 m" = 1800 ターン。ただし全 8 シナリオを 1800 ターンで
    // 回すと計測時間が長すぎるので、代表として 600 ターンで測り、
    // 総時間は 3 倍して 1800 ターン相当を読み取る。
    // 最大描画停止・fps は 1 ターンあたりの性質なのでターン数に依らない。
    const int READ_TURNS = 600;
    printf( "--- [2] 読書 %d ターン（技能書1800ターンの1/3。総時間は3倍で読む） ---\n", READ_TURNS );
    report( "[2a] 現行0.I  ", run_activity( READ_TURNS, 0, 1 ), READ_TURNS );
    report( "[2b] 本PR現状 ", run_activity( READ_TURNS, 1, 1 ), READ_TURNS );
    report( "[2c] 案A      ", run_activity( READ_TURNS, 2, 1 ), READ_TURNS );
    report( "[2d] 案B      ", run_activity( READ_TURNS, 3, 1 ), READ_TURNS );
    printf( "\n" );

    printf( "--- [3] 低速機（CPU 3倍遅い想定）読書 %d ターン ---\n", READ_TURNS );
    report( "[3a] 現行0.I  ", run_activity( READ_TURNS, 0, 3 ), READ_TURNS );
    report( "[3b] 本PR現状 ", run_activity( READ_TURNS, 1, 3 ), READ_TURNS );
    report( "[3c] 案A      ", run_activity( READ_TURNS, 2, 3 ), READ_TURNS );
    report( "[3d] 案B      ", run_activity( READ_TURNS, 3, 3 ), READ_TURNS );
    printf( "\n" );

    // ------------------------------------------------------------------
    // [4] 案B の間隔 N の掃引
    // ------------------------------------------------------------------
    // N を決めるにあたっての制約は 2 つある。
    //   ・N が小さすぎると mon_info の間引き効果が出ない（速くならない）
    //   ・N が大きすぎると safemode の警告が N ターン遅れる。
    //     CDDA の 1 ターンは 1 ゲーム秒なので N ターン = N ゲーム秒。
    //     モンスターの移動速度は最速で 1 ターン 1 タイル級なので、
    //     N タイル分の接近を見逃す可能性がある。
    //     SAFEMODEPROXIMITY の既定は 0（=視界いっぱい）だが、
    //     実用上は数タイルの余裕で足りる。
    // よって「基準 A/B/D を満たす最小の N」を選ぶ。
    // 単発だとブラウザ側の rAF 停止（タブスロットリング等）で
    // 外れ値が混じるため、3 回まわして中央値を採る。
    printf( "--- [4] 案B の間隔 N 掃引（低速機 3倍・%d ターン・3回中央値） ---\n", READ_TURNS );
    {
        static const int candidates[] = { 1, 2, 4, 8, 16, 32, 64 };
        const unsigned ncand = sizeof( candidates ) / sizeof( candidates[0] );
        for( unsigned i = 0; i < ncand; ++i ) {
            char label[64];
            result_t runs[3];
            mon_info_interval = candidates[i];
            for( int k = 0; k < 3; ++k ) {
                runs[k] = run_activity( READ_TURNS, 4, 3 );
            }
            /* total_ms の中央値を持つ回を採用 */
            int mid = 0;
            for( int k = 0; k < 3; ++k ) {
                int lower = 0;
                for( int j = 0; j < 3; ++j ) {
                    if( runs[j].total_ms < runs[k].total_ms ) { lower++; }
                }
                if( lower == 1 ) { mid = k; }
            }
            snprintf( label, sizeof( label ), "[4-%02d] N=%-3d ", candidates[i], candidates[i] );
            report( label, runs[mid], READ_TURNS );
        }
        mon_info_interval = MON_INFO_EVERY_N_TURNS;
    }
    printf( "\n" );

    printf( "RESULT sink=%lu\n", sink );
    printf( "DONE\n" );
    free( grid_a );
    free( grid_b );
    return 0;
}
