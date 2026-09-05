#!/usr/bin/env bash
# 批量跑 V0，输出一张对照表。
#
#   ./runall.sh                     # 跑 universe.txt，年度 2025
#   ./runall.sh 2024                # 换年度
#   ./runall.sh 2025 my_list.txt    # 换样本文件
#
# 样本文件每行三列（空格分隔）: 代码 行业 名称
# 单份完整输出落在 runs/<year>/<code>.txt，便于事后翻查告警细节。
#
# 注意: 不要去掉 ir_agent 内部的限流 sleep —— 东财有 IP 频控。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

YEAR="${1:-2025}"
UNIVERSE="${2:-universe.txt}"
OUT="runs/${YEAR}"

[ -f "$UNIVERSE" ] || { echo "找不到样本文件: $UNIVERSE" >&2; exit 2; }
mkdir -p "$OUT"

printf "%-8s %-9s %-10s %-4s %-9s %-8s %-9s %s\n" \
       代码 行业 名称 码 ROE PB "PE(TTM)" 备注
fail=0

while read -r code sector name rest; do
  [ -z "${code:-}" ] && continue
  case "$code" in \#*) continue ;; esac

  python -m ir_agent "$code" --year "$YEAR" --strict > "$OUT/$code.txt" 2>&1
  rc=$?
  [ "$rc" -ne 0 ] && fail=$((fail + 1))

  YEAR="$YEAR" python3 - "$OUT/$code.txt" "$code" "$sector" "$name" "$rc" <<'PY'
import os, re, sys
path, code, sector, name, rc = sys.argv[1:6]
year = os.environ["YEAR"]
t = open(path, encoding="utf-8").read()
g = lambda p, d="—": (re.search(p, t).group(1) if re.search(p, t) else d)

note = []
if "运行失败" in t:
    note.append(re.search(r"运行失败: (.{0,50})", t).group(1))
else:
    if "因抓取不全" in t:
        note.append("⚠抓取故障")
    if "本期分歧" in t:
        note.append("⚠本期分歧")
    x = g(rf"跨源勾稽 {year}FY: (.+)")
    if x not in ("一致通过", "—"):
        note.append(x[:18])
    # 正常跳过只列前两条，避免噪声淹没真正的告警
    note += [a for a, _ in re.findall(r"      (.+?) · (未披露|口径不适用)", t)][:2]

print(f'{code:<8} {sector:<9} {name:<10} {rc:<4} '
      f'{g(r"ROE 为 ([-0-9.]+%)"):<9} {g(r"PB ([0-9.]+x)"):<8} '
      f'{g(r"PE\(TTM\) ([0-9.]+x)"):<9} {",".join(note)[:46]}')
PY
done < "$UNIVERSE"

echo
echo "完整输出: $OUT/  ·  未通过 $fail 家"
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
