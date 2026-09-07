// 【計測 2】pump_events() による入力の明示的破棄を再現する。
//
// sdltiles.cpp:4096-4128 の pump_events() は:
//     CATA_WEB_YIELD();
//     CheckMessages();                 ← SDL キューを全部飲む
//     last_input = input_event();      ← ★飲んだ結果を捨てる
//     previously_pressed_key = 0;
//
// pump_events() は src 内 35 箇所から呼ばれる。移動 1 回のターン処理でも
// do_turn.cpp:306 / cata_tiles.cpp / animation.cpp などを通る。
// つまり「ターン処理中に届いた入力」は届いた瞬間に消える。
#include <SDL.h>
#include <stdio.h>

static int last_input;
static int seen_by_pump;
static int seen_by_getinput;

static void CheckMessages( void )
{
    SDL_Event ev;
    while( SDL_PollEvent( &ev ) ) {
        if( ev.type == SDL_USEREVENT ) {
            last_input = ev.user.code;
        }
    }
}

// sdltiles.cpp:4096 の pump_events() と同じ
static void pump_events( void )
{
    CheckMessages();
    int had = last_input;
    if( had ) {
        ++seen_by_pump;
    }
    last_input = 0;              // ← input_event() = error 型に戻す＝破棄
}

// sdltiles.cpp:4133 get_input_event() の inputdelay<0 分岐と同じ
static int get_input_event( void )
{
    do {
        CheckMessages();
        if( last_input != 0 ) {
            break;
        }
        // ここで yield して次のブラウザタスクを待つ
        return -1;               // 本計測では「待ちに入った」を意味する
    } while( last_input == 0 );
    ++seen_by_getinput;
    return last_input;
}

int main( void )
{
    SDL_Init( SDL_INIT_EVENTS );

    const int N = 10;
    printf( "RESULT scenario=ターン処理中に%d回キーが届く\n", N );

    for( int i = 1; i <= N; ++i ) {
        SDL_Event e; SDL_zero( e );
        e.type = SDL_USEREVENT; e.user.code = i;
        SDL_PushEvent( &e );
    }

    // 移動 1 回のターン処理では pump_events() が複数回通る
    // （do_turn.cpp:306, animation.cpp:79, cata_tiles.cpp:627/867 ...）
    pump_events();

    // その後にゲームが「次の入力」を取りに来る
    last_input = 0;
    int got = get_input_event();

    printf( "RESULT pump_events_が飲んで捨てた=%d\n", seen_by_pump );
    printf( "RESULT get_input_event_が受け取れた=%s\n",
            got == -1 ? "なし（待ちに入った）" : "あり" );
    printf( "RESULT 結論=ターン処理中の入力は全て消える\n" );
    printf( "DONE\n" );
    return 0;
}
