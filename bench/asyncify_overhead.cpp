// Measure the CPU cost of Asyncify instrumentation itself on a workload that
// resembles CDDA's per-turn hot loops: many small virtual/indirect calls over
// object graphs, plus integer/float grid work.
#include <emscripten.h>
#include <cstdio>
#include <memory>
#include <vector>

struct critter {
    virtual ~critter() = default;
    virtual int act( int x ) = 0;
    int hp = 100;
};
struct zombie : critter {
    int act( int x ) override { hp += ( x % 7 ) - 3; return hp ^ x; }
};
struct dog : critter {
    int act( int x ) override { hp += ( x % 5 ) - 2; return hp + ( x >> 2 ); }
};

static std::vector<std::unique_ptr<critter>> make_pop( int n )
{
    std::vector<std::unique_ptr<critter>> v;
    for( int i = 0; i < n; ++i ) {
        if( i % 2 ) { v.push_back( std::make_unique<zombie>() ); }
        else { v.push_back( std::make_unique<dog>() ); }
    }
    return v;
}

static int grid[132][132];

int main()
{
    auto pop = make_pop( 400 );
    // warm up
    volatile int acc = 0;
    double t0 = emscripten_get_now();
    const int turns = 400;
    for( int t = 0; t < turns; ++t ) {
        for( auto &c : pop ) { acc += c->act( t ); }
        for( int y = 1; y < 131; ++y ) {
            for( int x = 1; x < 131; ++x ) {
                grid[y][x] = ( grid[y - 1][x] + grid[y][x - 1] + t ) & 0xFFFF;
            }
        }
    }
    double t1 = emscripten_get_now();
    printf( "RESULT turns=%d total_ms=%.2f per_turn_ms=%.4f acc=%d\n",
            turns, t1 - t0, ( t1 - t0 ) / turns, (int)acc );
    printf( "DONE\n" );
    return 0;
}
