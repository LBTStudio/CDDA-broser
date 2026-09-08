// 【計測 6】進捗表示が「停滞して見える」ことの定量化。
//
// ---- 一次資料で確定した事実 ----
// src/do_turn.cpp:888-931 の進捗表示ロジック:
//
//   if( player_is_sleeping ) {
//       wait_refresh_rate = 30_minutes;      ← 睡眠
//   } else if( 進捗メッセージあり ) {
//       ACT_AUTODRIVE  → 1_turns
//       ACT_FIRSTAID   → 5_turns
//       それ以外       → 5_minutes;         ← 粉砕・読書・製作・解体
//   }
//   if( wait_redraw ) {
//       if( 初回 || once_every( min( 1_minutes, wait_refresh_rate ) ) ) {
//           if( 初回 || once_every( wait_refresh_rate ) ) {
//               ui_manager::redraw();        ← メイン画面（外側 1 分間隔）
//           }
//           ... wait_popup->wait_message( ... );
//           ui_manager::redraw();            ← ポップアップ
//           refresh_display();
//       }
//   }
//
// CDDA の 1 ターン = 1 ゲーム秒。
//   1_minutes  =    60 ターン
//   5_minutes  =   300 ターン
//   30_minutes =  1800 ターン
#include <stdio.h>

int main( void )
{
    const double pipeline_ms = 0.8600;   /* F-18 実測: 1 ターン全パイプライン */

    struct { const char *name; int total_turns; int refresh_turns; } c[] = {
        { "粉砕 死体 1 個",              30,   300 },
        { "粉砕 死体 5 個",             150,   300 },
        { "粉砕 死体 20 個",            600,   300 },
        { "読書 技能書 1 冊（30 分）", 1800,   300 },
        { "製作 30 分レシピ",          1800,   300 },
        { "解体 中サイズ死体",         1800,   300 },
        { "睡眠 8 時間",              28800,  1800 },
    };

    printf( "RESULT %-28s %8s %10s %12s %14s\n",
            "行動", "総ターン", "実時間", "進捗更新回数", "更新間隔(実時間)" );
    for( int i = 0; i < 7; ++i ) {
        double total_ms = c[i].total_turns * pipeline_ms;
        /* 初回 + refresh_turns ごと */
        int updates = 1 + c[i].total_turns / c[i].refresh_turns;
        double interval_ms = c[i].refresh_turns * pipeline_ms;
        printf( "RESULT %-28s %8d %8.0fms %10d 回 %10.0fms\n",
                c[i].name, c[i].total_turns, total_ms, updates, interval_ms );
    }

    printf( "RESULT\n" );
    printf( "RESULT ===== 問題点 =====\n" );
    printf( "RESULT (1) 粉砕 死体 1〜5 個は総ターンが更新間隔 300 未満なので\n" );
    printf( "RESULT     【初回の 1 回しか描画されない】= 完全に停滞して見える\n" );
    printf( "RESULT (2) 粉砕には進捗率表示が無い（pulp_activity_actor に\n" );
    printf( "RESULT     get_progress_message が未実装）→「Smashing…」だけ\n" );
    printf( "RESULT (3) 読書・製作は 1800 ターンで 7 回だけ更新\n" );
    printf( "RESULT (4) 睡眠は 28800 ターンで 17 回だけ更新（1548ms 間隔）\n" );
    printf( "DONE\n" );
    return 0;
}
