// 【計測 3】修正の効果検証。
// 修正前（単一 last_input）と修正後（FIFO キュー）で、
// 押した回数が実際に処理される回数と一致するかを比較する。
#include <SDL.h>
#include <stdio.h>
#include <stdlib.h>

#define QMAX 64

static int last_input;

/* ---------- 修正後: 退避キュー ---------- */
static int q[QMAX]; static int qh, qt, qn;
static void q_push( int v ){ if(!v) return; if(qn>=QMAX){ qh=(qh+1)%QMAX; --qn; } q[qt]=v; qt=(qt+1)%QMAX; ++qn; }
static int  q_pop ( void ){ if(!qn) return 0; int v=q[qh]; qh=(qh+1)%QMAX; --qn; return v; }
static void q_pushfront( int v ){ if(!v) return; if(qn>=QMAX){ qt=(qt-1+QMAX)%QMAX; --qn; } qh=(qh-1+QMAX)%QMAX; q[qh]=v; ++qn; }

/* 修正前 */
static void CheckMessages_before( void )
{
    SDL_Event ev;
    while( SDL_PollEvent( &ev ) ) {
        if( ev.type == SDL_USEREVENT ) last_input = ev.user.code;
    }
}
/* 修正後: ループ冒頭で前周回の last_input を退避 */
static void CheckMessages_after( void )
{
    SDL_Event ev;
    while( SDL_PollEvent( &ev ) ) {
        q_push( last_input );        /* ← 追加した 1 行 */
        last_input = 0;
        if( ev.type == SDL_USEREVENT ) last_input = ev.user.code;
    }
}
static void drain_if_idle( void ){ if( !last_input && qn ) last_input = q_pop(); }

static void pump_before( void ){ CheckMessages_before(); last_input = 0; }
static void pump_after ( void ){ CheckMessages_after();  q_pushfront( last_input ); last_input = 0; }

static void push_n( int n )
{
    for( int i = 1; i <= n; ++i ) {
        SDL_Event e; SDL_zero(e); e.type=SDL_USEREVENT; e.user.code=i;
        SDL_PushEvent(&e);
    }
}
static void clear_all( void ){ SDL_Event e; while(SDL_PollEvent(&e)){} last_input=0; qh=qt=qn=0; }

int main( void )
{
    SDL_Init( SDL_INIT_EVENTS );
    const int cases[] = { 1, 2, 3, 5, 10, 20 };

    printf( "RESULT ===== A) ターン処理中の pump_events() を挟む場合 =====\n" );
    for( int c = 0; c < 6; ++c ) {
        int n = cases[c];

        clear_all(); push_n(n);
        pump_before();                       /* ターン処理中の pump */
        last_input = 0; CheckMessages_before();
        int used_b = last_input ? 1 : 0;
        for(int k=0;k<n+4;k++){ last_input=0; CheckMessages_before(); if(last_input) ++used_b; }

        clear_all(); push_n(n);
        pump_after();
        int used_a = 0;
        for(int k=0;k<n+4;k++){ last_input=0; CheckMessages_after(); drain_if_idle(); if(last_input) ++used_a; }

        printf( "RESULT pressed=%2d  before=%2d  after=%2d  %s\n",
                n, used_b, used_a, (used_a==n)?"OK 一致":"NG" );
    }

    printf( "RESULT ===== B) pump なし・まとめて配送された場合 =====\n" );
    for( int c = 0; c < 6; ++c ) {
        int n = cases[c];

        clear_all(); push_n(n);
        int used_b = 0;
        for(int k=0;k<n+4;k++){ last_input=0; CheckMessages_before(); if(last_input) ++used_b; }

        clear_all(); push_n(n);
        int used_a = 0;
        for(int k=0;k<n+4;k++){ last_input=0; CheckMessages_after(); drain_if_idle(); if(last_input) ++used_a; }

        printf( "RESULT pressed=%2d  before=%2d  after=%2d  %s\n",
                n, used_b, used_a, (used_a==n)?"OK 一致":"NG" );
    }

    printf( "RESULT ===== C) 上限 %d を超えた場合（暴走防止） =====\n", QMAX );
    clear_all(); push_n(200);
    int used=0; for(int k=0;k<300;k++){ last_input=0; CheckMessages_after(); drain_if_idle(); if(last_input) ++used; }
    printf( "RESULT pressed=200 after=%d （上限で頭切り＝押しっぱなし暴走なし: %s）\n",
            used, used<=QMAX+1 ? "OK" : "NG" );

    printf( "DONE\n" );
    return 0;
}
