#!/bin/bash
# Run all tests for Percy/Quest smoke bug fixes
#
# Tests:
# 1. Sandbox escape tests (existing)
# 2. STT/wake normalization tests
# 3. Pipeline/color follow-up tests
# 4. Client model update tests

set -e

cd "$(dirname "$0")"

# The backend deps (pydantic, trimesh, manifold3d...) live in the project venv.
# Bare `python3` is usually a system interpreter without them, which makes every
# backend suite "fail" with ModuleNotFoundError. Prefer the venv, and say so
# loudly if the deps are missing rather than reporting phantom test failures.
if [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PY="$VIRTUAL_ENV/bin/python"
elif [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY="$(command -v python3)"
fi
PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")"

echo "========================================"
echo "RUNNING ALL PERCY/QUEST TESTS"
echo "========================================"
echo "python: $PY ($("$PY" -V 2>&1))"

if ! "$PY" -c "import pydantic, pydantic_settings" >/dev/null 2>&1; then
    echo ""
    echo "ERROR: backend dependencies are missing from $PY."
    echo "       Create the venv first, then re-run:"
    echo "         python3 -m venv .venv"
    echo "         .venv/bin/pip install -r backend/requirements.txt"
    exit 2
fi

TOTAL_PASS=0
TOTAL_FAIL=0

# Test 1: Sandbox tests (existing)
echo ""
echo ">>> Running Sandbox Tests..."
echo ""
cd backend
if "$PY" -m cad.test_sandbox; then
    echo "Sandbox tests: PASSED"
else
    echo "Sandbox tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 1b: CAD PARAMS (extract / rewrite / prompt examples)
echo ""
echo ">>> Running CAD PARAMS Tests..."
echo ""
cd backend
if "$PY" -m cad.test_params; then
    echo "CAD PARAMS tests: PASSED"
else
    echo "CAD PARAMS tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 2: Speech/STT tests
echo ""
echo ">>> Running Speech/STT Tests..."
echo ""
cd backend
if "$PY" -m voice.test_speech; then
    echo "Speech tests: PASSED"
else
    echo "Speech tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 3: Pipeline tests
echo ""
echo ">>> Running Pipeline Tests..."
echo ""
cd backend
if "$PY" -m app.test_pipeline; then
    echo "Pipeline tests: PASSED"
else
    echo "Pipeline tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 3a: Intent router tests
echo ""
echo ">>> Running Intent Tests..."
echo ""
cd backend
if "$PY" -m ai.test_intent; then
    echo "Intent tests: PASSED"
else
    echo "Intent tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 3b: Project / version history tests
echo ""
echo ">>> Running Projects Tests..."
echo ""
cd backend
if "$PY" -m app.test_projects; then
    echo "Projects tests: PASSED"
else
    echo "Projects tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 3c: Mesh boolean tests (hole / loop / flat base on sculpts)
echo ""
echo ">>> Running Mesh Boolean Tests..."
echo ""
cd backend
if "$PY" -m mesh.test_boolean; then
    echo "Mesh boolean tests: PASSED"
else
    echo "Mesh boolean tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 4: Client model update tests
echo ""
echo ">>> Running Client Model Update Tests..."
echo ""
if node web-client/src/test_model_update.js; then
    echo "Client tests: PASSED"
else
    echo "Client tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi

# Test 5: VAD timing tests (post-wake grace period)
echo ""
echo ">>> Running VAD Timing Tests..."
echo ""
if node web-client/src/test_vad_timing.js; then
    echo "VAD timing tests: PASSED"
else
    echo "VAD timing tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi

# Test 6: Interaction math (two-hand, measurement, tape measure)
echo ""
echo ">>> Running Interaction Tests..."
echo ""
if node web-client/src/interaction/test_interaction.js; then
    echo "Interaction tests: PASSED"
else
    echo "Interaction tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi

# Test 7: suites that live in the tree but were never wired into this runner,
# plus the HTTP-surface regression tests.
for M in mesh.test_cleanup mesh.test_factory mesh.test_hf_space \
         mesh.test_meshy mesh.test_three_ws photos.test_drive app.test_api; do
    echo ""
    echo ">>> Running $M..."
    echo ""
    cd backend
    if "$PY" -m $M; then
        echo "$M: PASSED"
    else
        echo "$M: FAILED"
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
    fi
    cd ..
done

# Final summary
echo ""
echo "========================================"
echo "FINAL SUMMARY"
echo "========================================"

if [ $TOTAL_FAIL -eq 0 ]; then
    echo "✓ ALL TEST SUITES PASSED"
    exit 0
else
    echo "⚠️  $TOTAL_FAIL TEST SUITE(S) FAILED"
    exit 1
fi
