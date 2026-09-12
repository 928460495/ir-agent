# -*- coding: utf-8 -*-
"""A股 DCF 模板 — 验证层。从工作簿读假设、用 Python 独立重算，再与公式引擎实跑结果比对。"""
from __future__ import annotations
import warnings, logging, math, os, re
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import numpy as np
from openpyxl import load_workbook

TOL = 1e-6

def _rowmap(ws):
    m = {}
    for r in range(1, ws.max_row+1):
        v = ws.cell(r,1).value
        if isinstance(v,str) and v.strip(): m.setdefault(v.strip(), r)
    return m

def _find(m, prefix):
    """精确匹配优先——前缀匹配会让「存货」命中「存货周转天数 DIO」。"""
    if prefix in m: return m[prefix]
    hits=[r for k,r in m.items() if k.startswith(prefix)]
    if len(hits)==1: return hits[0]
    if len(hits)>1: raise KeyError(f"「{prefix}」前缀歧义，命中 {len(hits)} 行，请用完整标签")
    raise KeyError(prefix)

_REF_RE = re.compile(
    # 「!」不设为可选——本表引用已由 _SELF_REF_RE 先行拦截；这里若把「!」设为
    # 可选，形如 "=B1+C1" 的普通公式会被表名分组贪婪吞掉 "B1+" 再回溯匹配出
    # "C1"，被误判成引用。跨表引用必须显式带「!」，因此收紧为必选。
    r"^=\s*(?:'([^']+)'|([^!'\s]+))!\$?([A-Z]+)\$?(\d+)\s*$")
_SELF_REF_RE = re.compile(r"^=\s*\$?([A-Z]+)\$?(\d+)\s*$")
_MAX_REF_DEPTH = 5
_ARITH_CELL_RE = re.compile(r"\$?([A-Z]{1,3})\$?(\d{1,7})")


class _CircularRefError(ValueError):
    """_cell_value 的引用层数守卫专用异常——是 ValueError 的子类（不改变既有
    `except ValueError` 调用方的行为），只为了在 _eval_arith 里能把它跟『解析
    失败』的普通 ValueError 区分开，见 _eval_arith 里的说明。"""


def _eval_arith(wb, sheet, expr, _depth):
    """对『仅含本表单元格引用与四则运算』的简单算式求值。

    驱动因子分解的『合成』行故意写成 Excel 公式（如 (1+C4)*(1+C5)-1 或
    0.1415+(C10+C11)/100）而非常量——这样改一个因子能顺着公式一路传导到
    2_利润表/5_DCF。但 read_inputs 的独立复算得先从这类公式里拿到一个
    浮点数：_resolve_ref 只认『整格就是一个引用』（=B48 / =1b_驱动分解!B5），
    对这种四则运算公式无能为力，会直接把公式字符串丢回去，下游 float()
    立刻炸掉。这里把公式里出现的单元格逐个换成其（可能还要跨表/递归解析
    出来的）数值，再对剩下的纯算式求值。只覆盖本项目自己生成的这类简单
    算式——不支持函数调用等更复杂形式；解析不出来就返回 None，调用方按
    老规矩原样返回公式字符串，不影响其余既有行为。
    """
    def repl(m):
        v = _cell_value(wb, sheet, f"{m.group(1)}{m.group(2)}", _depth + 1)
        return f"({float(v)})"
    try:
        text = _ARITH_CELL_RE.sub(repl, expr)
    except _CircularRefError:
        # 循环引用的诊断信息必须穿透这里，不能被下面那条兜底 except 吞掉——
        # 否则排查者看到的只是一个莫名其妙的 float() 类型错误，而不是「这里可能
        # 有循环引用」。别的 ValueError（比如 repl 里 float(v) 碰到解析不出数值
        # 的东西）仍然维持老行为：走 return None，让调用方原样吐回公式串。
        raise
    except (TypeError, ValueError):
        return None
    if not re.fullmatch(r"[0-9+\-*/().\s]+", text):
        return None          # 残留非数字字符（如函数名）——不是我们支持的算式
    if "**" in text:
        # 显式拒绝乘方：白名单按字符放行 '*'，两个相邻就拼成 Python 的乘方
        # 运算符。Excel 自己的乘方符号是 '^'（已被上面的 fullmatch 挡掉），
        # 只有手改过的工作簿才写得出 9**9**9 这种指数塔 —— 它会算出一个几亿位
        # 的大整数，把校验进程拖死。
        return None
    try:
        return float(eval(text, {"__builtins__": {}}, {}))       # noqa: S307
    except Exception:
        return None


def _resolve_ref(v):
    """把单元格里的引用式拆成 (表名 或 None, 地址)。不是引用则返回 None。

    此前只认本表引用（=B48）。接入驱动因子分解后，「1_假设」的增速与毛利率
    单元格要指向「1b_驱动分解」，不支持跨表就会让 read_inputs 拿到公式字符串
    然后 float() 抛异常 —— **独立复算验证会整个断掉**。
    """
    if not isinstance(v, str):
        return None
    m = _SELF_REF_RE.match(v.strip())
    if m:
        return None, f"{m.group(1)}{m.group(2)}"
    m = _REF_RE.match(v.strip())
    if not m:
        return None
    sheet = m.group(1) or m.group(2)
    if not sheet:
        return None
    return sheet, f"{m.group(3)}{m.group(4)}"


def _cell_value(wb, sheet, addr, _depth=0):
    """读一个单元格的值，逐层解析引用（含跨表）。"""
    if _depth > _MAX_REF_DEPTH:
        raise _CircularRefError(
            f"引用层数超过 {_MAX_REF_DEPTH} —— 可能存在循环引用")
    v = sheet[addr].value
    ref = _resolve_ref(v)
    if ref is None:
        if isinstance(v, str) and v.strip().startswith("="):
            ev = _eval_arith(wb, sheet, v.strip()[1:], _depth)
            if ev is not None:
                return ev
        return v
    name, target = ref
    nxt = wb[name] if name else sheet
    return _cell_value(wb, nxt, target, _depth + 1)


def read_inputs(path):
    wb = load_workbook(path, data_only=False)
    a = wb["1_假设"]; am = _rowmap(a)
    def g(p, _depth=0):
        """读假设值；引用（含跨表）逐层解析，避免读回公式字符串。"""
        return _cell_value(wb, a, f"B{_find(am, p)}")
    hdr = [wb["2_利润表"].cell(5,c).value for c in range(2,50)]
    hdr = [h for h in hdr if h]
    nh = sum(1 for h in hdr if h.endswith("A")); ny = len(hdr)-nh
    scen = int(g("选择情景"))
    def path3(prefix, n):
        out = []
        for i in range(n):
            r = _find(am, f"{hdr[nh+i]} {prefix}")
            col = chr(ord('A') + scen)      # scen=1→B, 2→C, 3→D
            out.append(float(_cell_value(wb, a, f"{col}{r}")))
        return out
    o = {k: float(g(k)) for k in ["货币资金","交易性金融资产","应收票据及应收账款","存货","其他流动资产",
         "固定资产净额","在建工程","其他非流动资产","短期借款","应付票据及应付账款","其他流动负债",
         "一年内到期非流动负债","长期借款","其他非流动负债","少数股东权益","归母权益","基期营业总收入",
         "总股本（亿股）","现价（元）"]}
    gen = {k: float(g(k)) for k in ["销售费用率","管理费用率","研发费用率","税金及附加率","其他收益 / 营收",
           "投资收益 / 营收","有息负债利率","现金收益率","实际所得税率","少数股东损益占比",
           "其他流动资产 / 营收","其他流动负债 / 营收","其他经营损益 / 营收","应收周转天数 DSO","存货周转天数 DIO",
           "应付周转天数 DPO","折旧率（占期初固定资产净额）","无形摊销 / 营收",
           "资本开支投向无形/长期待摊的比重","非现金减值减记中固定资产占比","归母分红率","少数股东分红率",
           "最低现金余额（亿）","现金归集阈值（亿）"]}
    dcf = {k: float(g(k)) for k in ["无风险利率 Rf","股权风险溢价 ERP","Beta（5年","债务成本 Kd",
           "债务权重 D/(D+E)","永续增长率 g","终值所得税率 t_term","期中折现开关","估值基准日滚动年数",
           "非经营资产（加回）","少数股东扣除方式","少数股东利润占比"]}
    it=None
    try:
        it=dict(sw=float(g("锚定开关")), share=float(g("已披露期占全年营收比重")),
                gm_rest=float(g("剩余期毛利率")), rev=float(g("已披露期营业总收入（实际）")),
                cogs=float(g("已披露期营业成本（实际）")))
    except KeyError:
        pass
    dg=float(g("营收增速 Δ")); dm_=float(g("毛利率 Δ"))
    try:
        fin=dict(ns=float(g("增发股数（亿股）")), px=float(g("发行价（元/股）")), fee=float(g("发行费用率")),
                 yr=int(float(g("到账年份序号（1=首个预测年）"))), ucx=float(g("投向 — 资本开支（亿元）")),
                 udb=float(g("投向 — 偿还债务（亿元）")), by=int(float(g("募投资本开支建设期（年）"))),
                 disc=float(g("桥接折现年数（0=视同已完成）")),
                 bbr=float(g("年度回购金额 / 营收")), bbp=float(g("回购均价（元/股）")))
        fin["net"]=fin["ns"]*fin["px"]*(1-fin["fee"])      # 公式格，独立重算
    except KeyError:
        fin=dict(ns=0.0,px=0.0,fee=0.0,yr=1,ucx=0.0,udb=0.0,by=1,disc=0.0,net=0.0)
    return dict(hdr=hdr, nh=nh, ny=ny, o=o, gen=gen, dcf=dcf, it=it, dg=dg, dm=dm_, fin=fin,
                growth=[x+dg for x in path3("营收增速",ny)],
                gm=[x+dm_ for x in path3("毛利率",ny)],
                capex=path3("资本开支 / 营收",ny))

def recompute(inp):
    o=dict(); src=inp["o"]; G=inp["gen"]; ny=inp["ny"]; F=inp["fin"]
    for a,b in [("cash","货币资金"),("tfa","交易性金融资产"),("ar","应收票据及应收账款"),("inv","存货"),
                ("oca","其他流动资产"),("ppe","固定资产净额"),("cip","在建工程"),("onca","其他非流动资产"),
                ("st","短期借款"),("ap","应付票据及应付账款"),("ocl","其他流动负债"),
                ("cur","一年内到期非流动负债"),("lt","长期借款"),("oncl","其他非流动负债"),
                ("mi","少数股东权益"),("eq","归母权益")]:
        o[a]=src[b]
    rev=src["基期营业总收入"]; t=G["实际所得税率"]; rows=[]
    IT=inp.get("it")
    growth=list(inp["growth"]); gmp=list(inp["gm"])
    if IT and IT["sw"]==1:
        # 锚定：首年生效增速/毛利率由已披露中期反推（与「1_假设」生效值列一致）
        fy_rev=IT["rev"]/IT["share"]
        fy_cogs=IT["cogs"]+(fy_rev-IT["rev"])*(1-IT["gm_rest"])
        growth[0]=fy_rev/src["基期营业总收入"]-1
        gmp[0]=1-fy_cogs/fy_rev
    for i in range(ny):
        rev=rev*(1+growth[i]); cogs=rev*(1-gmp[i])
        fin=(o["st"]+o["cur"]+o["lt"])*G["有息负债利率"]-o["cash"]*G["现金收益率"]
        op=(rev-cogs)-rev*(G["税金及附加率"]+G["销售费用率"]+G["管理费用率"]+G["研发费用率"]) \
           +rev*G["其他收益 / 营收"]+rev*G["其他经营损益 / 营收"]-fin
        ni=op*(1-t); mi=ni*G["少数股东损益占比"]; nip=ni-mi
        ar=rev*G["应收周转天数 DSO"]/365; iv=cogs*G["存货周转天数 DIO"]/365; oca=rev*G["其他流动资产 / 营收"]
        ap=cogs*G["应付周转天数 DPO"]/365; ocl=rev*G["其他流动负债 / 营收"]
        dep=o["ppe"]*G["折旧率（占期初固定资产净额）"]
        amt=rev*G["无形摊销 / 营收"]    # 折旧减记固定资产，摊销减记其他非流动资产 —— 分子分母口径这才对得上
        fcx=(F["ucx"]/F["by"]) if (F["by"]>0 and F["yr"]<=i+1<=F["yr"]+F["by"]-1) else 0.0
        capex=rev*inp["capex"][i]+fcx
        _cxi=G["资本开支投向无形/长期待摊的比重"]        # 资本开支按去向拆分：非无形部分进固定资产，无形部分进其他非流动资产
        _ncx=-rev*G["其他经营损益 / 营收"]; _ips=G["非现金减值减记中固定资产占比"]   # 减值按期初余额比例分摊减记
        ppe=o["ppe"]+capex*(1-_cxi)-dep-_ncx*_ips
        onca=o["onca"]+capex*_cxi-amt-_ncx*(1-_ips)
        dwc=-(ar-o["ar"])-(iv-o["inv"])-(oca-o["oca"])+(ap-o["ap"])+(ocl-o["ocl"])
        # 非现金其他经营损益（减值/公允价值变动/资产处置）已在 EBIT 中扣除但不耗现金，
        # 按全额加回 —— 不加回等于把同一笔成本算两次（资本开支一次、减值一次）。
        ncx=-rev*G["其他经营损益 / 营收"]
        ocf=ni+dep+amt+dwc+ncx; ebit=op+fin
        fcff=ebit*(1-t)+dep+amt-capex+dwc+ncx
        div=nip*G["归母分红率"]; midiv=mi*G["少数股东分红率"]
        # 融资：募资流入 → 先还短贷（以期初余额封顶）→ 余下还长贷（同样封顶）
        prc = F["net"] if F["yr"]==i+1 else 0.0
        want = F["udb"] if F["yr"]==i+1 else 0.0
        rst = min(want, o["st"]); rlt = min(max(want-o["st"], 0.0), o["lt"])
        bb = -rev*F["bbr"]                                  # 回购：负数=流出
        bb_sh = (-bb/F["bbp"]) if F["bbp"] > 0 else 0.0
        cum_bb = o.get("cum_bb", 0.0) + bb_sh
        pre=o["cash"]+ocf-capex-div-midiv+prc-rst-rlt+bb
        mc,sw=G["最低现金余额（亿）"],G["现金归集阈值（亿）"]
        st_av=o["st"]-rst        # 现金归集只能归还【定向偿还之后】仍存续的短贷
        rv=(mc-pre) if pre<mc else (-min(st_av,pre-(mc+sw)) if pre>mc+sw and st_av>0 else 0.0)
        cash=pre+rv; st=o["st"]+rv-rst; lt=o["lt"]-rlt
        eq=o["eq"]+nip-div+prc+bb; mie=o["mi"]+mi-midiv
        shr=src["总股本（亿股）"]+(F["ns"] if F["yr"]<=i+1 else 0.0)-cum_bb
        ta=cash+o["tfa"]+ar+iv+oca+ppe+o["cip"]+onca
        tl=st+ap+ocl+o["cur"]+lt+o["oncl"]
        rows.append(dict(rev=rev,cogs=cogs,op=op,ni=ni,mi=mi,nip=nip,eps=nip/shr,
            ebit=ebit,dep=dep,amt=amt,da=dep+amt,ncx=ncx,onca=onca,capex=capex,fcff=fcff,ocf=ocf,cash=cash,ar=ar,inv=iv,oca=oca,ppe=ppe,
            ap=ap,ocl=ocl,st=st,lt=lt,ta=ta,tl=tl,eq=eq,mie=mie,te=eq+mie,check=ta-tl-eq-mie,
            nwc=ar+iv+oca-ap-ocl, fin_net=prc, rep_st=-rst, rep_lt=-rlt,
            bb=bb, cum_bb=cum_bb))
        o.update(cash=cash,ar=ar,inv=iv,oca=oca,ppe=ppe,onca=onca,st=st,lt=lt,ap=ap,ocl=ocl,eq=eq,mi=mie,
                 cum_bb=cum_bb)
    D=inp["dcf"]
    ke=D["无风险利率 Rf"]+D["Beta（5年"]*D["股权风险溢价 ERP"]
    w=(1-D["债务权重 D/(D+E)"])*ke+D["债务权重 D/(D+E)"]*D["债务成本 Kd"]*(1-t)
    gt=D["永续增长率 g"]; tt=D["终值所得税率 t_term"]; my=D["期中折现开关"]
    ic=rows[-1]["nwc"]+rows[-1]["ppe"]
    tebit=rows[-1]["ebit"]+rows[-1]["ncx"]      # 正常化：终值年 EBIT 同样含非现金减值
    roic=tebit*(1-tt)/ic; rr=gt/roic
    tf=tebit*(1+gt)*(1-tt)*(1-rr); tv=tf/(w-gt)
    pv=sum(rows[i]["fcff"]/(1+w)**(i+1-0.5*my) for i in range(ny))
    pvtv=tv/(1+w)**(ny-0.5*my); ev0=pv+pvtv
    rl=D["估值基准日滚动年数"]
    ev=ev0*(1+w)**rl - rows[0]["fcff"]*rl*(1+w)**(rl/2)   # 扣除期间已实现 FCFF
    nd=src["短期借款"]+src["一年内到期非流动负债"]+src["长期借款"]-src["货币资金"]-src["交易性金融资产"]
    fin_pv=F["net"]/(1+w)**F["disc"]      # 净负债按基期计量 → 募资现金对 EV 不可见，必须单列
    gx=ev-nd+D["非经营资产（加回）"]+fin_pv
    mi_ded=src["少数股东权益"] if D["少数股东扣除方式"]==1 else gx*D["少数股东利润占比"]
    eqv=gx-mi_ded; ps=eqv/(src["总股本（亿股）"]+F["ns"])
    return rows, dict(wacc=w,ke=ke,ic=ic,roic=roic,rr=rr,tf=tf,tv=tv,pv=pv,pvtv=pvtv,
                      ev0=ev0,ev=ev,nd=nd,gx=gx,mi=mi_ded,eq=eqv,ps=ps,fin_pv=fin_pv)

def verify_all_scenarios(path, quiet=False):
    """三档情景全部验算——read_inputs 只读 B6 指向的那一档，Bull/Bear 原本从未被覆盖。"""
    import shutil, tempfile
    from openpyxl import load_workbook as _lw
    allf=[]
    wb0=_lw(path); a0=wb0["1_假设"]; am=_rowmap(a0); sr=_find(am,"选择情景")
    cur=int(a0.cell(sr,2).value or 2)
    for sc,nm in [(1,"Bull"),(2,"Base"),(3,"Bear")]:
        if sc==cur:
            f=path
        else:
            f=os.path.join(tempfile.gettempdir(), f"_sc{sc}_"+os.path.basename(path))
            shutil.copy(path,f); w=_lw(f); w["1_假设"].cell(sr,2,sc); w.save(f)
        fails,_=verify(f, quiet=True)
        if sc!=cur:
            try: os.remove(f)
            except OSError: pass
        allf += [(nm,)+x for x in fails]
    if not quiet:
        print(f"三情景验算：{'✅ 全部通过' if not allf else f'❌ {len(allf)} 项失败'}")
        for x in allf[:10]: print("   ✗",x)
    return allf


def verify(path, quiet=False):
    if "%" in os.path.basename(path):
        raise ValueError(
            f"文件名含 % 字符：{os.path.basename(path)}\n"
            "formulas 引擎会把文件名塞进 %-格式模板，% 后面跟中文会抛出\n"
            "「unsupported format character」这种毫无线索的错误。请改名后重试。")
    import formulas
    inp=read_inputs(path); rows,dv=recompute(inp)
    nh,ny=inp["nh"],inp["ny"]
    from openpyxl.utils import get_column_letter as CL
    CC=[CL(2+nh+i) for i in range(ny)]
    fails=[]
    wb=load_workbook(path)
    hdr=[wb["2_利润表"].cell(5,c).value for c in range(2,2+nh+ny)]
    if len([h for h in hdr if h])!=nh+ny: fails.append(("列头","不完整",hdr,None))
    if not all(str(h).endswith("E") for h in hdr[nh:]): fails.append(("预测列头",hdr[nh:],"应全部以E结尾",None))
    sol=formulas.ExcelModel().loads(path).finish().calculate()
    _fn=os.path.basename(path)
    def G(sh,cell):
        v=sol.get(f"'[{_fn}]{sh}'!{cell}")
        if v is None: return None
        try: return float(v.value[0,0])
        except Exception: return None
    def chk(name,sh,cell,ref):
        x=G(sh,cell)
        if x is None or (isinstance(x,float) and math.isnan(x)):
            fails.append((name,cell,"空/NaN",ref)); return
        if abs(x-ref)>TOL: fails.append((name,cell,round(x,6),round(float(ref),6)))
    for nm,sh,rw,key in [("营收","2_利润表",6,"rev"),("营业成本","2_利润表",8,"cogs"),("营业利润","2_利润表",18,"op"),
      ("净利润","2_利润表",21,"ni"),("归母净利","2_利润表",23,"nip"),("EPS","2_利润表",24,"eps"),("EBIT","2_利润表",27,"ebit"),
      ("现金","3_资产负债表",6,"cash"),("应收","3_资产负债表",8,"ar"),("存货","3_资产负债表",9,"inv"),
      ("固定资产","3_资产负债表",12,"ppe"),("资产总计","3_资产负债表",16,"ta"),("负债合计","3_资产负债表",24,"tl"),
      ("权益合计","3_资产负债表",27,"te"),("NWC","3_资产负债表",31,"nwc"),("折旧摊销合计","4_现金流量表",7,"da"),("折旧","4_现金流量表",29,"dep"),
      ("摊销","4_现金流量表",30,"amt"),("其他非流动资产","3_资产负债表",14,"onca"),
      ("股份回购","4_现金流量表",31,"bb"),("累计回购股数","4_现金流量表",32,"cum_bb"),
      ("经营现金流","4_现金流量表",13,"ocf"),("期末现金","4_现金流量表",23,"cash"),("FCFF","4_现金流量表",25,"fcff"),
      ("短期借款","3_资产负债表",18,"st"),("长期借款","3_资产负债表",22,"lt"),
      ("非现金其他经营损益","4_现金流量表",33,"ncx"),
      ("增发募资净额","4_现金流量表",26,"fin_net"),("偿还短贷","4_现金流量表",27,"rep_st"),
      ("偿还长贷","4_现金流量表",28,"rep_lt")]:
        for i,c in enumerate(CC): chk(nm,sh,f"{c}{rw}",rows[i][key])
    for i,c in enumerate(CC): chk("平衡校验","3_资产负债表",f"{c}29",0.0)
    dm=_rowmap(wb["5_DCF"])
    for pfx,ref in [("WACC",dv["wacc"]),("股权成本 Ke",dv["ke"]),("投入资本 IC",dv["ic"]),
                    ("终值 ROIC",dv["roic"]),("稳态再投资率",dv["rr"]),("终值 FCFF",dv["tf"]),
                    ("终值 TV",dv["tv"]),("明确期 FCFF 现值合计",dv["pv"]),("终值现值 PV(TV)",dv["pvtv"]),
                    ("企业价值 EV（滚动",dv["ev"]),("加：增发募资净额",dv["fin_pv"]),
                    ("归属全体股东权益价值",dv["gx"]),
                    ("减：少数股东权益",dv["mi"]),("归母股权价值",dv["eq"]),("每股内在价值",dv["ps"])]:
        try: chk(pfx,"5_DCF",f"B{_find(dm,pfx)}",ref)
        except KeyError: fails.append((pfx,"未找到行",None,ref))
    fr=_find(dm,"FCFF")
    for i in range(ny): chk("DCF-FCFF","5_DCF",f"{CL(2+i)}{fr}",rows[i]["fcff"])
    # 假设「生效值」必须等于利润表实际使用的增速/毛利率（防止假设改了报表不动）
    am=_rowmap(wb["1_假设"]); hdr_all=[wb["2_利润表"].cell(5,c).value for c in range(2,2+nh+ny)]
    for i,c in enumerate(CC):
        yl=hdr_all[nh+i]
        for pfx,strow in ((f"{yl} 营收增速",7),(f"{yl} 毛利率",10)):
            try: er=_find(am,pfx)
            except KeyError: continue
            eff=G("1_假设",f"E{er}"); used=G("2_利润表",f"{c}{strow}")
            if eff is None or used is None or math.isnan(eff) or math.isnan(used):
                fails.append((f"生效值联动 {pfx}",f"E{er}","空/NaN",None))
            elif abs(eff-used)>TOL:
                fails.append((f"生效值联动 {pfx}",f"E{er} vs 2_利润表!{c}{strow}",round(eff,8),round(used,8)))
    # 杜邦恒等式：连乘必须等于直接计算的 ROE（三因子 / 五因子各一道）
    if "7_财务比率与杜邦" in wb.sheetnames:
        _wr=wb["7_财务比率与杜邦"]; rrm=_rowmap(_wr)
        from openpyxl.utils import get_column_letter as _CL
        for pfx in ("★ 三因子恒等式校验","★ 五因子恒等式校验"):
            try: rr=_find(rrm,pfx)
            except KeyError:
                fails.append((pfx,"未找到行",None,0.0)); continue
            found=False
            for i in range(nh+ny):
                cc=_CL(2+i); x=G("7_财务比率与杜邦",f"{cc}{rr}")
                if x is None or (isinstance(x,float) and math.isnan(x)): continue
                found=True
                if abs(x)>1e-8: fails.append((pfx,f"{cc}{rr}",round(x,10),0.0))
            if not found: fails.append((pfx,"全列为空",None,0.0))
    # 敏感性中心格自检：Δ=0 / 生效WACC / 生效g 必须复现基准每股价值
    base_ps=G("5_DCF",f"B{_find(dm,'每股内在价值')}")
    _ws5=wb["5_DCF"]
    for k in [x for x in dm if x.startswith("模块") or x.startswith("六、敏感性")]:
        # 按内容定位：标题行之后第一个 A 列为数值的行即首个数据行（模块表有小注行，第六节没有，
        # 用「表头+固定偏移」会错行——这正是第六节自检长期指错单元格的原因）
        first=None
        for rr in range(dm[k]+1, dm[k]+8):
            av=_ws5.cell(rr,1).value
            if isinstance(av,(int,float)) or (isinstance(av,str) and av.startswith("=")):
                first=rr; break
        if first is None:
            fails.append((k[:14]+" 中心格","定位失败",None,None)); continue
        ctr=first+2
        v=G("5_DCF",f"D{ctr}")
        if v is None or (isinstance(v,float) and math.isnan(v)):
            fails.append((k[:14]+" 中心格","D%d"%ctr,"空/NaN",base_ps))
        elif base_ps is None or abs(v-base_ps)>TOL:
            fails.append((k[:14]+" 中心格","D%d"%ctr,round(v,8),round(base_ps,8)))
    if not quiet:
        print(f"验证：{'✅ 全部通过' if not fails else f'❌ {len(fails)} 项失败'}"
              f"（列头结构 + NaN 检测 + {len(CC)*24+ny+15} 格独立复算）")
        for f in fails[:12]: print("   ✗", f)
    return fails, dv

# 预测期内不应为负的资产负债表科目（键 -> 中文名）。
# 平衡校验只保证「资产 = 负债 + 权益」，不保证每一项都 ≥ 0 ——
# 摊销把「其他非流动资产」单向抽干到负数时，三道防线一道都不会响（中免 10 年耗尽 98.5%）。
NONNEG_ITEMS=[("cash","货币资金"),("ar","应收票据及应收账款"),("inv","存货"),
              ("oca","其他流动资产"),("ppe","固定资产净额"),("onca","其他非流动资产"),
              ("st","短期借款"),("lt","长期借款"),("ap","应付票据及应付账款"),("ocl","其他流动负债")]
DRAIN_WARN=0.20        # 末年余额低于期初的 20% 即提示：再延长明确期就会变负
# 「被抽干」预警的豁免项：这些科目有【刻意设计的归零机制】且已在公式里以 0 封底 ——
#   短期借款 = 现金归集主动偿还；长期借款 = 募资定向偿还。
# 它们归零是模型在正确工作，不是缺陷。负值检查仍然覆盖它们（封底失效就该报错）。
DRAIN_EXEMPT={"st","lt"}


CX_INTAN_WARN=0.20      # 资本开支流向无形的比重超过此值即提示口径风险


def assumption_warnings(inp):
    """假设层面的口径风险：不是算错，而是默认值在特定公司身上会失真。"""
    w=[]; G=inp["gen"]
    cxi=G.get("资本开支投向无形/长期待摊的比重")
    if cxi is not None and cxi > CX_INTAN_WARN:
        w.append(f"资本开支有 {cxi*100:.0f}% 被分流到无形/长期待摊资产（默认按 申报折旧:申报摊销 推导）。"
                 f"若该公司的摊销主体是【并购形成】的商誉/客户关系/技术，那类资产会自然跑完、"
                 f"并不由有机资本开支重置 —— 此时该比重应手工调低，否则固定资产被压平、"
                 f"投入资本偏低、终值 ROIC 被高估")
    return w


def balance_sheet_warnings(inp, rows):
    """逐科目扫描预测期：出现负值 → 警告；单调抽干至期初 20% 以下 → 提前预警。"""
    w=[]; src=inp["o"]
    OPEN={"cash":"货币资金","ar":"应收票据及应收账款","inv":"存货","oca":"其他流动资产",
          "ppe":"固定资产净额","onca":"其他非流动资产","st":"短期借款","lt":"长期借款",
          "ap":"应付票据及应付账款","ocl":"其他流动负债"}
    for key,name in NONNEG_ITEMS:
        vals=[r.get(key) for r in rows]
        if any(v is None for v in vals): continue
        neg=[i+1 for i,v in enumerate(vals) if v < 0]
        if neg:
            w.append(f"「{name}」在第 {neg[0]} 个预测年变为负数（{vals[neg[0]-1]:.2f} 亿）："
                     f"资产负债表仍然配平，但该科目在会计上不可能为负 —— 驱动它的假设有结构性缺陷")
            continue
        if key in DRAIN_EXEMPT: continue
        op=src.get(OPEN.get(key))
        if not (op and op > 1.0): continue
        mono = all(vals[i] < vals[i-1] for i in range(1,len(vals))) and vals[0] < op
        # 两条触发线：① 快见底（<20%）；② 全程单调下行且已腰斩 —— 后者能在真正见底前
        # 就抓到「只出不进」的形态（中免 10 年 −73%，按 20% 阈值要到第 12 年才报）。
        if vals[-1] < op*DRAIN_WARN or (mono and vals[-1] < op*0.50):
            yrs = len(vals)
            rate = (op-vals[-1])/yrs
            left = (vals[-1]/rate) if rate > 0 else float("inf")
            w.append(f"「{name}」被单向抽干：期初 {op:.1f} 亿 → 第 {yrs} 年 {vals[-1]:.1f} 亿"
                     f"（剩 {vals[-1]/op:.0%}）"
                     + (f"，按此速度约 {left:.0f} 年后变负" if left < 50 else ""))
    return w


def sanity(dv, rows, wacc_key="wacc"):
    """健全性警告：不判失败，但必须提示——这些形态下 DCF 结果不可直接采信。"""
    w=[]
    _tw=dv["pvtv"]/dv["ev0"]
    # 阈值从 90% 降到 75%：README 自己写的就是「>75% 说明估值高度依赖终值假设」，
    # 90% 的门槛实际上等于不报警（均胜 81% + EBIT 逐年下滑，原先一条都不响）。
    if _tw > 0.90:
        w.append(f"终值占 EV {_tw*100:.0f}%（>90%）：估值几乎全部来自终值假设，明确期现金流贡献极小甚至为负")
    elif _tw > 0.75:
        w.append(f"终值占 EV {_tw*100:.0f}%（>75%）：估值高度依赖终值假设，明确期只贡献 {(1-_tw)*100:.0f}%")
    if dv["pv"] < 0:
        w.append(f"明确期 FCFF 现值为负（{dv['pv']:.1f} 亿）：预测期内公司净消耗现金，DCF 点估值意义有限")
    if dv["roic"] <= dv[wacc_key]:
        w.append(f"终值 ROIC {dv['roic']*100:.1f}% ≤ WACC {dv[wacc_key]*100:.1f}%：永续期不创造价值，增长反而毁灭价值，终值公式在此失真")
    if dv["rr"] > 0.60:
        w.append(f"稳态再投资率 {dv['rr']*100:.0f}%（>60%）：ROIC 偏低导致维持增长需吞掉大部分利润")
    if dv["rr"] < 0:
        w.append("稳态再投资率为负：ROIC 与永续增速符号矛盾，请检查终值年 EBIT")
    # 营收在涨而 EBIT 在跌 —— 几乎总是毛利率均值回归默认值造成的，而非经营判断。
    # 均胜电子实测：营收 583→689，EBIT 36.9→21.4（−42%），因为毛利率被推回 6 年均值，
    # 而实际趋势是 11.6%→18.3% 单边上行。这一项单独改动会让每股从 ¥15.41 变 ¥44.47。
    _d=(rows[-1]["ebit"]/rows[0]["ebit"]-1) if (rows and rows[0]["ebit"]) else 0.0
    # 设 10% 的重要性下限：轻微下滑（中免 −6%）是噪音，会淹没真正要命的那种（均胜 −42%）
    if len(rows) >= 2 and rows[-1]["rev"] > rows[0]["rev"] and _d < -0.10:
        w.append(f"营收增长而 EBIT 下滑（{rows[0]['ebit']:.1f} → {rows[-1]['ebit']:.1f}，{_d*100:+.0f}%）："
                 f"通常是毛利率被默认的均值回归路径压低所致。若公司正处于盈利能力上行期，"
                 f"请手工改「毛利率」判断项（黄底），否则估值会被系统性低估")
    neg=[i+1 for i,r in enumerate(rows) if r["ebit"] <= 0]
    if neg: w.append(f"第 {neg} 个预测年 EBIT ≤ 0：亏损期公司不适合用 FCFF-DCF")
    if dv["eq"] <= 0:
        w.append("股权价值为负：净负债+少数股东权益已超过企业价值")
    return w
