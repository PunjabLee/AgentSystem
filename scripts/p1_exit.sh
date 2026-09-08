#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# P1 出口判据的机械裁判（WBS，2026-08-31 改为可证伪形式）
#
#   判据 1  三档 tool_calls  finish_reason=="tool_calls" ∧ 命中 stub ∧ arguments 可解析
#   判据 2  CI 绿           且必须**点名**包含回滚路径与原子消费两个单测
#   判据 3  恢复演练         干净实例上行数一致
#
# 单人项目无第三方裁判，机械判据是唯一防线。故本脚本刻意不给任何
# 「大概算通过」的余地：三条全绿才 PASS。
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail          # 刻意不加 -e：三条判据都要跑完再汇总，不能中途退出
cd "$(dirname "${BASH_SOURCE[0]}")/.."

C1=FAIL; C2=FAIL; C3=FAIL

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  判据 1 · 三档 tool_calls                                ║"
echo "╚══════════════════════════════════════════════════════════╝"
if uv run python scripts/chat.py; then C1=PASS; fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  判据 2 · CI 绿（含两个点名单测）                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
# 判据原文特别注明「空测试集也会绿」，故不能只看 pytest 退出码 ——
# 必须确认这两个用例**确实存在且通过**。它们分别守着 P1.3.2 的回滚路径
# 与 P1.4.2 的原子消费，是 P1 两条核心设计的唯一实证。
REQUIRED=(
  "tests/test_audit_decorator.py::test_audit_survives_business_rollback"
  "tests/test_write_intent.py::test_concurrent_consume_exactly_one_wins"
)
LINT_OK=0; FMT_OK=0; TESTS_OK=0; NAMED_OK=1
uv run ruff check . >/dev/null 2>&1 && LINT_OK=1
uv run ruff format --check . >/dev/null 2>&1 && FMT_OK=1
uv run pytest -q >/dev/null 2>&1 && TESTS_OK=1
for t in "${REQUIRED[@]}"; do
  # 🔴 不能只看退出码：pytest 对被 skip 的用例返回 0，与「真跑过且通过」
  #    不可区分。已实测：一个 `assert False` 的用例加上 @pytest.mark.skip
  #    之后，`pytest -q` 退出码仍为 0。
  #    缺环境变量导致的静默跳过会让点名检查彻底失效 —— 而这两个用例是
  #    P1 两条核心设计的唯一实证。ci.yml 早有同样的校验，这里补齐对齐。
  out=$(uv run pytest "$t" -rs 2>&1)
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "  ❌ $t —— 缺失或未通过"; NAMED_OK=0
  # 🔴 按 -rs 输出的大写 SKIPPED 匹配，不要按「N skipped」统计行匹配：
  #    pyproject 的 addopts 已含 -q，命令行再传一个就成了 -qq，而 -qq
  #    会把统计行整个抑制掉 —— 模式永远匹配不上，检查静默失效。
  #    实测踩过：本处与 ci.yml 最初都写成了 [0-9]+ skipped。
  elif echo "$out" | grep -q "^SKIPPED"; then
    echo "  ❌ $t —— 被跳过。skip 不是 pass"; NAMED_OK=0
  else
    echo "  ✅ $t"
  fi
done
echo "  $([ $LINT_OK = 1 ] && echo ✅ || echo ❌) ruff check"
echo "  $([ $FMT_OK = 1 ] && echo ✅ || echo ❌) ruff format --check"
echo "  $([ $TESTS_OK = 1 ] && echo ✅ || echo ❌) pytest 全绿"
[ $LINT_OK = 1 ] && [ $FMT_OK = 1 ] && [ $TESTS_OK = 1 ] && [ $NAMED_OK = 1 ] && C2=PASS

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  判据 3 · 恢复演练                                       ║"
echo "╚══════════════════════════════════════════════════════════╝"
if bash scripts/backup.sh >/dev/null 2>&1 && bash scripts/restore_drill.sh 2>&1 | tail -6; then
  C3=PASS
fi

echo ""
echo "═══════════════════ P1 出口判据汇总 ═══════════════════"
printf "  判据 1  三档 tool_calls   %s\n" "$C1"
printf "  判据 2  CI 绿             %s\n" "$C2"
printf "  判据 3  恢复演练          %s\n" "$C3"
echo ""
if [ "$C1$C2$C3" = "PASSPASSPASS" ]; then
  echo "  ✅ P1 出口判据全部 PASS"
  exit 0
fi
echo "  ❌ P1 尚未出口"
exit 1
