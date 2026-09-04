#include <emscripten.h>
#include <stdio.h>
#include <stdlib.h>

// Measure the wall-clock cost of one emscripten_sleep(0) round trip
// (Asyncify unwind + setTimeout(0) + rewind) at various live stack depths.
static double work_sink = 0.0;

static void deep(int depth, int n)
{
    if( depth > 0 ) {
        // volatile locals so the frame is not optimized away
        volatile double pad[8];
        for( int i = 0; i < 8; ++i ) { pad[i] = depth * 1.0 + i; }
        deep( depth - 1, n );
        for( int i = 0; i < 8; ++i ) { work_sink += pad[i]; }
        return;
    }
    for( int i = 0; i < n; ++i ) {
        emscripten_sleep( 0 );
    }
}

int main(void)
{
    const int n = 200;
    int depths[] = { 0, 32, 256, 1024 };
    for( unsigned d = 0; d < sizeof(depths)/sizeof(depths[0]); ++d ) {
        double t0 = emscripten_get_now();
        deep( depths[d], n );
        double t1 = emscripten_get_now();
        printf( "RESULT sleep0 depth=%d n=%d total_ms=%.2f per_yield_ms=%.4f\n",
                depths[d], n, t1 - t0, ( t1 - t0 ) / n );
    }
    printf( "RESULT sink=%.1f\n", work_sink );
    printf( "DONE\n" );
    return 0;
}
