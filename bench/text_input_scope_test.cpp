// [CDDA-BROWSER] text_input_scope / set_text_input_flag の意味論検証。
#include "cata_web_text_input.h"
#include <cassert>
#include <cstdio>

static const char key_a = 0;
static const char key_b = 0;

int main()
{
    // 1. 初期状態は無効
    assert( !cata_web::text_input_active() );
    assert( cata_web::text_input_depth() == 0 );

    // 2. RAII スコープのネスト
    {
        cata_web::text_input_scope s1;
        assert( cata_web::text_input_active() );
        assert( cata_web::text_input_depth() == 1 );
        {
            cata_web::text_input_scope s2;
            assert( cata_web::text_input_depth() == 2 );
        }
        assert( cata_web::text_input_depth() == 1 );
        assert( cata_web::text_input_active() );
    }
    assert( cata_web::text_input_depth() == 0 );
    assert( !cata_web::text_input_active() );
    std::printf( "[1] RAII nest OK\n" );

    // 3. フラグの冪等性（同じキーを何度立てても 1 回分）
    for( int i = 0; i < 5; ++i ) {
        cata_web::set_text_input_flag( &key_a, true );
    }
    assert( cata_web::text_input_depth() == 1 );
    for( int i = 0; i < 5; ++i ) {
        cata_web::set_text_input_flag( &key_a, false );
    }
    assert( cata_web::text_input_depth() == 0 );
    std::printf( "[2] flag idempotent OK\n" );

    // 4. 別キーは独立して数えられる
    cata_web::set_text_input_flag( &key_a, true );
    cata_web::set_text_input_flag( &key_b, true );
    assert( cata_web::text_input_depth() == 2 );
    cata_web::set_text_input_flag( &key_a, false );
    assert( cata_web::text_input_depth() == 1 );
    assert( cata_web::text_input_active() );
    cata_web::set_text_input_flag( &key_b, false );
    assert( cata_web::text_input_depth() == 0 );
    std::printf( "[3] independent keys OK\n" );

    // 5. RAII とフラグの混在
    {
        cata_web::text_input_scope s;
        cata_web::set_text_input_flag( &key_a, true );
        assert( cata_web::text_input_depth() == 2 );
        cata_web::set_text_input_flag( &key_a, false );
        assert( cata_web::text_input_depth() == 1 );
    }
    assert( cata_web::text_input_depth() == 0 );
    std::printf( "[4] mixed RAII+flag OK\n" );

    // 6. 立っていないフラグを下ろしてもアンダーフローしない
    cata_web::set_text_input_flag( &key_a, false );
    cata_web::set_text_input_flag( &key_b, false );
    assert( cata_web::text_input_depth() == 0 );
    assert( !cata_web::text_input_active() );
    std::printf( "[5] no underflow OK\n" );

    std::printf( "DONE all checks passed\n" );
    return 0;
}
