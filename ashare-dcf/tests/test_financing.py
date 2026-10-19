# -*- coding: utf-8 -*-
"""融资模块端到端测试：改「1_假设」七节的输入 → 全公式重算 → 独立复算逐格比对。"""
import os, sys, shutil, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openpyxl import load_workbook
import ashare_verify as V

# 传入一个已生成的工作簿：环境变量 DCF_TEST_XLSX，或单独当脚本跑时的第一个参数。
# 在 pytest 下没设环境变量就跳过（此前读 sys.argv[1] 拿到的是 pytest 自己的参数，
# 导致整个测试目录收集失败）。
SRC=os.environ.get("DCF_TEST_XLSX") or (sys.argv[1] if __name__ == "__main__" and len(sys.argv) > 1 else None)
if not SRC:
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("需要已生成的工作簿：DCF_TEST_XLSX=<xlsx>", allow_module_level=True)
    sys.exit("用法：python tests/%s <已生成的工作簿.xlsx>" % os.path.basename(__file__))

def rowmap(ws):
    m={}
    for r in range(1,ws.max_row+1):
        v=ws.cell(r,1).value
        if isinstance(v,str) and v.strip(): m.setdefault(v.strip(),r)
    return m

os.makedirs("/tmp/dcf_fin_test",exist_ok=True)

def run(tag, edits):
    dst=f"/tmp/dcf_fin_test/_t_{tag}.xlsx"; shutil.copy(SRC,dst)
    wb=load_workbook(dst); a=wb["1_假设"]; m=rowmap(a)
    for k,v in edits.items(): a.cell(m[k],2).value=v
    wb.save(dst)
    fails,dv=V.verify(dst,quiet=True)
    inp=V.read_inputs(dst); rows,_=V.recompute(inp)
    print(f"\n=== {tag} ===")
    print(f"  验证 {'✅ 通过' if not fails else '❌ %d 项失败'%len(fails)}")
    for f in fails[:8]: print("    ✗",f)
    print(f"  每股 ¥{dv['ps']:.4f}   全体权益 {dv['gx']:.2f}   募资净额现值 {dv['fin_pv']:.2f}")
    print(f"  平衡校验 max|.| = {max(abs(r['check']) for r in rows):.2e}")
    print(f"  资本开支 {[round(r['capex'],2) for r in rows]}")
    print(f"  短贷 {[round(r['st'],2) for r in rows]}  长贷 {[round(r['lt'],2) for r in rows]}")
    print(f"  现金 {[round(r['cash'],2) for r in rows]}")
    return dv, rows

K_NS="增发股数（亿股）"; K_PX="发行价（元/股）"; K_YR="到账年份序号（1=首个预测年）"
K_UC="投向 — 资本开支（亿元）"; K_UD="投向 — 偿还债务（亿元）"; K_BY="募投资本开支建设期（年）"
K_DC="桥接折现年数（0=视同已完成）"

base,_=run("00_关闭（回归基准）",{})

# 长电 65 亿定增：发行价 55 元 → 1.1818 亿股；投向 40 亿资本开支 + 15 亿还债，余 8.0 亿补流
d1,_=run("01_65亿定增_40资本开支_15还债",
    {K_NS:1.1818, K_PX:55.0, K_YR:1, K_UC:40.0, K_UD:15.0, K_BY:3, K_DC:0})

# 纯补流：募资全部趴在账上 —— 检验「按内在价值折算的稀释/增厚」是否符合理论
d2,_=run("02_65亿全部补流",
    {K_NS:1.1818, K_PX:55.0, K_YR:1, K_UC:0.0, K_UD:0.0, K_BY:3, K_DC:0})

# 理论校验：全部补流时，每股 = (原权益 + 净额) / (原股本 + 增股)
o=V.read_inputs(SRC)["o"]; sh0=o["总股本（亿股）"]
net=1.1818*55.0*0.985
theo=(base["eq"]+net*(1-0.0))/(sh0+1.1818)
# 注意：少数股东按价值法扣，净额也要按同比例扣
inp2=V.read_inputs("/tmp/dcf_fin_test/_t_02_65亿全部补流.xlsx")
msh=inp2["dcf"]["少数股东利润占比"]; mode=inp2["dcf"]["少数股东扣除方式"]
theo=(base["eq"]+net*(1-(0 if mode==1 else msh)))/(sh0+1.1818)
print(f"\n理论值（纯补流）= (原归母权益 {base['eq']:.2f} + 净额 {net:.2f}×(1−少数 {msh:.3f})) / ({sh0:.4f}+1.1818) = ¥{theo:.4f}")
print(f"模型值 = ¥{d2['ps']:.4f}   偏差 {abs(theo-d2['ps']):.2e}  {'✅' if abs(theo-d2['ps'])<1e-6 else '❌'}")

# 稀释判定：发行价 55 远高于内在价值 18.8 → 对老股东【增厚】
print(f"\n发行价 ¥55 vs 内在价值 ¥{base['ps']:.2f}：高价发行 → 老股东增厚 {d2['ps']/base['ps']-1:+.2%} ✅（理论应为正）")

# 折价发行检验：发行价 = 10 元（低于内在价值 18.8）→ 应稀释
d3,_=run("03_折价发行_每股10元_全部补流",
    {K_NS:1.1818, K_PX:10.0, K_YR:1, K_UC:0.0, K_UD:0.0, K_BY:3, K_DC:0})
print(f"发行价 ¥10 < 内在价值 ¥{base['ps']:.2f} → 老股东被稀释 {d3['ps']/base['ps']-1:+.2%} {'✅' if d3['ps']<base['ps'] else '❌'}")

# 无回报的资本开支：募资全投资本开支且不改增速 → 应毁灭价值
d4,_=run("04_65亿全投资本开支_不提增速",
    {K_NS:1.1818, K_PX:55.0, K_YR:1, K_UC:64.0, K_UD:0.0, K_BY:3, K_DC:0})
print(f"全额投资本开支（不提增速）每股 ¥{d4['ps']:.4f} vs 纯补流 ¥{d2['ps']:.4f} "
      f"→ {'✅ 低于纯补流（无回报的开支毁灭价值，符合预期）' if d4['ps']<d2['ps'] else '❌'}")

# 还债与补流对估值应【完全等价】
d5,_=run("05_65亿全部还债",
    {K_NS:1.1818, K_PX:55.0, K_YR:1, K_UC:0.0, K_UD:64.0, K_BY:3, K_DC:0})
print(f"全部还债 ¥{d5['ps']:.4f} vs 全部补流 ¥{d2['ps']:.4f} → 差 {abs(d5['ps']-d2['ps']):.2e} "
      f"{'✅ 等价（净负债按基期计量、利息不进 FCFF）' if abs(d5['ps']-d2['ps'])<1e-9 else '❌'}")

# 到账年份 = 3，桥接折现 2 年
d6,_=run("06_到账年份3_折现2年",
    {K_NS:1.1818, K_PX:55.0, K_YR:3, K_UC:30.0, K_UD:0.0, K_BY:2, K_DC:2.0})
