// game.h / game.cpp に追加した activity_turn_profiler の
// 型的な正しさを、実ビルドを待たずに独立検証する。
// 追加したコードと同一の実装を切り出してコンパイル・実行する。
#include <chrono>
#include <string>
#include <cstdio>

static void add_msg_stub( const char *fmt, const std::string &verb,
                          int turns, int ms, double per )
{
    printf( "[perf] %s: %d turns in %d ms (%.3f ms/turn)\n",
            verb.c_str(), turns, ms, per );
    ( void )fmt;
}

class activity_turn_profiler
{
    public:
        using clock = std::chrono::steady_clock;
        void sample( bool in_activity, const std::string &verb );
    private:
        void report() const;
        bool active = false;
        std::string current_verb;
        int turns = 0;
        clock::time_point started;
};

void activity_turn_profiler::sample( const bool in_activity,
                                     const std::string &verb )
{
    if( in_activity ) {
        if( !active ) {
            active = true;
            current_verb = verb;
            turns = 0;
            started = clock::now();
        } else if( verb != current_verb ) {
            report();
            current_verb = verb;
            turns = 0;
            started = clock::now();
        }
        ++turns;
        return;
    }
    if( active ) {
        report();
        active = false;
        turns = 0;
        current_verb.clear();
    }
}

void activity_turn_profiler::report() const
{
    if( turns < 2 ) {
        return;
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                             clock::now() - started ).count();
    const double per_turn = turns > 0
                            ? static_cast<double>( elapsed ) / turns
                            : 0.0;
    add_msg_stub( "[perf] %1$s: %2$d turns in %3$d ms (%4$.3f ms/turn)",
                  current_verb, turns, static_cast<int>( elapsed ), per_turn );
}

// ---- 動作検証 ----
static void busy( int ms )
{
    auto t0 = std::chrono::steady_clock::now();
    volatile double s = 0;
    while( std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now() - t0 ).count() < ms ) {
        for( int i = 0; i < 1000; ++i ) { s += i * 0.5; }
    }
}

int main()
{
    activity_turn_profiler p;

    printf( "== (1) 粉砕を 30 ターン（1 ターン 2ms 相当）==\n" );
    for( int t = 0; t < 30; ++t ) {
        p.sample( true, "smashing" );
        busy( 2 );
    }
    p.sample( false, "" );                 // 終了 → 報告

    printf( "== (2) 1 ターンだけの行動は報告しない ==\n" );
    p.sample( true, "wielding" );
    p.sample( false, "" );
    printf( "   （上に何も出なければ正しい）\n" );

    printf( "== (3) 途中で別のアクティビティに切り替わる ==\n" );
    for( int t = 0; t < 5; ++t ) { p.sample( true, "butchering" ); busy( 3 ); }
    for( int t = 0; t < 5; ++t ) { p.sample( true, "smashing" );   busy( 1 ); }
    p.sample( false, "" );

    printf( "== (4) 睡眠（アクティビティではないが effect_sleep）==\n" );
    for( int t = 0; t < 10; ++t ) { p.sample( true, "sleep" ); busy( 1 ); }
    p.sample( false, "" );

    printf( "== (5) 非アクティビティ中に何度呼んでも無害 ==\n" );
    for( int t = 0; t < 100; ++t ) { p.sample( false, "" ); }
    printf( "   （何も出なければ正しい）\n" );

    printf( "DONE\n" );
    return 0;
}
