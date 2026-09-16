#!/usr/bin/env bash
# Gold Test 一键复跑（Git Bash / WSL）
# 用法：bash scripts/run_gold_test.sh [profile]   （默认 sample_a）
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE="${1:-sample_a}"
export PYTHONPATH=src

echo "============================================"
echo "  JTY-BidCompiler  Gold Test  (sample_a)"
echo "============================================"
echo
echo "[1/3] 全流程：MVP-1 ~ MVP-5"
python -m jty_bidcompiler.cli run --profile "$PROFILE"
echo
echo "[2/3] 自测（单元测试 + 黄金判例）"
python -m unittest discover -s tests -t .
echo
echo "[3/3] 产物清单"
ls -1 "projects/$PROFILE"
echo
echo "全部完成。"
