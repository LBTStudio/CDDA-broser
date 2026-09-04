// 製作（クラフト）中のターン処理を、実コードの構造どおりに再現する。
//
// 実コードから抽出した製作中の 1 ターンの流れ:
//
//   main.cpp:958        while( !do_turn() ) {}
//   do_turn.cpp:565     while( u.get_moves() > 0 || uquit == QUIT_WATCH ) {
//   do_turn.cpp:566-576   process_falling / cleanup_dead / mon_info_update /
//                          process_sound_markers（NPC 全員ぶん）
//   do_turn.cpp:577       explosion_handler::process_explosions()
//   do_turn.cpp:578       sounds::process_sound_markers( &u )
//   do_turn.cpp:579-583   if( !u.activity ) { redraw(); }  ← 製作中は false
//   do_turn.cpp:587       g->handle_action()
//   handle_action.cpp:3106  → has_destination() でなければ
//   do_turn.cpp:607-616  else 節: 100ms ごとに handle_key_blocking_activity()
//   do_turn.cpp:239        input_context ctxt = get_default_mode_input_context();
//   do_turn.cpp:240        ctxt.handle_input( 0 )
//   input_context.cpp:446    inp_mngr.get_input_event( ... )
//   sdltiles.cpp:4133        StartTextInput()          ← keychar のとき
//   sdltiles.cpp:4149        wnoutrefresh( stdscr )
//   sdltiles.cpp:4152-4155   needupdate ? refresh_display() : try_sdl_update()
//   sdltiles.cpp:4182-4189   inputdelay==0 → CheckMessages() 1 回で戻る
//
// 重要な事実（実コードを読んで確定）:
//   * 製作中は u.activity が真なので do_turn.cpp:579 の redraw() は走らない。
//   * handle_key_blocking_activity() は 100ms に 1 回しか呼ばれない
//     （do_turn.cpp:613 のレート制限）。
//   * その 1 回の中で input_context を 146 アクションぶん新規構築する。
//   * refresh_display() / ui_manager::redraw() もそこでしか走らない。
//
// つまり製作中は「100ms ごとに 1 回だけ画面が更新され、
// それ以外の時間は wasm がメインスレッドを占有し続ける」。
// 4GB Chromebook では 1 ターンの計算自体も遅いため、
// 100ms の枠に収まるターン数が減り、体感がさらに悪化する。
#include <emscripten.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static void log_line( const char *s )
{
    printf( "%s\n", s );
    fflush( stdout );
}

// ---------------- 計測補助 ----------------
EM_JS( void, install_frames, (), {
    Module._frames = 0;
    Module._gaps = [];
    Module._last = performance.now();
    var tick = function() {
        var n = performance.now();
        Module._gaps.push( n - Module._last );
        Module._last = n;
        Module._frames++;
        requestAnimationFrame( tick );
    };
    requestAnimationFrame( tick );
} );
EM_JS( int, get_frames, (), { return Module._frames; } );
EM_JS( double, get_max_gap, (), {
    var m = 0;
    for( var i = 0; i < Module._gaps.length; ++i ) {
        if( Module._gaps[i] > m ) { m = Module._gaps[i]; }
    }
    return m;
} );
EM_JS( void, reset_gaps, (), {
    Module._gaps = []; Module._frames = 0; Module._last = performance.now();
} );

EM_ASYNC_JS( void, yield_mc, (), {
    if( !Module._ch )
    {
        var ch = new MessageChannel();
        var pending = [];
        ch.port1.onmessage = function() {
            var q = pending; pending = [];
            for( var i = 0; i < q.length; ++i ) { q[i](); }
        };
        Module._ch = { push: function( r ) { pending.push( r ); ch.port2.postMessage( 0 ); } };
    }
    await new Promise( function( r ) { Module._ch.push( r ); } );
} );

// ---------------- input_context の再現 ----------------
//
// registered_actions は std::vector<std::string>。
// register_action は毎回 std::find で線形探索する（input_context.cpp:199）。
// DEFAULTMODE は 146 個登録するので、比較回数は 146*145/2 = 10585 回。
// さらに 10 個は to_translation() 付きで action_name_overrides
// （std::map<std::string, translation>）へも挿入される。
#define N_ACTIONS 146
#define N_TRANSLATED 10

typedef struct {
    char **actions;
    int count;
    // action_name_overrides の再現（std::map なので挿入は O(log n) + 確保）
    char **override_keys;
    char **override_vals;
    int n_overrides;
} ctx_t;

static const char *action_names[N_ACTIONS];

static void init_action_names( void )
{
    static char buf[N_ACTIONS][40];
    // 実際の DEFAULTMODE のアクション名（game.cpp:2674-2828 から抜粋）。
    // 長さ分布を実物に合わせるため実名をそのまま使う。
    const char *real[] = {
        "UP", "RIGHTUP", "RIGHT", "RIGHTDOWN", "DOWN", "LEFTDOWN", "LEFT", "LEFTUP",
        "pause", "LEVEL_DOWN", "LEVEL_UP", "toggle_map_memory", "center",
        "shift_n", "shift_ne", "shift_e", "shift_se", "shift_s", "shift_sw",
        "shift_w", "shift_nw", "cycle_move", "cycle_move_reverse", "reset_move",
        "toggle_run", "toggle_crouch", "toggle_prone", "open_movement", "open",
        "close", "smash", "loot", "examine", "examine_and_pickup", "advinv",
        "pickup", "pickup_all", "grab", "haul", "haul_toggle", "butcher", "chat",
        "look", "peek", "listitems", "zones", "inventory", "compare", "organize",
        "apply", "apply_wielded", "wear", "take_off", "eat", "open_consume",
        "read", "wield", "pick_style", "reload_item", "reload_weapon",
        "reload_wielded", "insert", "unload", "throw", "throw_wielded", "fire",
        "cast_spell", "recast_spell", "fire_burst", "select_fire_mode",
        "select_default_ammo", "drop", "unload_container", "drop_adj", "bionics",
        "mutations", "medical", "bodystatus", "sort_armor", "wait", "craft",
        "recraft", "long_craft", "construct", "disassemble", "sleep",
        "control_vehicle", "auto_travel_mode", "safemode", "autosafe",
        "autoattack", "ignore_enemy", "whitelist_enemy", "workout", "save",
        "quicksave", "SUICIDE", "player_data", "map", "sky", "missions",
        "factions", "morale", "messages", "help", "HELP_KEYBINDINGS",
        "open_options", "open_autopickup", "open_autonotes", "open_safemode",
        "open_distraction_manager", "open_color", "open_world_mods", "debug",
        "debug_scent", "debug_scent_type", "debug_temp", "debug_visibility",
        "debug_lighting", "debug_radiation", "debug_hour_timer",
    };
    const int n_real = ( int )( sizeof( real ) / sizeof( real[0] ) );
    for( int i = 0; i < N_ACTIONS; ++i ) {
        if( i < n_real ) {
            snprintf( buf[i], sizeof( buf[i] ), "%s", real[i] );
        } else {
            snprintf( buf[i], sizeof( buf[i] ), "%s_%d", real[i % n_real], i );
        }
        action_names[i] = buf[i];
    }
}

static volatile int sink_int = 0;

// std::string のコピーを含む register_action の再現。
// std::vector<std::string> への push_back は各要素で 1 回ヒープ確保
// （SSO を超える 16 文字以上のものは特に）。
static void ctx_register( ctx_t *c, const char *name, int with_translation )
{
    for( int i = 0; i < c->count; ++i ) {
        if( strcmp( c->actions[i], name ) == 0 ) {
            return;
        }
    }
    c->actions[c->count] = strdup( name );   // std::string のコピー
    ++c->count;
    if( with_translation ) {
        c->override_keys[c->n_overrides] = strdup( name );
        c->override_vals[c->n_overrides] = strdup( "Move north" );
        ++c->n_overrides;
    }
}

// get_default_mode_input_context() 相当。
static ctx_t *ctx_build( void )
{
    ctx_t *c = ( ctx_t * )calloc( 1, sizeof( ctx_t ) );
    c->actions = ( char ** )calloc( N_ACTIONS + 4, sizeof( char * ) );
    c->override_keys = ( char ** )calloc( N_TRANSLATED + 4, sizeof( char * ) );
    c->override_vals = ( char ** )calloc( N_TRANSLATED + 4, sizeof( char * ) );
    // コンストラクタが必ず登録するもの（input_context.h:61）
    ctx_register( c, "toggle_language_to_en", 0 );
    for( int i = 0; i < N_ACTIONS; ++i ) {
        ctx_register( c, action_names[i], i < N_TRANSLATED );
    }
    return c;
}

static void ctx_free( ctx_t *c )
{
    for( int i = 0; i < c->count; ++i ) {
        free( c->actions[i] );
    }
    for( int i = 0; i < c->n_overrides; ++i ) {
        free( c->override_keys[i] );
        free( c->override_vals[i] );
    }
    free( c->actions );
    free( c->override_keys );
    free( c->override_vals );
    free( c );
}

// ---------------- 製作 1 ターンの実処理 ----------------
//
// craft_activity_actor::do_turn（activity_actor.cpp:4163-4290）の
// 主要計算 + do_turn.cpp:566-578 の毎ターン処理を合成。
// 4GB Chromebook 想定で、1 ターンあたり実測 0.05ms 程度になるよう調整。
static void craft_turn_work( void )
{
    volatile double s = 0;
    // batch_time / crafting_speed_multiplier / exertion_adjusted_move_multiplier
    for( int i = 0; i < 8000; ++i ) {
        s += i * 1.000001;
    }
    // process_falling / cleanup_dead / mon_info_update / process_sound_markers
    for( int i = 0; i < 4000; ++i ) {
        s += i * 0.999999;
    }
    ( void )s;
}

// 画面更新（ui_manager::redraw + refresh_display）のコスト。
// タイル描画は実際はもっと重いが、ここでは相対比較が目的。
static void redraw_work( void )
{
    volatile double s = 0;
    for( int i = 0; i < 30000; ++i ) {
        s += i * 1.0000001;
    }
    ( void )s;
}

#define TURNS 4000

int main( void )
{
    init_action_names();
    install_frames();

    // ==== [1] 各部品の単体コスト ====
    {
        ctx_t *warm = ctx_build();
        ctx_free( warm );
        const int n = 1000;
        double t0 = emscripten_get_now();
        for( int i = 0; i < n; ++i ) {
            ctx_t *c = ctx_build();
            sink_int += c->count;
            ctx_free( c );
        }
        double t1 = emscripten_get_now();
        char buf[256];
        snprintf( buf, sizeof( buf ),
                  "[1a] get_default_mode_input_context() 相当: %d 回 = %.1fms / 1 回 %.4fms",
                  n, t1 - t0, ( t1 - t0 ) / n );
        log_line( buf );

        t0 = emscripten_get_now();
        for( int i = 0; i < n; ++i ) {
            craft_turn_work();
        }
        t1 = emscripten_get_now();
        snprintf( buf, sizeof( buf ), "[1b] 製作 1 ターンの実処理: %d 回 = %.1fms / 1 回 %.4fms",
                  n, t1 - t0, ( t1 - t0 ) / n );
        log_line( buf );

        t0 = emscripten_get_now();
        for( int i = 0; i < n; ++i ) {
            redraw_work();
        }
        t1 = emscripten_get_now();
        snprintf( buf, sizeof( buf ), "[1c] 画面更新 1 回: %d 回 = %.1fms / 1 回 %.4fms",
                  n, t1 - t0, ( t1 - t0 ) / n );
        log_line( buf );
    }

    // ==== [2] 現行実装 ====
    // 100ms レート制限 + 毎回 ctxt 構築 + emscripten_sleep(0)
    {
        reset_gaps();
        double last = emscripten_get_now();
        const double t0 = emscripten_get_now();
        int pumps = 0;
        for( int i = 0; i < TURNS; ++i ) {
            craft_turn_work();
            const double now = emscripten_get_now();
            if( now - last > 100.0 ) {
                ctx_t *c = ctx_build();      // do_turn.cpp:239
                sink_int += c->count;
                ctx_free( c );
                redraw_work();               // do_turn.cpp:255-257
                emscripten_sleep( 0 );       // ブラウザへの譲渡
                ++pumps;
                last = emscripten_get_now();
            }
        }
        const double t1 = emscripten_get_now();
        char buf[320];
        snprintf( buf, sizeof( buf ),
                  "[2] 現行(100ms制限/毎回ctxt構築/sleep0): %d ターン = %.1fms, "
                  "更新 %d 回, 描画 %d フレーム, 最大停止 %.1fms",
                  TURNS, t1 - t0, pumps, get_frames(), get_max_gap() );
        log_line( buf );
    }

    // ==== [3] 譲渡プリミティブのみ改善（100ms 制限は維持） ====
    {
        reset_gaps();
        double last = emscripten_get_now();
        const double t0 = emscripten_get_now();
        int pumps = 0;
        for( int i = 0; i < TURNS; ++i ) {
            craft_turn_work();
            const double now = emscripten_get_now();
            if( now - last > 100.0 ) {
                ctx_t *c = ctx_build();
                sink_int += c->count;
                ctx_free( c );
                redraw_work();
                yield_mc();
                ++pumps;
                last = emscripten_get_now();
            }
        }
        const double t1 = emscripten_get_now();
        char buf[320];
        snprintf( buf, sizeof( buf ),
                  "[3] MessageChannel のみ(100ms維持): %d ターン = %.1fms, "
                  "更新 %d 回, 描画 %d フレーム, 最大停止 %.1fms",
                  TURNS, t1 - t0, pumps, get_frames(), get_max_gap() );
        log_line( buf );
    }

    // ==== [4] レート制限を 16ms に + MessageChannel ====
    {
        reset_gaps();
        double last = emscripten_get_now();
        const double t0 = emscripten_get_now();
        int pumps = 0;
        for( int i = 0; i < TURNS; ++i ) {
            craft_turn_work();
            const double now = emscripten_get_now();
            if( now - last >= 16.0 ) {
                ctx_t *c = ctx_build();
                sink_int += c->count;
                ctx_free( c );
                redraw_work();
                yield_mc();
                ++pumps;
                last = emscripten_get_now();
            }
        }
        const double t1 = emscripten_get_now();
        char buf[320];
        snprintf( buf, sizeof( buf ),
                  "[4] 16ms + MessageChannel(ctxt 毎回構築): %d ターン = %.1fms, "
                  "更新 %d 回, 描画 %d フレーム, 最大停止 %.1fms",
                  TURNS, t1 - t0, pumps, get_frames(), get_max_gap() );
        log_line( buf );
    }

    // ==== [5] 16ms + MessageChannel + ctxt を再利用 ====
    {
        reset_gaps();
        ctx_t *shared = ctx_build();     // 1 回だけ構築して使い回す
        double last = emscripten_get_now();
        const double t0 = emscripten_get_now();
        int pumps = 0;
        for( int i = 0; i < TURNS; ++i ) {
            craft_turn_work();
            const double now = emscripten_get_now();
            if( now - last >= 16.0 ) {
                sink_int += shared->count;   // 構築せず参照するだけ
                redraw_work();
                yield_mc();
                ++pumps;
                last = emscripten_get_now();
            }
        }
        const double t1 = emscripten_get_now();
        ctx_free( shared );
        char buf[320];
        snprintf( buf, sizeof( buf ),
                  "[5] 16ms + MC + ctxt 再利用: %d ターン = %.1fms, "
                  "更新 %d 回, 描画 %d フレーム, 最大停止 %.1fms",
                  TURNS, t1 - t0, pumps, get_frames(), get_max_gap() );
        log_line( buf );
    }

    // ==== [6] 参考: 4GB Chromebook 想定（1 ターンが 3 倍重い場合） ====
    // 低速機では 100ms の枠に入るターン数が減り、
    // 「1 回の更新までに待つ時間」は変わらないが進捗が遅くなる。
    // 16ms 化するとフレームあたりの進捗が細かくなり体感が改善する。
    {
        reset_gaps();
        double last = emscripten_get_now();
        const double t0 = emscripten_get_now();
        int pumps = 0;
        const int slow_turns = TURNS / 3;
        for( int i = 0; i < slow_turns; ++i ) {
            craft_turn_work();
            craft_turn_work();
            craft_turn_work();               // 3 倍重い機体を模す
            const double now = emscripten_get_now();
            if( now - last > 100.0 ) {
                ctx_t *c = ctx_build();
                sink_int += c->count;
                ctx_free( c );
                redraw_work();
                emscripten_sleep( 0 );
                ++pumps;
                last = emscripten_get_now();
            }
        }
        const double t1 = emscripten_get_now();
        char buf[320];
        snprintf( buf, sizeof( buf ),
                  "[6] 低速機(3倍重)+現行: %d ターン = %.1fms, 更新 %d 回, "
                  "描画 %d フレーム, 最大停止 %.1fms",
                  slow_turns, t1 - t0, pumps, get_frames(), get_max_gap() );
        log_line( buf );
    }

    {
        reset_gaps();
        ctx_t *shared = ctx_build();
        double last = emscripten_get_now();
        const double t0 = emscripten_get_now();
        int pumps = 0;
        const int slow_turns = TURNS / 3;
        for( int i = 0; i < slow_turns; ++i ) {
            craft_turn_work();
            craft_turn_work();
            craft_turn_work();
            const double now = emscripten_get_now();
            if( now - last >= 16.0 ) {
                sink_int += shared->count;
                redraw_work();
                yield_mc();
                ++pumps;
                last = emscripten_get_now();
            }
        }
        const double t1 = emscripten_get_now();
        ctx_free( shared );
        char buf[320];
        snprintf( buf, sizeof( buf ),
                  "[7] 低速機(3倍重)+改善案: %d ターン = %.1fms, 更新 %d 回, "
                  "描画 %d フレーム, 最大停止 %.1fms",
                  slow_turns, t1 - t0, pumps, get_frames(), get_max_gap() );
        log_line( buf );
    }

    log_line( "DONE" );
    return 0;
}
