#!/usr/bin/env python3
"""Validate real Makefile backend selection without compiling/publishing a game.
Usage: python3 bench/jspi_build_test.py /path/to/0.I-source
The source checkout HEAD must be unpatched 0.I.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
source = Path(sys.argv[1]).resolve()
original = subprocess.check_output(['git', '-C', str(source), 'show', 'HEAD:Makefile'], text=True)
out = root / 'bench/out'
out.mkdir(exist_ok=True)
script = root / 'ci/tune-makefile.sh'

with tempfile.TemporaryDirectory(prefix='backend-config-', dir=out) as directory:
    work = Path(directory)
    makefile = work / 'Makefile'

    def tune(backend=None):
        env = dict(os.environ)
        env.pop('CDDA_RUNTIME_BACKEND', None)
        env.pop('CDDA_INLINE_LIMIT', None)
        env.pop('CDDA_LINK_OPT', None)
        if backend is not None:
            env['CDDA_RUNTIME_BACKEND'] = backend
        return subprocess.run(['bash', str(script)], cwd=work, env=env,
                              capture_output=True, text=True)

    configured = {}
    for backend in [None, 'asyncify', 'jspi']:
        makefile.write_text(original)
        result = tune(backend)
        assert result.returncode == 0, result.stdout + result.stderr
        text = makefile.read_text()
        configured[str(backend)] = text
        for flag in ['-sINITIAL_MEMORY=256MB', '-sMAXIMUM_MEMORY=2GB', '-sSTACK_SIZE=4194304',
                     'LDFLAGS += -O2', 'OPTLEVEL = -O3']:
            assert flag in text, flag
        if backend == 'jspi':
            assert '-fwasm-exceptions' in text and '-fexceptions' not in text
            assert '-sJSPI\n' in text and "-sJSPI_EXPORTS=['main']" in text
            assert '-sSUPPORT_LONGJMP=wasm' in text
            assert '-sASYNCIFY' not in text
        else:
            assert '-fexceptions' in text and '-fwasm-exceptions' not in text
            assert '-sASYNCIFY\n' in text and '-sASYNCIFY_STACK_SIZE=16777216' in text
            assert '-sJSPI' not in text
        # Reapplying the same backend must not duplicate flags or alter output.
        result = tune(backend)
        assert result.returncode == 0, result.stdout + result.stderr
        assert makefile.read_text() == text
    assert configured['None'] == configured['asyncify'], 'default must stay unchanged'
    assert hashlib.sha256(configured['jspi'].encode()).digest() != hashlib.sha256(configured['asyncify'].encode()).digest()

    makefile.write_text(original)
    result = tune('invalid')
    assert result.returncode != 0 and makefile.read_text() == original
    makefile.write_text(configured['jspi'])
    assert tune('asyncify').returncode != 0, 'backend switch requires a clean source tree'

print('PASS build configuration: unchanged default, native EH/JSPI pairing, memory bounds, repeatability, invalid/mixed backend rejection')
