// 入力取りこぼし（last_input 単一スロット）の再現。
// CheckMessages() は 1 回の呼び出しで SDL キューを「全部」空にし、
// その過程で last_input を毎回上書きする。呼び出し側が受け取るのは
// 最後の 1 個だけ。よって 1 回の pump に N 個溜まっていたら N-1 個は
// 消滅する。これを SDL のイベントキューそのもので実証する。
#include <SDL.h>
#include <stdio.h>
#include <emscripten.h>

static int last_input;      // sdltiles.cpp:150 の last_input に相当
static int seen_total;      // pump が実際に見たイベント数

// sdltiles.cpp の CheckMessages() と同じ構造:
//   while( SDL_PollEvent(&ev) ) { ... last_input = ...; }
static void CheckMessages( void )
{
    SDL_Event ev;
    while( SDL_PollEvent( &ev ) ) {
        if( ev.type == SDL_USEREVENT ) {
            ++seen_total;
            last_input = ev.user.code;   // ← 毎回上書き
        }
    }
}

int main( void )
{
    SDL_Init( SDL_INIT_EVENTS );

    const int N = 12;   // 短時間に 12 回キーを押した状況
    printf( "RESULT push=%d\n", N );

    // ブラウザが 1 タスク中に配送した N 個をキューへ入れる
    for( int i = 1; i <= N; ++i ) {
        SDL_Event e; SDL_zero( e );
        e.type = SDL_USEREVENT; e.user.code = i;
        SDL_PushEvent( &e );
    }

    // ゲーム側は「1 ターン = 1 入力」で消費する。
    // ターン処理は必ず pump を通るので、そこで全部飲まれる。
    int consumed = 0;
    for( int turn = 0; turn < N; ++turn ) {
        last_input = 0;
        CheckMessages();            // ← ここで残り全部が消える
        if( last_input != 0 ) {
            ++consumed;
            printf( "RESULT turn=%d consumed_code=%d\n", turn, last_input );
        } else {
            printf( "RESULT turn=%d consumed_code=NONE\n", turn );
        }
    }
    printf( "RESULT pushed=%d seen_by_pump=%d actually_used=%d lost=%d\n",
            N, seen_total, consumed, seen_total - consumed );
    printf( "DONE\n" );
    return 0;
}
