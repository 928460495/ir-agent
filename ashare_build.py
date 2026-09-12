# -*- coding: utf-8 -*-
"""A股 DCF 模板 — 工作簿生成层。只认 calibrate() 的输出，不知道数据从哪来。"""
from __future__ import annotations
import numpy as np, pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter as CL
from openpyxl.chart import LineChart, BarChart, Reference, Series
from openpyxl.chart.title import Title
from openpyxl.chart.text import RichText, Text
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.marker import Marker
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import (Paragraph, ParagraphProperties, CharacterProperties,
                                   RegularTextRun)
import ashare_data as D

NAVY="1F4E79"; LBLUE="D9E1F2"; GREY="F2F2F2"; YEL="FFF2CC"; ORG="FCE4D6"
F_HDR=Font(color="FFFFFF",bold=True,size=11); F_COL=Font(bold=True,size=10)
F_IN=Font(color="0000FF",size=10); F_FX=Font(color="000000",size=10)
F_LNK=Font(color="008000",size=10); F_H=Font(size=10,italic=True,color="404040")
P_N=PatternFill("solid",fgColor=NAVY); P_B=PatternFill("solid",fgColor=LBLUE)
P_G=PatternFill("solid",fgColor=GREY); P_Y=PatternFill("solid",fgColor=YEL); P_O=PatternFill("solid",fgColor=ORG)
BX=Border(bottom=Side(style="thin",color="BFBFBF"))
NUM="#,##0.00"; PCT="0.0%"; PCT2="0.00%"; CNY="¥#,##0.00"; MX="0.00\"x\""; Z="0.00000000"; D1="0.0"

def build(cal: dict, path: str) -> dict:
    M, PR, O, GEN, JU, DC = cal["meta"], cal["periods"], cal["open"], cal["gen"], cal["judg"], cal["dcf"]
    DA_SRC = cal.get("da_src") or "未标注"
    FIN = cal.get("fin") or dict(new_shares=0.0, issue_price=float(M["price"]), fee=.015,
                                 year=1, use_capex=0.0, use_debt=0.0, build_years=3, disc_years=0.0)
    IS, BS, CF = cal["stmts"]["income"], cal["stmts"]["balance"], cal["stmts"]["cashflow"]
    HP, HL, YE = PR["all"], PR["labels"], PR["proj"]
    NH, NY = len(HL), len(YE)
    HC = [CL(2+i) for i in range(NH)]                 # 历史列
    CC = [CL(2+NH+i) for i in range(NY)]              # 预测列
    BASE = HC[len(PR["annual"])-1]                    # 最近年报列 = 预测基数
    def prevc(c):
        i = CC.index(c)
        return BASE if i == 0 else CC[i-1]
    A="'1_假设'"; P="'2_利润表'"; B_="'3_资产负债表'"; K="'4_现金流量表'"
    wb = Workbook()

    # ================= 1_假设 =================
    ws = wb.active; ws.title="1_假设"; ws.sheet_properties.tabColor=NAVY
    ws.column_dimensions["A"].width=36
    for c in "BCDE": ws.column_dimensions[c].width=14
    ws.column_dimensions["F"].width=58
    ws["A1"]=f"{M['name']}（{M['code']}）— 三表预测 + DCF 全公式模型"
    ws["A1"].font=Font(bold=True,size=15,color=NAVY)
    ws["A2"]=(f"CAS 口径 ｜ 单位 亿元 ｜ 生成 {M['asof']} ｜ 行情源 {M['quote_source']} "
              f"｜ 财报源 新浪 stock_financial_report_sina")
    ws["A2"].font=Font(size=9,italic=True)
    ws["A3"]="蓝色=可编辑输入　黑色=公式　绿色=跨表链接　黄底=判断项（必须自行复核）"
    ws["A3"].font=Font(size=10,color="C00000",bold=True)
    def sec(r,t,span=6):
        ws.cell(r,1,t).font=F_HDR
        for c in range(1,span+1): ws.cell(r,c).fill=P_N
        return r+1
    r=5; r=sec(r,"一、情景开关")
    SCEN=r
    ws.cell(r,1,"选择情景  1=Bull乐观 / 2=Base中性 / 3=Bear悲观").font=Font(bold=True,size=10)
    c=ws.cell(r,2,2); c.font=F_IN; c.fill=P_O; c.number_format="0"
    ws.cell(r,6,"◀ 改这一格切换全模型情景").font=Font(size=10,color="C00000",bold=True); r+=1
    ws.cell(r,1,"当前情景").font=Font(bold=True,size=10)
    ws.cell(r,2,f'=CHOOSE(B{SCEN},"Bull 乐观","Base 中性","Bear 悲观")').font=F_LNK; r+=2

    r=sec(r,"二、判断项 —— 模板不做外推，三档初始同值，请自行拉开")
    for i,t in enumerate(["假设项","Bull","Base","Bear","生效值"]):
        cc=ws.cell(r,i+1,t); cc.font=F_COL; cc.fill=P_B; cc.alignment=Alignment(horizontal="center")
    ws.cell(r,6,"历史参考").font=F_COL; ws.cell(r,6).fill=P_B; r+=1
    gm_, gs_ = JU["gm_stats"], JU["g_stats"]

    def _scen(key, i, fallback):
        """三档取值。上游（ir-agent 的 assume.py）给出 *_scen 时按档分列，
        否则沿用单列值填满三档 —— 模板原本就是三档同值并提示自行拉开。
        顺序与列一致: B=Bull, C=Base, D=Bear。"""
        sc = JU.get(key)
        if not sc:
            return [fallback] * 3
        return [sc["bull"][i], sc["base"][i], sc["bear"][i]]
    G_ROW=r
    for i,y in enumerate(YE):
        ws.cell(r,1,f"{y} 营收增速").font=Font(size=10); ws.cell(r,1).border=BX
        _v3=_scen("growth_scen", i, JU["growth"][i])
        for j in range(3):
            cc=ws.cell(r,2+j,_v3[j]); cc.font=F_IN; cc.number_format=PCT; cc.fill=P_Y
        cc=ws.cell(r,5,f"=CHOOSE($B${SCEN},B{r},C{r},D{r})"); cc.font=F_FX; cc.number_format=PCT
        if i==0:
            ws.cell(r,6,f"近3年CAGR {JU['cagr3']*100:.1f}%｜历史均值 {gs_[0]*100:.1f}% 标准差 {gs_[1]*100:.1f}pp "
                        f"区间 {gs_[2]*100:.1f}%~{gs_[3]*100:.1f}%").font=Font(size=9,color="C00000")
            ws.cell(r,1).comment=Comment("判断项：默认为近3年CAGR向永续增速线性衰减。模板不对情景幅度做暗示，必须自行复核并拉开三档。","Template")
        r+=1
    M_ROW=r
    for i,y in enumerate(YE):
        ws.cell(r,1,f"{y} 毛利率").font=Font(size=10); ws.cell(r,1).border=BX
        _v3=_scen("gm_scen", i, JU["gm"][i])
        for j in range(3):
            cc=ws.cell(r,2+j,_v3[j]); cc.font=F_IN; cc.number_format=PCT; cc.fill=P_Y
        cc=ws.cell(r,5,f"=CHOOSE($B${SCEN},B{r},C{r},D{r})"); cc.font=F_FX; cc.number_format=PCT
        if i==0:
            ws.cell(r,6,f"最近一期 {JU['gm_hist'][-1]*100:.1f}%｜历史均值 {gm_[0]*100:.1f}% 标准差 {gm_[1]*100:.1f}pp "
                        f"区间 {gm_[2]*100:.1f}%~{gm_[3]*100:.1f}%").font=Font(size=9,color="C00000")
            ws.cell(r,1).comment=Comment("判断项：默认为最近一期毛利率向历史均值线性回归。毛利率对估值的杠杆通常大于营收增速。","Template")
        r+=1
    CX_ROW=r
    for i,y in enumerate(YE):
        ws.cell(r,1,f"{y} 资本开支 / 营收").font=Font(size=10); ws.cell(r,1).border=BX
        for j in range(3):
            cc=ws.cell(r,2+j,JU["capex"][i]); cc.font=F_IN; cc.number_format=PCT
        cc=ws.cell(r,5,f"=CHOOSE($B${SCEN},B{r},C{r},D{r})"); cc.font=F_FX; cc.number_format=PCT
        if i==0: ws.cell(r,6,"自动：近3年均值，沿预测期向「折旧/营收」衰减（增速降下来后不应继续投扩张性资本开支）").font=Font(size=9,color="808080")
        r+=1
    DG_ROW=r
    ws.cell(r,1,"营收增速 Δ（敏感性平移，平时保持 0）").font=Font(size=10); ws.cell(r,1).border=BX
    cc=ws.cell(r,2,0.0); cc.font=F_IN; cc.number_format=PCT2; cc.fill=P_O
    ws.cell(r,6,"对上面 5 年增速路径整体加减。原生模拟运算表以此格为「行输入单元格」").font=Font(size=9,color="808080"); r+=1
    DM_ROW=r
    ws.cell(r,1,"毛利率 Δ（敏感性平移，平时保持 0）").font=Font(size=10); ws.cell(r,1).border=BX
    cc=ws.cell(r,2,0.0); cc.font=F_IN; cc.number_format=PCT2; cc.fill=P_O
    ws.cell(r,6,"对上面 5 年毛利率路径整体加减").font=Font(size=9,color="808080"); r+=2
    for i in range(len(YE)):
        ws.cell(G_ROW+i,5).value=f"=CHOOSE($B${SCEN},B{G_ROW+i},C{G_ROW+i},D{G_ROW+i})+$B${DG_ROW}"
        ws.cell(M_ROW+i,5).value=f"=CHOOSE($B${SCEN},B{M_ROW+i},C{M_ROW+i},D{M_ROW+i})+$B${DM_ROW}"
    IT=cal.get("interim")
    IT_ROWS={}
    if IT and IT.get("share"):
        r=sec(r,f"三、锚定已披露中期（{IT['period'][:4]}年{IT['label']}实际已确定，首个预测年不再靠增速假设外推）")
        hs="｜".join(f"{x*100:.1f}%" for x in IT["share_hist"])
        rows_spec=[("锚定开关（1=锚定 0=按增速外推）",1,"0",
                    "1 时首个预测年营收 = 已披露实际 ÷ 占比，忽略上方该年的营收增速假设"),
                   ("已披露期占全年营收比重",IT["share"],PCT2,
                    f"近{len(IT['share_hist'])}年同期占比：{hs}｜均值 {IT['share']*100:.2f}% 标准差 {IT['share_std']*100:.2f}pp"),
                   ("剩余期毛利率",round(IT["gm"],4),PCT2,
                    f"默认沿用已披露期毛利率 {IT['gm']*100:.2f}%；判断剩余期是否改善请自行调整")]
        base_r=r
        for lbl,v,f,note in rows_spec:
            ws.cell(r,1,lbl).font=Font(size=10); ws.cell(r,1).border=BX
            cc=ws.cell(r,2,v); cc.font=F_IN; cc.number_format=f
            cc.fill=P_O if "开关" in lbl else P_Y
            ws.cell(r,6,note).font=Font(size=9,color="808080" if "开关" in lbl else "C00000"); r+=1
        IT_ROWS={sp[0]:base_r+i for i,sp in enumerate(rows_spec)}
        ws.cell(r,1,"已披露期营业总收入（实际）").font=Font(size=10); ws.cell(r,1).border=BX
        cc=ws.cell(r,2,round(IT["rev"],4)); cc.font=F_LNK; cc.number_format=NUM
        IT_ROWS["rev"]=r; ws.cell(r,6,f"{IT['period']} 实际值").font=Font(size=9,color="808080"); r+=1
        ws.cell(r,1,"已披露期营业成本（实际）").font=Font(size=10); ws.cell(r,1).border=BX
        cc=ws.cell(r,2,round(IT["cogs"],4)); cc.font=F_LNK; cc.number_format=NUM
        IT_ROWS["cogs"]=r; r+=1
        ws.cell(r,1,"→ 隐含全年营业总收入").font=Font(bold=True,size=10)
        cc=ws.cell(r,2,f"=B{IT_ROWS['rev']}/B{IT_ROWS['已披露期占全年营收比重']}")
        cc.font=Font(bold=True,size=10); cc.number_format=NUM
        IT_ROWS["fy_rev"]=r
        ws.cell(r,6,"锚定开关=1 时，首个预测年营收即取此值").font=Font(size=9,color="006400"); r+=1
        ws.cell(r,1,"→ 隐含全年营业成本").font=Font(bold=True,size=10)
        cc=ws.cell(r,2,(f"=B{IT_ROWS['cogs']}+(B{IT_ROWS['fy_rev']}-B{IT_ROWS['rev']})"
                        f"*(1-B{IT_ROWS['剩余期毛利率']})"))
        cc.font=Font(bold=True,size=10); cc.number_format=NUM
        IT_ROWS["fy_cogs"]=r; r+=2

    r=sec(r,"四、自动校准假设（从历史推导；可覆盖）")
    GEN_SPEC=[("销售费用率",GEN["sell"],PCT,"近3年均值"),("管理费用率",GEN["admin"],PCT,"近3年均值"),
      ("研发费用率",GEN["rd"],PCT,"近3年均值｜CAS 全额费用化"),("税金及附加率",GEN["tsur"],PCT,"近3年均值"),
      ("其他收益 / 营收",GEN["other_inc"],PCT,"近3年均值"),("投资收益 / 营收",GEN["inv_inc"],PCT,"近3年均值"),
      ("有息负债利率",GEN["irate"],PCT,"财务费用 ÷ 期初有息负债，近3年均值"),("现金收益率",GEN["cash_yield"],PCT,"常量"),
      ("实际所得税率",GEN["tax"],PCT,"近3年均值，夹于 5%–25%"),
      ("少数股东损益占比",GEN["mi_share"],PCT,
        "最近年报实际（结构性比率，用均值会在单向抬升时严重滞后）｜历史："+("｜".join(f"{x*100:.1f}%" for x in JU.get("mish_hist",[])))),
      ("其他经营损益 / 营收",GEN.get("other_op",0.0),PCT,
        "信用/资产减值损失、公允价值变动、资产处置收益合计，近3年均值。不计入会系统性高估营业利润"),
      ("其他流动资产 / 营收",GEN["ocap"],PCT,"最近年报实际"),("其他流动负债 / 营收",GEN["oclp"],PCT,"最近年报实际"),
      ("应收周转天数 DSO",GEN["dso"],D1,"最近年报实际"),("存货周转天数 DIO",GEN["dio"],D1,"最近年报实际"),
      ("应付周转天数 DPO",GEN["dpo"],D1,"最近年报实际"),
      ("折旧率（占期初固定资产净额）",GEN["deprate"],PCT,
        f"分子来源：{DA_SRC}。÷ 期初固定资产净额。"
        "OCF 倒推法的残差会把减值、公允价值变动、递延税等全部非现金项吸收进来，分子虚高"),
      ("资本开支投向无形/长期待摊的比重",GEN.get("cx_intan",0.0),PCT,
        "现金流量表「购建固定资产、【无形资产】和其他长期资产」本就同时买了两类资产。"
        "全额记入固定资产会让摊销变成只出不进的单向抽干。默认按【申报折旧 : 申报摊销】比例分配"),
      ("非现金减值减记中固定资产占比",GEN.get("imp_ppe",1.0),PCT,
        "非现金减值在加回现金的同时必须减记资产（否则表不平）。减记哪一项是真歧义："
        "信用减值对应应收、存货跌价对应存货，而两者由周转天数驱动、动不得。"
        "默认按期初账面余额在固定资产与其他非流动资产之间分摊；余下部分记入其他非流动资产"),
      ("无形摊销 / 营收",GEN.get("amortrate",0.0),PCT,
        f"无形资产 + 长期待摊费用 + 使用权资产 摊销合计 ÷ 营收。来源：{DA_SRC}。"
        "与折旧分开建模：折旧对固定资产计提、摊销按营收比例"),
      ("归母分红率",GEN["payout"],PCT,"默认 40%，请按公司分红政策调整"),("少数股东分红率",GEN["mipay"],PCT,"默认 30%"),
      ("最低现金余额（亿）",GEN["mincash"],NUM,"低于此触发循环贷"),("现金归集阈值（亿）",GEN["sweep"],NUM,"超出最低现金此数则还短贷")]
    GEN_ROW=r
    for lbl,v,f,note in GEN_SPEC:
        ws.cell(r,1,lbl).font=Font(size=10); ws.cell(r,1).border=BX
        cc=ws.cell(r,2,round(float(v),6)); cc.font=F_IN; cc.number_format=f
        ws.cell(r,6,note).font=Font(size=9,color="808080"); r+=1
    GR={s[0]:GEN_ROW+i for i,s in enumerate(GEN_SPEC)}
    r+=1
    r=sec(r,f"五、{PR['annual'][-1][:4]}A 期初余额（模型起点，来自实际财报）")
    OP=[("货币资金",O["cash"]),("交易性金融资产",O["tfa"]),("应收票据及应收账款",O["ar"]),("存货",O["inv"]),
        ("其他流动资产",O["oca"]),("固定资产净额",O["ppe"]),("在建工程",O["cip"]),("其他非流动资产",O["onca"]),
        ("短期借款",O["st"]),("应付票据及应付账款",O["ap"]),("其他流动负债",O["ocl"]),("一年内到期非流动负债",O["cur"]),
        ("长期借款",O["lt"]),("其他非流动负债",O["oncl"]),("少数股东权益",O["mi"]),("归母权益",O["eq"]),
        ("基期营业总收入",cal["rev"][PR["annual"][-1]]),("总股本（亿股）",M["shares"]),("现价（元）",M["price"])]
    OPEN_ROW=r
    for lbl,v in OP:
        ws.cell(r,1,lbl).font=Font(size=10); ws.cell(r,1).border=BX
        cc=ws.cell(r,2,float(v)); cc.font=F_IN; cc.number_format=NUM; r+=1
    OR_={s[0]:OPEN_ROW+i for i,s in enumerate(OP)}
    if IT_ROWS.get("fy_rev"):
        # 首年「生效值」内置锚定分支：显示真正生效的增速/毛利率，而非被忽略的输入。
        # 只引用假设页内单元格，不引用利润表 —— 避免结构性循环引用。
        SW=IT_ROWS['锚定开关（1=锚定 0=按增速外推）']; BR=OR_['基期营业总收入']
        ws.cell(G_ROW,5).value=(f"=IF($B${SW}=1,B{IT_ROWS['fy_rev']}/$B${BR}-1,"
                                f"CHOOSE($B${SCEN},B{G_ROW},C{G_ROW},D{G_ROW})+$B${DG_ROW})")
        ws.cell(M_ROW,5).value=(f"=IF($B${SW}=1,1-B{IT_ROWS['fy_cogs']}/B{IT_ROWS['fy_rev']},"
                                f"CHOOSE($B${SCEN},B{M_ROW},C{M_ROW},D{M_ROW})+$B${DM_ROW})")
        for rr in (G_ROW, M_ROW):
            for cc_ in (2,3,4):
                ws.cell(rr,cc_).fill=P_G
                ws.cell(rr,cc_).font=Font(color="808080",size=10,italic=True)
            ws.cell(rr,6,"⚠ 锚定开启时本行输入不生效——生效值由已披露中期反推（见上方第三小节）").font=Font(size=9,color="C00000")
    ws.cell(r,1,"★ 期初平衡校验 = 资产 −（负债+权益）").font=Font(bold=True,size=10)
    cc=ws.cell(r,2,f"=SUM(B{OR_['货币资金']}:B{OR_['其他非流动资产']})-SUM(B{OR_['短期借款']}:B{OR_['归母权益']})")
    cc.font=F_FX; cc.number_format=Z; cc.fill=P_O
    ws.cell(r,6,"必须为 0").font=Font(size=9,color="006400"); r+=2
    r=sec(r,"六、DCF 参数")
    DP=[("无风险利率 Rf（中债10Y）",DC["rf"],PCT2,f"AkShare bond_zh_us_rate，{M['rf_date']}"),
        ("股权风险溢价 ERP",DC["erp"],PCT2,"中国 A 股常规 6–8%，默认取下限"),
        ("Beta（5年对上证综指回归+Blume）",DC["beta"],"0.000",f"原始 {M['beta_raw']}，样本 {M['beta_n']} 个交易日"),
        ("股权成本 Ke",None,PCT2,"Ke = Rf + Beta × ERP"),
        ("债务成本 Kd（税前）",DC["kd"],PCT2,
         f"来源：{cal.get('kd_src','?')}。取【实测付息成本】与【中债10Y+120bp】的较大者；"
         "现金远超有息负债时财务费用净额为负，倒推利率无意义，此时只用下限"),
        ("债务权重 D/(D+E)",DC["wd"],PCT2,f"按实际资本结构算：有息负债 ÷（有息负债 + 现价×总股本）。若你相信内在价值远低于现价，此权重应上调"),
        ("WACC",None,PCT2,"WACC = (1−Wd)×Ke + Wd×Kd×(1−t)"),
        ("永续增长率 g",DC["g"],PCT2,"中国名义 GDP 长期中枢"),
        ("终值所得税率 t_term",DC["t_term"],PCT2,"永续期不应假设税收优惠永存"),
        ("期中折现开关（1=是 0=否）",DC["midyear"],"0","现金流全年均匀发生时取 1"),
        ("估值基准日滚动年数",DC["roll"],"0.000",f"{PR['annual'][-1][:4]}-12-31 → {M['asof']}"),
        ("非经营资产（加回）",DC["nonop"],NUM,"长期股权投资+投资性房地产+其他非流动金融资产"),
        ("少数股东扣除方式（1=账面 2=价值）",DC["mi_mode"],"0","价值法按其利润分成折算"),
        ("少数股东利润占比",None,PCT2,"链接自上方自动校准值，避免两处副本发散")]
    DCF_ROW=r
    for lbl,v,f,note in DP:
        ws.cell(r,1,lbl).font=Font(size=10); ws.cell(r,1).border=BX
        cc=ws.cell(r,2,None if v is None else round(float(v),6)); cc.font=F_FX if v is None else F_IN
        cc.number_format=f; ws.cell(r,6,note).font=Font(size=9,color="808080"); r+=1
    DR={s[0]:DCF_ROW+i for i,s in enumerate(DP)}
    KE=DR["股权成本 Ke"]; WC=DR["WACC"]
    ws.cell(DR["少数股东利润占比"],2).value=f"=B{GR['少数股东损益占比']}"
    ws.cell(DR["少数股东利润占比"],2).font=F_LNK
    ws.cell(KE,2).value=f"=B{DR['无风险利率 Rf（中债10Y）']}+B{DR['Beta（5年对上证综指回归+Blume）']}*B{DR['股权风险溢价 ERP']}"
    ws.cell(WC,2).value=(f"=(1-B{DR['债务权重 D/(D+E)']})*B{KE}+B{DR['债务权重 D/(D+E)']}"
                         f"*B{DR['债务成本 Kd（税前）']}*(1-B{GR['实际所得税率']})")
    r+=1
    # ---------- 七、股本与融资 ----------
    # 设计要点：净负债在 DCF 桥里按【基期】计量，因此募资到账的现金对 EV 不可见 ——
    # 必须在权益桥单列「加：募资净额现值」，否则股本增加而权益价值不变 = 机械性过度稀释。
    # 又因 EBIT = 营业利润 + 财务费用（利息收支相抵），FCFF 与现金/循环贷无关，
    # 把募资现金打进三表【不会】通过利息收入二次抬高 EV，无重复计价。
    r=sec(r,"七、股本与融资（增发 / 配股）")
    FN_SPEC=[
        ("增发股数（亿股）",FIN["new_shares"],"0.0000",None,"0 = 关闭本模块。全部效应（稀释、募资、募投）均由此开关驱动"),
        ("发行价（元/股）",FIN["issue_price"],CNY,None,"默认=现价；定增实务多为定价基准日前20日均价×80%"),
        ("发行费用率",FIN["fee"],PCT,None,"承销+保荐+律师+审计，A股定增通常 1%–2%"),
        ("募资总额（亿元）",None,NUM,"=B{ns}*B{ip}",None),
        ("募资净额（亿元）",None,NUM,"=B{gross}*(1-B{fee})",None),
        ("到账年份序号（1=首个预测年）",FIN["year"],"0",None,f"取 1..{NY}；超出范围则本模块在预测期内不发生"),
        ("投向 — 资本开支（亿元）",FIN["use_capex"],NUM,None,"⚠ 只有这一项会改变经营现金流：它进资本开支、压 FCFF。若不同步上调增速/毛利率，模型会告诉你「无回报的资本开支毁灭价值」——这是对的"),
        ("投向 — 偿还债务（亿元）",FIN["use_debt"],NUM,None,"先还短期借款、不足部分还长期借款（各自以期初余额封顶）。对估值无影响（净负债按基期计量、利息不进 FCFF），只影响报表与净利润"),
        ("投向 — 补充流动资金（亿元）",None,NUM,"=B{net}-B{ucx}-B{udb}",None),
        ("募投资本开支建设期（年）",FIN["build_years"],"0",None,"自到账年份起平均投入；不得为 0"),
        ("年度回购金额 / 营收",FIN.get("buyback_rate",0.0),PCT,None,
         "A股回购远不如美股常规，默认 0。回购是【筹资】行为：现金流出、归母权益减少、股本减少，"
         "但【不改变 FCFF 与企业价值】——所以「每股内在价值」仍按【基期股本】计算，不因未来回购而抬高。"
         "回购真正的增厚来自「以低于内在价值的价格买回」，那属于第二层效应，本模板不自动计入"),
        ("回购均价（元/股）",FIN.get("buyback_price",0.0),CNY,None,"默认=现价；用于把回购金额折成股数"),
        ("桥接折现年数（0=视同已完成）",FIN["disc_years"],"0.00",None,"募资净额在权益桥里按 WACC 折现的年数。0 = 按已完成的备考口径（卖方惯例）；填 0.5/1 则体现发行未落地的时间价值"),
    ]
    FIN_ROW=r
    for lbl,v,f,_fx,note in FN_SPEC:
        ws.cell(r,1,lbl).font=Font(size=10); ws.cell(r,1).border=BX
        cc=ws.cell(r,2,None if v is None else round(float(v),6))
        cc.font=F_FX if v is None else F_IN; cc.number_format=f
        if note: ws.cell(r,6,note).font=Font(size=9,color="808080")
        r+=1
    FN={s_[0]:FIN_ROW+i for i,s_ in enumerate(FN_SPEC)}
    _map=dict(ns=FN["增发股数（亿股）"],ip=FN["发行价（元/股）"],fee=FN["发行费用率"],
              gross=FN["募资总额（亿元）"],net=FN["募资净额（亿元）"],
              ucx=FN["投向 — 资本开支（亿元）"],udb=FN["投向 — 偿还债务（亿元）"])
    for lbl,v,f,fx,note in FN_SPEC:
        if fx: ws.cell(FN[lbl],2).value=fx.format(**_map)
    ws.cell(r,1,"★ 投向校验 = 补充流动资金（须 ≥ 0）").font=Font(bold=True,size=10)
    cc=ws.cell(r,2,f"=B{FN['投向 — 补充流动资金（亿元）']}"); cc.font=F_FX; cc.number_format=NUM; cc.fill=P_O
    ws.cell(r,6,"为负说明「资本开支+还债」已超过募资净额，方案不自洽").font=Font(size=9,color="006400"); r+=1
    ws.cell(r,1,"　摊薄比例 = 增发股数 /（原股本 + 增发股数）").font=Font(size=10)
    cc=ws.cell(r,2,f"=IF(B{FN['增发股数（亿股）']}=0,0,B{FN['增发股数（亿股）']}/(B{OR_['总股本（亿股）']}+B{FN['增发股数（亿股）']}))")
    cc.font=F_FX; cc.number_format=PCT; r+=2
    # ---------- 八、数据来源与交叉校验 ----------
    # 之前 da_src 与 xcheck 只打印在命令行，随会话消失 —— README 不足 #7 要的是
    # 可追溯的审计痕迹，必须落在工作簿里，且「不可用」要和「一致」显著区分。
    r=sec(r,"八、数据来源与交叉校验（审计痕迹）")
    def _note(lbl, val, color="808080", bold=False):
        nonlocal r
        ws.cell(r,1,lbl).font=Font(size=10,bold=bold); ws.cell(r,1).border=BX
        c_=ws.cell(r,2,val); c_.font=Font(size=10,bold=bold,color=color); r+=1
    _note("财报来源","新浪（AkShare stock_financial_report_sina）")
    _note("行情来源",f"{M.get('quote_source','?')}｜现价基准日 {M.get('asof','?')}")
    _note("无风险利率",f"中债10Y {M.get('rf',0)*100:.3f}%（{M.get('rf_date','?')}）")
    _note("折旧摊销来源",DA_SRC,"C00000" if "⚠" in DA_SRC else "006400",True)
    # 标签刻意不以「债务成本 Kd」开头 —— 验证器按前缀查行，同前缀会触发歧义报错
    _note("Kd（债务成本）来源",cal.get("kd_src","?"))
    for per,dp,am in (cal.get("da_detail") or []):
        _note(f"　{per[:4]} 申报折旧 / 摊销",
              f"{'—' if dp is None else f'{dp:.4f}'}　/　{'—' if am is None else f'{am:.4f}'}（亿元）")
    _CL = cal.get("clamps") or []
    _note("自动校准被截断项",
          "（无）" if not _CL else f"{len(_CL)} 项 —— 这些数字不是纯推导结果，已被上下限动过手脚",
          "808080" if not _CL else "C00000", bool(_CL))
    for x in _CL: _note("　⚠", x, "C00000")
    XC = cal.get("xcheck") or {}
    _st = XC.get("ok")
    _txt = {True:"✅ 一致", False:"❌ 存在不一致", None:"⚠ 不可用（不等于一致）"}.get(_st,"⚠ 未执行")
    _note("新浪 ↔ 东财 交叉校验",_txt,{True:"006400",False:"C00000"}.get(_st,"C00000"),True)
    _note("　比对覆盖",f"{XC.get('n_compared','?')}/{XC.get('n_expected','?')} 项"
                      f"（{XC.get('coverage',0)*100:.0f}%）｜分表 {XC.get('per_sheet','—')}")
    _note("　说明",str(XC.get("note","—")))
    if XC.get("detail"): _note("　取数错误",str(XC["detail"])[:120],"C00000")
    for x in (XC.get("rows") or [])[:8]:
        _note(f"　差异 {x['期'][:4]} {x['科目']}",f"新浪 {x['新浪']} vs 东财 {x['东财']}（{x['偏差']}）","C00000")
    r+=1
    ws.freeze_panes="A5"
    cal["_rows"]=dict(SCEN=SCEN,G_ROW=G_ROW,M_ROW=M_ROW,CX_ROW=CX_ROW,GR=GR,OR_=OR_,DR=DR,FN=FN,DG=DG_ROW,DM=DM_ROW,IT=IT_ROWS,
                      HC=HC,CC=CC,BASE=BASE,NH=NH,NY=NY)

    # 驱动因子分解：只有当上游（ir-agent 的方法论预研）给出 drivers 时才建表。
    # 没有分解式是降级路径（预研失败），此时 1_假设 保持原样，工作簿形状
    # 与从前完全一致 —— 见 _build_driver_sheet 的文档字符串。
    DRV = JU.get("drivers")
    if DRV:
        _build_driver_sheet(wb, DRV, YE, ws, G_ROW, M_ROW)

    return _sheets(wb, cal, path)


_SCEN_COLS = ("B", "C", "D")          # Bull / Base / Bear —— 与 1_假设 一致


def _build_driver_sheet(wb, drv, YE, assum_ws, g_row, m_row):
    """建「1b_驱动分解」，并把 1_假设 的增速/毛利率改为跨表引用。

    **列序必须与 1_假设 一致（B=Bull C=Base D=Bear）**，它与
    CHOOSE($B$SCEN,…) 绑定。写反不会让验算失败 —— 公式本身没错，
    只是「选悲观」会选出乐观值。

    单位不能混：`变化率` 只进乘法链（营收 = Π(1+因子变化率)−1），
    `pp` 只进加法链（毛利率 = 基期 + Σ因子贡献(pp)/100）。表里对每个
    因子都标出单位与所属运算列，避免两者被误用互换。
    """
    ds = wb.create_sheet("1b_驱动分解", 2)
    ds.column_dimensions["A"].width=30
    for c in "BCD": ds.column_dimensions[c].width=12
    ds.column_dimensions["E"].width=10
    r = 1
    ds.cell(r, 1, "驱动因子分解").font = Font(bold=True, size=12); r += 2
    for i, t in enumerate(["因子 / 年", "Bull", "Base", "Bear", "单位"]):
        c = ds.cell(r, i + 1, t); c.font = F_COL; c.fill = P_B
    r += 1

    rows: dict[str, dict[str, int]] = {}
    for target in ("revenue", "gross_margin"):
        block = drv.get(target)
        if not block:
            continue
        rows[target] = {}
        # 覆盖年数以 values 实际给出的档数为准，不强行对齐 YE 全长 ——
        # AI 逐因子填写可能只覆盖部分预测年（比如方法论预研只做到第一年），
        # 未覆盖的年份 1_假设 保留原直接取值，不被本表覆盖。
        n_cov = len(block["values"]["base"])
        for yi, y in enumerate(YE[:n_cov]):
            for fi, f in enumerate(block["factors"]):
                ds.cell(r, 1, f"{y} {f['label']}").font = Font(size=10)
                for ci, case in enumerate(("bull", "base", "bear")):
                    cc = ds.cell(r, 2 + ci, block["values"][case][yi][fi])
                    cc.font = F_IN
                    cc.number_format = PCT if f["unit"] == "变化率" else "0.00"
                    cc.fill = P_Y
                ds.cell(r, 5, f["unit"]).font = Font(size=9, color="808080")
                rows[target][f"{yi}:{fi}"] = r
                r += 1

            label = "营收增速（合成）" if target == "revenue" else "毛利率（合成）"
            ds.cell(r, 1, f"{y} {label}").font = Font(size=10, bold=True)
            for ci, col in enumerate(_SCEN_COLS):
                refs = [f"{col}{rows[target][f'{yi}:{fi}']}"
                        for fi in range(len(block["factors"]))]
                if block["op"] == "乘":
                    expr = "*".join(f"(1+{x})" for x in refs) + "-1"
                else:
                    base = block.get("base_level", 0.0)
                    expr = f"{base}+(" + "+".join(refs) + ")/100"
                cc = ds.cell(r, 2 + ci, f"={expr}")
                cc.font = F_FX; cc.number_format = PCT
            rows[target][f"synth:{yi}"] = r
            r += 1
        r += 1

    # 1_假设 的判断项改为引用本表 —— 只覆盖本表实际分解到的年份
    for target, start in (("revenue", g_row), ("gross_margin", m_row)):
        if target not in rows:
            continue
        for key, r_ in rows[target].items():
            if not key.startswith("synth:"):
                continue
            yi = int(key.split(":")[1])
            for ci, col in enumerate(_SCEN_COLS):
                assum_ws.cell(start + yi, 2 + ci,
                              f"='1b_驱动分解'!{col}{r_}").font = F_FX

def _sheets(wb, cal, path):
    M,PR,O,GEN,JU,DC = cal["meta"],cal["periods"],cal["open"],cal["gen"],cal["judg"],cal["dcf"]
    IS,BS,CF = cal["stmts"]["income"],cal["stmts"]["balance"],cal["stmts"]["cashflow"]
    R=cal["_rows"]; HC,CC,BASE,NH,NY = R["HC"],R["CC"],R["BASE"],R["NH"],R["NY"]
    GR,OR_,DR,SCEN,G_ROW,M_ROW,CX_ROW = R["GR"],R["OR_"],R["DR"],R["SCEN"],R["G_ROW"],R["M_ROW"],R["CX_ROW"]
    FN=R["FN"]
    _K_NET="募资净额（亿元）"; _K_DBT="投向 — 偿还债务（亿元）"
    HP,HL,YE = PR["all"],PR["labels"],PR["proj"]
    A="'1_假设'"; P="'2_利润表'"; B_="'3_资产负债表'"; K="'4_现金流量表'"
    # 融资模块的「年度触发式」表达式：年序号 t 是 Python 字面量，直接写进公式，无需辅助列。
    FYR=f"{A}!$B${FN['到账年份序号（1=首个预测年）']}"
    def _fin_at(i, cell):            # 仅在到账年份发生
        return f"IF({FYR}={i+1},{cell},0)"
    def _fin_capex(i):               # 建设期内平摊的募投资本开支（正数）
        BY=f"{A}!$B${FN['募投资本开支建设期（年）']}"
        UC=f"{A}!$B${FN['投向 — 资本开支（亿元）']}"
        return f"IF(AND({BY}>0,{i+1}>={FYR},{i+1}<={FYR}+{BY}-1),{UC}/{BY},0)"
    def _fin_shares(i):              # 到账年份及以后的增发股数
        return f"IF({FYR}<={i+1},{A}!$B${FN['增发股数（亿股）']},0)"
    def pc(c):
        i=CC.index(c); return BASE if i==0 else CC[i-1]
    def H(df,item,p):
        v=D.val(df,item,p); return None if v is None else round(v,4)
    def hrow(df,item): return [H(df,item,p) for p in HP]
    def bs_der(kind,p):
        g=lambda k:(D.val(BS,k,p) or 0.0)
        if kind=="oca":  return round(g("流动资产合计")-g("货币资金")-g("交易性金融资产")-(g("应收票据及应收账款") or g("应收票据")+g("应收账款"))-g("存货"),4)
        if kind=="onca": return round(g("非流动资产合计")-(g("固定资产净额") or g("固定资产"))-(g("在建工程合计") or g("在建工程")),4)
        if kind=="ocl":  return round(g("流动负债合计")-g("短期借款")-(g("应付票据及应付账款") or g("应付票据")+g("应付账款"))-g("一年内到期的非流动负债"),4)
        if kind=="oncl": return round(g("负债合计")-g("流动负债合计")-g("长期借款"),4)
        if kind=="eqp":  return round(g("资产总计")-g("负债合计")-g("少数股东权益"),4)
        if kind=="te":   return round(g("资产总计")-g("负债合计"),4)
    def sheet(title,tab=None):
        w=wb.create_sheet(title)
        if tab: w.sheet_properties.tabColor=tab
        w.column_dimensions["A"].width=34
        for i in range(2,2+NH+NY): w.column_dimensions[CL(i)].width=13
        w.cell(1,1,f"{M['name']} {M['code']} — {title[2:]}（亿元，CAS）").font=Font(bold=True,size=13,color=NAVY)
        w.cell(2,1,f"{HC[0]}:{HC[-1]} = 历史实际（斜体静态）　｜　{CC[0]}:{CC[-1]} = 公式预测　｜　⚠ 预测以 {HL[len(PR['annual'])-1]}（{BASE}列）为基数").font=Font(size=9,italic=True,color="C00000")
        w.cell(4,1,title[2:]).font=F_HDR
        for c in range(1,2+NH+NY): w.cell(4,c).fill=P_N
        w.cell(5,1,"科目（亿元）").font=F_COL; w.cell(5,1).fill=P_B
        for i,y in enumerate(HL+YE):
            cc=w.cell(5,2+i,y); cc.font=F_COL; cc.alignment=Alignment(horizontal="center")
            cc.fill=P_G if i<NH else P_B
        w.freeze_panes="B6"; return w
    def emit(w,row,label,hist,fx,fmt=NUM,bold=False,note=None,link=None,fill=None):
        c0=w.cell(row,1,label); c0.font=Font(bold=bold,size=10); c0.border=BX
        if note: c0.comment=Comment(note,"Template")
        for i in range(NH):
            cc=w.cell(row,2+i,hist[i] if hist else None); cc.number_format=fmt; cc.font=F_H
        if link is not None:
            cc=w.cell(row,1+len(PR["annual"]) if False else 2+len(PR["annual"])-1,link); cc.font=F_LNK; cc.number_format=fmt
        for i,c in enumerate(CC):
            cc=w.cell(row,2+NH+i,fx(c,i)); cc.number_format=fmt
            cc.font=Font(bold=True,size=10) if bold else F_FX
            if fill: cc.fill=fill
        return row+1

    # ---------- 利润表 ----------
    wi=sheet("2_利润表","2E75B6")
    RV,RG,RC,RGP,RGM,RTS,RSE,RAD,RRD,ROI,RII,RFIN,ROP,RPBT,RTAX,RNI,RMI,RNIP,REPS,RROE,RPE,REBIT = range(6,28)
    revh=hrow(IS,"营业总收入")
    if all(x is None for x in revh): revh=hrow(IS,"营业收入")
    IT=R.get("IT") or {}
    emit(wi,RV,"营业总收入",revh,lambda c,i:f"={pc(c)}{RV}*(1+{A}!$E${G_ROW+i})",NUM,True,
         "预测 = 上年营收 ×(1+生效营收增速)；首个预测列以最近年报为基数，跳过中期报告列",f"={A}!B{OR_['基期营业总收入']}")
    na=len(PR["annual"])
    # 中期报告列不计算同比：半年报 ÷ 上年全年 是无意义的比较
    gh=[None]+[round(revh[i]/revh[i-1]-1,6) if i<na and revh[i] and revh[i-1] else None for i in range(1,NH)]
    emit(wi,RG,"  营收增速",gh,lambda c,i:f"={c}{RV}/{pc(c)}{RV}-1",PCT)
    emit(wi,RC,"营业成本",hrow(IS,"营业成本"),lambda c,i:f"={c}{RV}*(1-{A}!$E${M_ROW+i})")
    cg=hrow(IS,"营业成本")
    emit(wi,RGP,"毛利",[round(revh[i]-cg[i],4) if revh[i] is not None and cg[i] is not None else None for i in range(NH)],
         lambda c,i:f"={c}{RV}-{c}{RC}",NUM,True)
    emit(wi,RGM,"  毛利率",[round(1-cg[i]/revh[i],6) if revh[i] and cg[i] is not None else None for i in range(NH)],
         lambda c,i:f"={c}{RGP}/{c}{RV}",PCT)
    for row,lbl,item,key in [(RTS,"税金及附加","营业税金及附加","税金及附加率"),(RSE,"销售费用","销售费用","销售费用率"),
        (RAD,"管理费用","管理费用","管理费用率"),(RRD,"研发费用","研发费用","研发费用率"),
        (ROI,"其他收益","其他收益","其他收益 / 营收"),(RII,"投资收益","投资收益","投资收益 / 营收")]:
        emit(wi,row,lbl,hrow(IS,item),lambda c,i,k=key:f"={c}{RV}*{A}!$B${GR[k]}")
    emit(wi,RFIN,"财务费用",hrow(IS,"财务费用"),
         lambda c,i:f"=({B_}!{pc(c)}18+{B_}!{pc(c)}21+{B_}!{pc(c)}22)*{A}!$B${GR['有息负债利率']}-{B_}!{pc(c)}6*{A}!$B${GR['现金收益率']}",
         NUM,False,"期初有息负债×利率 − 期初现金×收益率（用期初值，避免循环引用）")
    emit(wi,ROP,"营业利润",hrow(IS,"营业利润"),
         lambda c,i:(f"={c}{RGP}-{c}{RTS}-{c}{RSE}-{c}{RAD}-{c}{RRD}+{c}{ROI}"
                     f"+{c}{RV}*{A}!$B${GR['其他经营损益 / 营收']}-{c}{RFIN}"),NUM,True,
         "已剔除投资收益（长期股权投资按非经营资产单独加回，避免重复计价）；已计入其他经营损益（减值等）")
    emit(wi,RPBT,"利润总额",hrow(IS,"利润总额"),lambda c,i:f"={c}{ROP}",NUM,True)
    emit(wi,RTAX,"所得税费用",hrow(IS,"所得税费用"),lambda c,i:f"={c}{RPBT}*{A}!$B${GR['实际所得税率']}")
    emit(wi,RNI,"净利润",hrow(IS,"净利润"),lambda c,i:f"={c}{RPBT}-{c}{RTAX}",NUM,True)
    emit(wi,RMI,"  少数股东损益",hrow(IS,"少数股东损益"),lambda c,i:f"={c}{RNI}*{A}!$B${GR['少数股东损益占比']}")
    emit(wi,RNIP,"归属于母公司股东净利润",hrow(IS,"归属于母公司所有者的净利润"),lambda c,i:f"={c}{RNI}-{c}{RMI}",NUM,True)
    nip=hrow(IS,"归属于母公司所有者的净利润"); sh=M["shares"]
    emit(wi,REPS,"EPS（元/股）",[round(x/sh,4) if x is not None and sh==sh else None for x in nip],
         lambda c,i:f"={c}{RNIP}/({A}!$B${OR_['总股本（亿股）']}+{_fin_shares(i)}-{K}!{c}32)",CNY,True,
         "历史 EPS 按现有总股本折算；预测期自增发到账年份起按摊薄后股本计算")
    emit(wi,RROE,"ROE（归母，平均净资产）",[None]*NH,lambda c,i:f"={c}{RNIP}/AVERAGE({B_}!{pc(c)}25,{B_}!{c}25)",PCT)
    emit(wi,RPE,"对应 PE（现价）",[None]*NH,lambda c,i:f"={A}!$B${OR_['现价（元）']}/{c}{REPS}",MX)
    oph=hrow(IS,"营业利润"); fnh=hrow(IS,"财务费用")
    emit(wi,REBIT,"EBIT（营业利润+财务费用）",
         [round((oph[i] or 0)+(fnh[i] or 0),4) if oph[i] is not None else None for i in range(NH)],
         lambda c,i:f"={c}{ROP}+{c}{RFIN}",NUM,True,"DCF 用")

    # ---------- 资产负债表（行号固定 6..31）----------
    wbs=sheet("3_资产负债表","2E75B6")
    def eb(row,label,hk,fx,fmt=NUM,bold=False,note=None,link=None,der=None,fill=None):
        hist=[bs_der(der,p) for p in HP] if der else (hrow(BS,hk) if hk else [None]*NH)
        emit(wbs,row,label,hist,fx,fmt,bold,note,link,fill)
    eb(6,"货币资金","货币资金",lambda c,i:f"={K}!{c}23",NUM,False,"模型 plug = 现金流量表期末现金",f"={A}!B{OR_['货币资金']}")
    eb(7,"交易性金融资产","交易性金融资产",lambda c,i:f"={pc(c)}7",NUM,False,None,f"={A}!B{OR_['交易性金融资产']}")
    eb(8,"应收票据及应收账款","应收票据及应收账款",lambda c,i:f"={P}!{c}{RV}*{A}!$B${GR['应收周转天数 DSO']}/365",NUM,False,None,f"={A}!B{OR_['应收票据及应收账款']}")
    eb(9,"存货","存货",lambda c,i:f"={P}!{c}{RC}*{A}!$B${GR['存货周转天数 DIO']}/365",NUM,False,None,f"={A}!B{OR_['存货']}")
    eb(10,"其他流动资产",None,lambda c,i:f"={P}!{c}{RV}*{A}!$B${GR['其他流动资产 / 营收']}",NUM,False,None,f"={A}!B{OR_['其他流动资产']}","oca")
    eb(11,"流动资产合计","流动资产合计",lambda c,i:f"=SUM({c}6:{c}10)",NUM,True)
    eb(12,"固定资产净额","固定资产净额",
       lambda c,i:(f"={pc(c)}12-{K}!{c}14*(1-{A}!$B${GR['资本开支投向无形/长期待摊的比重']})"
                   f"-{K}!{c}29-{K}!{c}33*{A}!$B${GR['非现金减值减记中固定资产占比']}"),NUM,False,
       "期初 + 资本开支中的非无形部分(CF为负故相减) − 折旧 − 非现金减值（减值加回现金的同时必须减记资产，否则表不平）",
       f"={A}!B{OR_['固定资产净额']}")
    eb(13,"在建工程","在建工程合计",lambda c,i:f"={pc(c)}13",NUM,False,None,f"={A}!B{OR_['在建工程']}")
    eb(14,"其他非流动资产",None,
       lambda c,i:(f"={pc(c)}14-{K}!{c}14*{A}!$B${GR['资本开支投向无形/长期待摊的比重']}-{K}!{c}30"
                   f"-{K}!{c}33*(1-{A}!$B${GR['非现金减值减记中固定资产占比']})"),NUM,False,
       "期初 + 资本开支中的无形部分(CF为负故相减) − 摊销（A股表内无单列无形资产行，在此消化）",
       f"={A}!B{OR_['其他非流动资产']}","onca")
    eb(15,"非流动资产合计","非流动资产合计",lambda c,i:f"=SUM({c}12:{c}14)",NUM,True)
    eb(16,"资产总计","资产总计",lambda c,i:f"={c}11+{c}15",NUM,True)
    wbs.cell(17,1,"负债与所有者权益").font=F_HDR
    for c in range(1,2+NH+NY): wbs.cell(17,c).fill=P_N
    eb(18,"短期借款","短期借款",lambda c,i:f"={pc(c)}18+{K}!{c}18+{K}!{c}27",NUM,False,"期初 + 循环贷净变动 + 募资偿还",f"={A}!B{OR_['短期借款']}")
    eb(19,"应付票据及应付账款","应付票据及应付账款",lambda c,i:f"={P}!{c}{RC}*{A}!$B${GR['应付周转天数 DPO']}/365",NUM,False,None,f"={A}!B{OR_['应付票据及应付账款']}")
    eb(20,"其他流动负债",None,lambda c,i:f"={P}!{c}{RV}*{A}!$B${GR['其他流动负债 / 营收']}",NUM,False,None,f"={A}!B{OR_['其他流动负债']}","ocl")
    eb(21,"一年内到期非流动负债","一年内到期的非流动负债",lambda c,i:f"={pc(c)}21",NUM,False,None,f"={A}!B{OR_['一年内到期非流动负债']}")
    eb(22,"长期借款","长期借款",lambda c,i:f"={pc(c)}22+{K}!{c}28",NUM,False,"期初 + 募资偿还（无自动摊还计划）",f"={A}!B{OR_['长期借款']}")
    eb(23,"其他非流动负债",None,lambda c,i:f"={pc(c)}23",NUM,False,None,f"={A}!B{OR_['其他非流动负债']}","oncl")
    eb(24,"负债合计","负债合计",lambda c,i:f"=SUM({c}18:{c}23)",NUM,True)
    eb(25,"归属于母公司股东权益",None,lambda c,i:f"={pc(c)}25+{P}!{c}{RNIP}+{K}!{c}16+{K}!{c}26+{K}!{c}31",NUM,False,"期初 + 归母净利 + 分红(CF为负) + 增发募资净额 + 回购(负)",f"={A}!B{OR_['归母权益']}","eqp")
    eb(26,"少数股东权益","少数股东权益",lambda c,i:f"={pc(c)}26+{P}!{c}{RMI}+{K}!{c}17",NUM,False,None,f"={A}!B{OR_['少数股东权益']}")
    eb(27,"所有者权益合计",None,lambda c,i:f"={c}25+{c}26",NUM,True,None,None,"te")
    eb(28,"负债和股东权益总计",None,lambda c,i:f"={c}24+{c}27",NUM,True)
    eb(29,"★ 平衡校验 = 资产 −（负债+权益）",None,lambda c,i:f"={c}16-{c}28",Z,True,"必须为 0",None,None,P_O)
    eb(30,"净负债（有息负债−现金−交易性金融资产）",None,lambda c,i:f"={c}18+{c}21+{c}22-{c}6-{c}7")
    eb(31,"营运资本 NWC",None,lambda c,i:f"={c}8+{c}9+{c}10-{c}19-{c}20",NUM,False,"应收+存货+其他流动资产 − 应付 − 其他流动负债")

    # ---------- 现金流量表（行号固定 6..25）----------
    wk=sheet("4_现金流量表","2E75B6")
    wk.cell(3,1,"注：历史列仅填报表已披露科目；营运资本明细变动与循环贷为模型专用行，历史留空。").font=Font(size=9,color="808080")
    MC=f"{A}!$B${GR['最低现金余额（亿）']}"; SW=f"{A}!$B${GR['现金归集阈值（亿）']}"
    def ek(row,label,hist,fx,fmt=NUM,bold=False,note=None,link=None):
        emit(wk,row,label,hist,fx,fmt,bold,note,link)
    ek(6,"净利润",hrow(IS,"净利润"),lambda c,i:f"={P}!{c}{RNI}")
    ek(7,"加：折旧摊销合计",[None]*NH,lambda c,i:f"={c}29+{c}30",NUM,False,"折旧 + 摊销；两者分列于第 29/30 行")
    for row,lbl,r_ in [(8,"减：应收账款增加",8),(9,"减：存货增加",9),(10,"减：其他流动资产增加",10)]:
        ek(row,lbl,[None]*NH,lambda c,i,rr=r_:f"=-({B_}!{c}{rr}-{B_}!{pc(c)}{rr})")
    ek(11,"加：应付账款增加",[None]*NH,lambda c,i:f"={B_}!{c}19-{B_}!{pc(c)}19")
    ek(12,"加：其他流动负债增加",[None]*NH,lambda c,i:f"={B_}!{c}20-{B_}!{pc(c)}20")
    # 减值/公允价值变动/资产处置合计是【非现金】的，却进了营业利润和 EBIT。
    # 不加回等于把同一笔成本算两次：一次在当年买资产的资本开支里，一次在减值里。
    ek(33,"加：非现金其他经营损益（减值/公允价值变动等）",[None]*NH,
       lambda c,i:f"=-{P}!{c}{RV}*{A}!$B${GR['其他经营损益 / 营收']}",NUM,False,
       "该科目为负（损失）时此行为正。标准 FCFF 做法：非现金项按全额加回，不做税后折算")
    ek(13,"经营活动现金流量净额",hrow(CF,"经营活动产生的现金流量净额"),
       lambda c,i:f"=SUM({c}6:{c}12)+{c}33",NUM,True)
    cxh=hrow(CF,"购建固定资产、无形资产和其他长期资产所支付的现金")
    ek(14,"购建固定资产（资本开支）",[None if x is None else -x for x in cxh],
       lambda c,i:f"=-{P}!{c}{RV}*{A}!$E${CX_ROW+i}-{_fin_capex(i)}",NUM,False,
       "经营性资本开支（营收×资本开支率）+ 募投项目资本开支（建设期内平摊）")
    ek(15,"投资活动现金流量净额",hrow(CF,"投资活动产生的现金流量净额"),lambda c,i:f"={c}14",NUM,True)
    ek(16,"分配股利 — 归母",[None]*NH,lambda c,i:f"=-{P}!{c}{RNIP}*{A}!$B${GR['归母分红率']}")
    ek(17,"分配股利 — 少数股东",[None]*NH,lambda c,i:f"=-{P}!{c}{RMI}*{A}!$B${GR['少数股东分红率']}")
    # 归集上限必须是【募资偿还之后】的短贷余额（{c}27 为负）——否则同一笔短贷会被
    # 「定向偿还」与「现金归集」重复偿还，短期借款会出现负数。
    ek(18,"循环贷净变动",[None]*NH,lambda c,i:(f"=IF({c}22<{MC},{MC}-{c}22,"
       f"IF(AND({c}22>{MC}+{SW},{B_}!{pc(c)}18+{c}27>0),"
       f"-MIN({B_}!{pc(c)}18+{c}27,{c}22-({MC}+{SW})),0))"),
       NUM,False,"现金低于最低余额则借入；高于阈值且仍有短贷则归还（上限＝募资偿还后的余额）")
    ek(26,"增发募资净额",[None]*NH,lambda c,i:f"={_fin_at(i, f'{A}!$B${FN[_K_NET]}')}",
       NUM,False,"到账年份一次性流入；同时计入归母权益（不走利润表）")
    ek(27,"偿还债务 — 短期借款",[None]*NH,
       lambda c,i:f"=-MIN({_fin_at(i, f'{A}!$B${FN[_K_DBT]}')},{B_}!{pc(c)}18)",
       NUM,False,"以期初短期借款余额封顶")
    ek(28,"偿还债务 — 长期借款",[None]*NH,
       lambda c,i:(f"=-MIN(MAX({_fin_at(i, f'{A}!$B${FN[_K_DBT]}')}-{B_}!{pc(c)}18,0),{B_}!{pc(c)}22)"),
       NUM,False,"短贷不足以覆盖时的剩余部分，以期初长期借款封顶")
    ek(31,"股份回购",[None]*NH,
       lambda c,i:f"=-{P}!{c}{RV}*{A}!$B${FN['年度回购金额 / 营收']}",NUM,False,
       "营收 × 回购率，负数=流出")
    ek(32,"　累计回购股数（亿股）",[None]*NH,
       lambda c,i:(f"={'0' if i==0 else f'{pc(c)}32'}"
                   f"+IF({A}!$B${FN['回购均价（元/股）']}>0,"
                   f"-{c}31/{A}!$B${FN['回购均价（元/股）']},0)"),NUM,False,
       "逐年累加；用于摊薄股数与 EPS，不进入 DCF 的每股计算")
    ek(19,"筹资活动现金流量净额",hrow(CF,"筹资活动产生的现金流量净额"),
       lambda c,i:f"={c}16+{c}17+{c}18+{c}26+{c}27+{c}28+{c}31",NUM,True)
    ek(20,"现金及现金等价物净增加额",hrow(CF,"现金及现金等价物净增加额"),lambda c,i:f"={c}13+{c}15+{c}19",NUM,True)
    ek(21,"期初现金",[None]*NH,lambda c,i:f"={pc(c)}23",NUM,False,None,f"={A}!B{OR_['货币资金']}")
    ek(22,"（辅助）筹资前现金余额",[None]*NH,lambda c,i:f"={c}21+{c}13+{c}15+{c}16+{c}17+{c}26+{c}27+{c}28+{c}31",NUM,False,
       "含募资流入与偿债流出——循环贷应在募资到位【之后】才判断是否触发")
    ek(23,"期末现金",hrow(CF,"期末现金及现金等价物余额"),lambda c,i:f"={c}21+{c}20",NUM,True,None,f"={A}!B{OR_['货币资金']}")
    ocfh=hrow(CF,"经营活动产生的现金流量净额")
    ek(24,"自由现金流 FCF（OCF − 资本开支）",
       [None if ocfh[i] is None or cxh[i] is None else round(ocfh[i]-cxh[i],4) for i in range(NH)],
       lambda c,i:f"={c}13+{c}14",NUM,True)
    ek(29,"　折旧（对固定资产计提）",[None]*NH,
       lambda c,i:f"={B_}!{pc(c)}12*{A}!$B${GR['折旧率（占期初固定资产净额）']}",NUM,False,
       "期初固定资产净额 × 折旧率。只有它减记固定资产")
    ek(30,"　摊销（无形/长期待摊/使用权）",[None]*NH,
       lambda c,i:f"={P}!{c}{RV}*{A}!$B${GR['无形摊销 / 营收']}",NUM,False,
       "营收 × 摊销率。减记「其他非流动资产」")
    ek(25,"FCFF（无杠杆自由现金流）",[None]*NH,
       lambda c,i:f"={P}!{c}{REBIT}*(1-{A}!$B${GR['实际所得税率']})+{c}7+{c}14+SUM({c}8:{c}12)+{c}33",NUM,True,
       "EBIT×(1−t) + 折旧摊销 − 资本开支 + 营运资本变动 + 非现金其他经营损益")
    for c in CC: wk[f"{c}22"].font=Font(size=10,color="808080",italic=True)
    cal["_ref"]=dict(RV=RV,RC=RC,RGP=RGP,ROP=ROP,RPBT=RPBT,RTAX=RTAX,
                     RNI=RNI,RMI=RMI,RNIP=RNIP,REPS=REPS,REBIT=REBIT)
    return _dcf_sheet(wb,cal,path)

def _dcf_sheet(wb, cal, path):
    M,PR,DC,GEN = cal["meta"],cal["periods"],cal["dcf"],cal["gen"]
    R=cal["_rows"]; RF=cal["_ref"]; CC=R["CC"]; NY=R["NY"]
    GR,OR_,DR,FN = R["GR"],R["OR_"],R["DR"],R["FN"]
    A="'1_假设'"; P="'2_利润表'"; B_="'3_资产负债表'"; K="'4_现金流量表'"
    YE=PR["proj"]; DCOL=[CL(2+i) for i in range(NY)]
    w=wb.create_sheet("5_DCF"); w.sheet_properties.tabColor="C00000"
    w.column_dimensions["A"].width=40
    for i in range(2,3+NY): w.column_dimensions[CL(i)].width=14
    w.column_dimensions[CL(3+NY)].width=52
    w.cell(1,1,f"{M['name']} {M['code']} — DCF 估值（全公式）").font=Font(bold=True,size=15,color=NAVY)
    w.cell(2,1,"绿色单元格链接自「1_假设」，改假设本页自动重算").font=Font(size=9,italic=True)
    NOTE=3+NY
    def sec(r,t):
        w.cell(r,1,t).font=F_HDR
        for c in range(1,3+NY): w.cell(r,c).fill=P_N
        return r+1
    def put(r,label,val,fmt=NUM,bold=False,note=None,link=False):
        c0=w.cell(r,1,label); c0.font=Font(bold=bold,size=10); c0.border=BX
        cc=w.cell(r,2,val); cc.font=F_LNK if link else (Font(bold=True,size=10) if bold else F_FX)
        cc.number_format=fmt
        if note: w.cell(r,NOTE,note).font=Font(size=9,color="808080")
        return r+1
    def row(r,label,fx,fmt=NUM,bold=False,note=None):
        c0=w.cell(r,1,label); c0.font=Font(bold=bold,size=10); c0.border=BX
        for i,src in enumerate(CC):
            cc=w.cell(r,2+i,fx(CL(2+i),src,i)); cc.number_format=fmt
            cc.font=Font(bold=True,size=10) if bold else F_FX
        if note: w.cell(r,NOTE,note).font=Font(size=9,color="808080")
        return r+1
    r=4; r=sec(r,"一、折现率 WACC")
    RFr=r; r=put(r,"无风险利率 Rf（中债10Y）",f"={A}!B{DR['无风险利率 Rf（中债10Y）']}",PCT2,False,None,True)
    ERPr=r;r=put(r,"股权风险溢价 ERP",f"={A}!B{DR['股权风险溢价 ERP']}",PCT2,False,None,True)
    BEr=r; r=put(r,"Beta（5年回归 Blume 调整）",f"={A}!B{DR['Beta（5年对上证综指回归+Blume）']}","0.000",False,f"原始 {M['beta_raw']}，样本 {M['beta_n']} 天",True)
    KEr=r; r=put(r,"股权成本 Ke = Rf + β×ERP",f"=B{RFr}+B{BEr}*B{ERPr}",PCT2,True)
    KDr=r; r=put(r,"债务成本 Kd（税前）",f"={A}!B{DR['债务成本 Kd（税前）']}",PCT2,False,None,True)
    TXr=r; r=put(r,"实际所得税率 t",f"={A}!B{GR['实际所得税率']}",PCT2,False,None,True)
    WDr=r; r=put(r,"债务权重 D/(D+E)",f"={A}!B{DR['债务权重 D/(D+E)']}",PCT2,False,None,True)
    WCr=r; r=put(r,"WACC",f"=(1-B{WDr})*B{KEr}+B{WDr}*B{KDr}*(1-B{TXr})",PCT2,True)
    GTr=r; r=put(r,"永续增长率 g",f"={A}!B{DR['永续增长率 g']}",PCT2,False,None,True)
    MYr=r; r=put(r,"期中折现开关（1=期中 0=期末）",f"={A}!B{DR['期中折现开关（1=是 0=否）']}","0",False,"现金流全年均匀发生时取 1",True)
    r+=1
    r=sec(r,"二、自由现金流 FCFF 与现值")
    w.cell(r,1,"科目（亿元）").font=F_COL; w.cell(r,1).fill=P_B
    for i,y in enumerate(YE):
        cc=w.cell(r,2+i,y); cc.font=F_COL; cc.fill=P_B; cc.alignment=Alignment(horizontal="center")
    r+=1
    Er=r;  r=row(r,"EBIT",lambda c,s,i:f"={P}!{s}{RF['REBIT']}")
    EAr=r; r=row(r,"EBIT × (1 − t)",lambda c,s,i:f"={c}{Er}*(1-$B${TXr})")
    DPr=r; r=row(r,"加：折旧摊销",lambda c,s,i:f"={K}!{s}7")
    CXr=r; r=row(r,"减：资本开支",lambda c,s,i:f"={K}!{s}14")
    WKr=r; r=row(r,"营运资本变动（现金影响）",lambda c,s,i:f"=SUM({K}!{s}8:{s}12)")
    NCr=r; r=row(r,"加：非现金其他经营损益",lambda c,s,i:f"={K}!{s}33",NUM,False,
                 "减值/公允价值变动/资产处置：已在 EBIT 中扣除，但不耗现金，按全额加回")
    FFr=r; r=row(r,"FCFF",lambda c,s,i:f"={c}{EAr}+{c}{DPr}+{c}{CXr}+{c}{WKr}+{c}{NCr}",NUM,True)
    Tr=r;  r=row(r,"折现年数 t",lambda c,s,i:i+1,"0")
    DFr=r; r=row(r,"折现因子（含期中折现开关）",lambda c,s,i:f"=1/(1+$B${WCr})^({c}{Tr}-0.5*$B${MYr})","0.0000")
    PVr=r; r=row(r,"FCFF 现值",lambda c,s,i:f"={c}{FFr}*{c}{DFr}",NUM,True)
    r+=1
    r=sec(r,"三、终值（Damodaran 一致性：稳态再投资率 = g / ROIC）")
    TTr=r; r=put(r,"终值所得税率 t_term",f"={A}!B{DR['终值所得税率 t_term']}",PCT2,False,"永续期不应假设税收优惠永存",True)
    TEr=r; r=put(r,f"{YE[-1]} 正常化 EBIT（加回非现金损益）",
                 f"={CL(1+NY)}{Er}+{CL(1+NY)}{NCr}",NUM,False,
                 "终值年 EBIT 里同样含非现金减值。明确期加回而终值不加回 = 自相矛盾，"
                 "故终值 ROIC 与终值 FCFF 均使用正常化口径")
    TNr=r; r=put(r,f"{YE[-1]} 营运资本 NWC",f"={B_}!{CC[-1]}31")
    TPr=r; r=put(r,f"{YE[-1]} 固定资产净额",f"={B_}!{CC[-1]}12")
    ICr=r; r=put(r,"投入资本 IC = NWC + 固定资产",f"=B{TNr}+B{TPr}",NUM,True)
    ROr=r; r=put(r,"终值 ROIC = EBIT×(1−t_term) / IC",f"=B{TEr}*(1-B{TTr})/B{ICr}",PCT2,True,"必须可持续，否则终值不成立")
    RRr=r; r=put(r,"稳态再投资率 RR = g / ROIC",f"=B{GTr}/B{ROr}",PCT2,True,"Damodaran：g = RR × ROIC，三者不可自由设定")
    TFr=r; r=put(r,"终值 FCFF = EBIT(1+g)(1−t_term)(1−RR)",f"=B{TEr}*(1+B{GTr})*(1-B{TTr})*(1-B{RRr})",NUM,True)
    TVr=r; r=put(r,"终值 TV = FCFF终 /(WACC − g)",f"=B{TFr}/(B{WCr}-B{GTr})",NUM,True)
    PTr=r; r=put(r,"终值现值 PV(TV)",f"=B{TVr}/(1+B{WCr})^({NY}-0.5*B{MYr})",NUM,True)
    r+=1
    r=sec(r,"四、企业价值 → 每股内在价值")
    S1=r; r=put(r,"明确期 FCFF 现值合计",f"=SUM(B{PVr}:{CL(1+NY)}{PVr})")
    S2=r; r=put(r,"终值现值",f"=B{PTr}")
    EV0=r;r=put(r,f"企业价值 EV（{PR['annual'][-1][:4]}-12-31 基准）",f"=B{S1}+B{S2}",NUM,True)
    RLr=r;r=put(r,"估值基准日滚动年数",f"={A}!B{DR['估值基准日滚动年数']}","0.000",False,"折现回基期年末后需滚回评估日",True)
    EVr=r;r=put(r,"企业价值 EV（滚动至评估日）",
                f"=B{EV0}*(1+B{WCr})^B{RLr}-B{FFr}*B{RLr}*(1+B{WCr})^(B{RLr}/2)",NUM,True,
                "滚动后须扣除基期年末至评估日已实现的自由现金流（按首年 FCFF 按比例摊，半期折现）")
    NDr=r;r=put(r,"减：净负债（基期）",f"={A}!B{OR_['短期借款']}+{A}!B{OR_['一年内到期非流动负债']}+{A}!B{OR_['长期借款']}-{A}!B{OR_['货币资金']}-{A}!B{OR_['交易性金融资产']}")
    NOr=r;r=put(r,"加：非经营资产",f"={A}!B{DR['非经营资产（加回）']}",NUM,False,"长期股权投资/投资性房地产/其他非流动金融资产",True)
    # 净负债按【基期】计量 → 未来募资的现金对 EV 不可见，必须在桥里单列，否则股本增加而权益不变 = 假稀释。
    FNr=r;r=put(r,"加：增发募资净额（现值）",
                f"={A}!B{FN['募资净额（亿元）']}/(1+B{WCr})^{A}!B{FN['桥接折现年数（0=视同已完成）']}",NUM,False,
                "募投若投向资本开支，该笔开支已在上方 FCFF 中扣减 —— 一进一出，无重复计价",True)
    GXr=r;r=put(r,"归属全体股东权益价值",f"=B{EVr}-B{NDr}+B{NOr}+B{FNr}",NUM,True)
    MMr=r;r=put(r,"少数股东扣除方式",f"={A}!B{DR['少数股东扣除方式（1=账面 2=价值）']}","0",False,"1=账面 2=按利润占比价值",True)
    MSr=r;r=put(r,"少数股东利润占比",f"={A}!B{DR['少数股东利润占比']}",PCT2,False,None,True)
    MIr=r;r=put(r,"减：少数股东权益",f"=IF(B{MMr}=1,{A}!B{OR_['少数股东权益']},B{GXr}*B{MSr})",NUM,False,"账面法低估其价值份额")
    EQr=r;r=put(r,"归母股权价值",f"=B{GXr}-B{MIr}",NUM,True)
    SHr=r;r=put(r,"总股本（亿股，含增发）",
                f"={A}!B{OR_['总股本（亿股）']}+{A}!B{FN['增发股数（亿股）']}",NUM,False,
                "增发股数为 0 时等于现有股本")
    PSr=r;r=put(r,"每股内在价值",f"=B{EQr}/B{SHr}",CNY,True)
    PRr=r;r=put(r,"现价",f"={A}!B{OR_['现价（元）']}",CNY)
    UPr=r;r=put(r,"上行 / 下行空间",f"=B{PSr}/B{PRr}-1",PCT,True)
    TWr=r;r=put(r,"终值占 EV 比重",f"=B{S2}/B{EV0}",PCT,False,"超过 75% 说明估值高度依赖终值假设")
    r+=1
    r=sec(r,"五、反向 DCF：现价隐含什么")
    IEr=r;r=put(r,"现价隐含归母股权价值",f"=B{PRr}*B{SHr}",NUM,True)
    IGr=r;r=put(r,"现价隐含全体股东权益价值",f"=IF(B{MMr}=1,B{IEr}+{A}!B{OR_['少数股东权益']},B{IEr}/(1-B{MSr}))")
    IVr=r;r=put(r,"现价隐含 EV（评估日）",f"=B{IGr}+B{NDr}-B{NOr}-B{FNr}",NUM,True)
    IZr=r;r=put(r,"现价隐含 EV（折回基期年末）",f"=B{IVr}/(1+B{WCr})^B{RLr}")
    ITr=r;r=put(r,"现价隐含终值现值",f"=B{IZr}-B{S1}")
    IMr=r;r=put(r,"现价隐含终值 TV",f"=B{ITr}*(1+B{WCr})^({NY}-0.5*B{MYr})")
    IXr=r;r=put(r,"现价隐含 终值FCFF 倍数",f"=B{IMr}/B{TFr}",MX,True)
    TMr=r;r=put(r,"模型理论倍数 = 1/(WACC − g)",f"=1/(B{WCr}-B{GTr})",MX)
    r=put(r,"倍数差异（隐含 ÷ 理论）",f"=B{IXr}/B{TMr}",MX,True,"倍数 >1 表示现价隐含的终值远高于模型")
    r+=1
    r=sec(r,"六、敏感性：每股内在价值（行 = WACC，列 = 永续增长率 g）")
    SHROW=r
    w.cell(r,1,"WACC \\ g").font=F_COL; w.cell(r,1).fill=P_B
    GS=[.020,.025,.030,.035,.040]
    for i,g in enumerate(GS):
        cc=w.cell(r,2+i,g); cc.font=F_COL; cc.fill=P_B; cc.number_format=PCT2
    r+=1
    for off in (-0.02,-0.01,0.0,0.01,0.02):     # 等距，且中间档 = 生效 WACC（公式引用，非硬编码）
        cc=w.cell(r,1,f"=$B${WCr}+{off}"); cc.font=F_COL; cc.fill=P_G; cc.number_format=PCT2
        for i in range(5):
            col=CL(2+i)
            pv="+".join(f"${CL(2+t)}${FFr}/(1+$A{r})^({t+1}-0.5*$B${MYr})" for t in range(NY))
            tf=(f"$B${TEr}*(1+{col}${SHROW})*(1-$B${TTr})"
                f"*(1-{col}${SHROW}/($B${TEr}*(1-$B${TTr})/$B${ICr}))")
            tvpv=f"({tf})/($A{r}-{col}${SHROW})/(1+$A{r})^({NY}-0.5*$B${MYr})"
            ev=(f"(({pv})+{tvpv})*(1+$A{r})^$B${RLr}"
                f"-$B${FFr}*$B${RLr}*(1+$A{r})^($B${RLr}/2)")
            gx=f"({ev}-$B${NDr}+$B${NOr}+$B${FNr})"
            eq=f"{gx}*(1-IF($B${MMr}=1,0,$B${MSr}))-IF($B${MMr}=1,{A}!$B${OR_['少数股东权益']},0)"
            f=f"=({eq})/$B${SHr}"
            cc=w.cell(r,2+i,f); cc.font=F_FX; cc.number_format=CNY
        r+=1
    r+=1
    _engine(wb, cal, dict(WC=WCr,GT=GTr,MY=MYr,TT=TTr,RL=RLr,ND=NDr,NO=NOr,MM=MMr,MS=MSr,SH=SHr,
                          FN=FNr,PS=PSr,PR=PRr,EV0=EV0,SHROW=SHROW,GSTART=r))
    _ratios(wb, cal, dict(WC=WCr,TT=TTr))
    wb.save(path)
    return dict(path=path, dcf_rows=dict(FF=FFr,PS=PSr,UP=UPr,TF=TFr,RO=ROr,RR=RRr,IC=ICr,
                TE=TEr,TN=TNr,S1=S1,S2=S2,EV0=EV0,EV=EVr,ND=NDr,NO=NOr,GX=GXr,MI=MIr,EQ=EQr,
                SH=SHr,WC=WCr,GT=GTr,MY=MYr,RL=RLr,TT=TTr,MM=MMr,MS=MSr,TW=TWr,IX=IXr,TM=TMr,FN=FNr))


# ============================================================
# 敏感性引擎：每个格子 = 一次完整的 5 年 FCFF 重算
# 可行的前提：FCFF = EBIT(1−t)+折旧−资本开支+ΔNWC，而 EBIT = 营业利润+财务费用，
# 财务费用被抵消 → FCFF 不依赖现金/循环贷 plug，可在单行内闭合计算。
# ============================================================
def _engine(wb, cal, D_):
    M,PR,GEN,DC = cal["meta"],cal["periods"],cal["gen"],cal["dcf"]
    R=cal["_rows"]; GR,OR_,DR,FN = R["GR"],R["OR_"],R["DR"],R["FN"]
    NY=R["NY"]; CX_ROW=R["CX_ROW"]; G_ROW=R["G_ROW"]; M_ROW=R["M_ROW"]; IT_=R.get("IT") or {}
    A="'1_假设'"; E="'6_敏感性引擎'"; DS="'5_DCF'"
    w=wb.create_sheet("6_敏感性引擎"); w.sheet_properties.tabColor="7F7F7F"
    w.column_dimensions["A"].width=10
    for i in range(2,4): w.column_dimensions[CL(i)].width=11
    w.cell(1,1,f"{M['name']} {M['code']} — 敏感性引擎（每行 = 一次完整 5 年 FCFF 重算）").font=Font(bold=True,size=13,color=NAVY)
    w.cell(2,1,"本页为计算中间层，不需要手工编辑；「5_DCF」页的敏感性表从这里取数。改「1_假设」后本页自动重算。").font=Font(size=9,italic=True,color="808080")

    # ---- 基期锚点 ----
    r=4; w.cell(r,1,"基期锚点与生效参数").font=F_HDR
    for c in range(1,4): w.cell(r,c).fill=P_N
    r+=1
    anchors=[("基期营业总收入",f"={A}!B{OR_['基期营业总收入']}"),
             ("期初固定资产净额",f"={A}!B{OR_['固定资产净额']}"),
             ("期初 NWC",f"={A}!B{OR_['应收票据及应收账款']}+{A}!B{OR_['存货']}+{A}!B{OR_['其他流动资产']}-{A}!B{OR_['应付票据及应付账款']}-{A}!B{OR_['其他流动负债']}"),
             ("经营费用率合计",f"={A}!B{GR['税金及附加率']}+{A}!B{GR['销售费用率']}+{A}!B{GR['管理费用率']}+{A}!B{GR['研发费用率']}"),
             ("其他收益+其他经营损益率",f"={A}!B{GR['其他收益 / 营收']}+{A}!B{GR['其他经营损益 / 营收']}"),
             ("非现金其他经营损益率",f"=-{A}!B{GR['其他经营损益 / 营收']}"),
             ("减值减记固定资产占比",f"={A}!B{GR['非现金减值减记中固定资产占比']}"),
             ("折旧率",f"={A}!B{GR['折旧率（占期初固定资产净额）']}"),
             ("摊销率",f"={A}!B{GR['无形摊销 / 营收']}"),
             ("无形资本开支比重",f"={A}!B{GR['资本开支投向无形/长期待摊的比重']}"),
             ("实际税率",f"={A}!B{GR['实际所得税率']}"),
             ("终值税率",f"={DS}!B{D_['TT']}"),
             ("WACC（生效）",f"={DS}!B{D_['WC']}"),
             ("永续 g（生效）",f"={DS}!B{D_['GT']}"),
             ("期中折现开关",f"={DS}!B{D_['MY']}"),
             ("滚动年数",f"={DS}!B{D_['RL']}"),
             ("净负债",f"={DS}!B{D_['ND']}"),
             ("非经营资产",f"={DS}!B{D_['NO']}"),
             ("少数股东方式",f"={DS}!B{D_['MM']}"),
             ("少数股东占比",f"={DS}!B{D_['MS']}"),
             ("账面少数股东权益",f"={A}!B{OR_['少数股东权益']}"),
             ("总股本",f"={DS}!B{D_['SH']}"),
             ("募资净额现值",f"={DS}!B{D_['FN']}")]
    _BY=f"{A}!$B${FN['募投资本开支建设期（年）']}"; _YR=f"{A}!$B${FN['到账年份序号（1=首个预测年）']}"
    _UC=f"{A}!$B${FN['投向 — 资本开支（亿元）']}"
    anchors += [(f"募投资本开支{t}",
                 f"=IF(AND({_BY}>0,{t}>={_YR},{t}<={_YR}+{_BY}-1),{_UC}/{_BY},0)") for t in range(1,NY+1)]
    AR_={}
    for lbl,f in anchors:
        w.cell(r,1,lbl).font=Font(size=10); w.cell(r,1).border=BX
        cc=w.cell(r,2,f); cc.font=F_LNK; cc.number_format=NUM if not any(k in lbl for k in ("率","开关","年数","方式","占比","比重")) else "0.0000"
        AR_[lbl]=r; r+=1
    r+=1

    C0=4                      # 年度块起始列
    W_=8                      # 每年 8 列
    def yc(t,k):              # t=1..NY, k=0..7
        return CL(C0 + W_*(t-1) + k)
    SUM0 = C0 + W_*NY
    SC={n:CL(SUM0+i) for i,n in enumerate(["PV","ROIC","RR","TF","TV","PVTV","EV0","EV","GX","MI","EQ","PS"])}
    for i in range(C0, SUM0+12): w.column_dimensions[CL(i)].width=11

    GRIDS=[]
    def block(title, xs, ys, xlabel, ylabel, mode, note):
        """mode: 'g_gterm' | 'gm_gterm' | 'g_gm' | 'wacc_gm'"""
        nonlocal r
        w.cell(r,1,title).font=F_HDR
        for c in range(1, SUM0+12): w.cell(r,c).fill=P_N
        r+=1
        w.cell(r,1,note).font=Font(size=9,color="C00000"); r+=1
        hdr=["场景",xlabel,ylabel]
        for t in range(1,NY+1):
            hdr += [f"营收{t}",f"成本{t}",f"EBIT{t}",f"固资{t}",f"折旧{t}",f"资本开支{t}",f"NWC{t}",f"FCFF{t}"]
        hdr += ["PV明确期","终值ROIC","再投资率","终值FCFF","TV","PV(TV)","EV0","EV","全体权益","少数股东","归母权益","每股"]
        for i,h in enumerate(hdr):
            cc=w.cell(r,1+i,h); cc.font=F_COL; cc.fill=P_B; cc.alignment=Alignment(horizontal="center")
        r+=1
        start=r
        for xi,xv in enumerate(xs):
            for yi,yv in enumerate(ys):
                w.cell(r,1,f"{xi+1}-{yi+1}").font=Font(size=9,color="808080")
                if mode=="wacc_gm":
                    off=xv  # 相对生效 WACC 的偏移
                    cx=w.cell(r,2,f"=$B${AR_['WACC（生效）']}+{off}")
                else:
                    cx=w.cell(r,2,xv)
                cx.font=F_IN; cx.number_format=PCT2
                cy=w.cell(r,3,yv); cy.font=F_IN; cy.number_format=PCT2
                # 每年的增速 / 毛利率 / WACC / g 来源
                _SW=(IT_ or {}).get('锚定开关（1=锚定 0=按增速外推）')
                def _d(t, dcell):
                    # 锚定开启时首年为既成事实，Δ 不应移动它
                    return f"IF({A}!$B${_SW}=1,0,{dcell})" if (t==1 and _SW) else dcell
                LG=lambda t:f"{A}!$E${G_ROW+t-1}"; LM=lambda t:f"{A}!$E${M_ROW+t-1}"
                LW=f"$B${AR_['WACC（生效）']}"; LGT=f"$B${AR_['永续 g（生效）']}"
                if mode=="g_gterm":   gexp=lambda t:f"({LG(t)}+{_d(t,f'$B{r}')})"; gmexp=LM; wexp=LW; gtexp=f"$C{r}"
                elif mode=="gm_gterm":gexp=LG; gmexp=lambda t:f"({LM(t)}+{_d(t,f'$B{r}')})"; wexp=LW; gtexp=f"$C{r}"
                elif mode=="g_gm":    gexp=lambda t:f"({LG(t)}+{_d(t,f'$B{r}')})"; gmexp=lambda t:f"({LM(t)}+{_d(t,f'$C{r}')})"; wexp=LW; gtexp=LGT
                else:                 gexp=LG; gmexp=lambda t:f"({LM(t)}+{_d(t,f'$C{r}')})"; wexp=f"$B{r}"; gtexp=LGT
                for t in range(1,NY+1):
                    prev_rev = f"$B${AR_['基期营业总收入']}" if t==1 else f"{yc(t-1,0)}{r}"
                    prev_ppe = f"$B${AR_['期初固定资产净额']}" if t==1 else f"{yc(t-1,3)}{r}"
                    prev_nwc = f"$B${AR_['期初 NWC']}" if t==1 else f"{yc(t-1,6)}{r}"
                    w[f"{yc(t,0)}{r}"]=f"={prev_rev}*(1+{gexp(t)})"                                   # 营收
                    w[f"{yc(t,1)}{r}"]=f"={yc(t,0)}{r}*(1-{gmexp(t)})"                                # 成本
                    w[f"{yc(t,2)}{r}"]=(f"={yc(t,0)}{r}-{yc(t,1)}{r}-{yc(t,0)}{r}*$B${AR_['经营费用率合计']}"
                                        f"+{yc(t,0)}{r}*$B${AR_['其他收益+其他经营损益率']}")                    # EBIT
                    w[f"{yc(t,4)}{r}"]=f"={prev_ppe}*$B${AR_['折旧率']}"                               # 折旧
                    w[f"{yc(t,5)}{r}"]=(f"={yc(t,0)}{r}*{A}!$E${CX_ROW+t-1}"
                                        f"+$B${AR_[f'募投资本开支{t}']}")                                # 资本开支（含募投）
                    w[f"{yc(t,3)}{r}"]=(f"={prev_ppe}+{yc(t,5)}{r}*(1-$B${AR_['无形资本开支比重']})"
                                        f"-{yc(t,4)}{r}-{yc(t,0)}{r}*$B${AR_['非现金其他经营损益率']}"
                                        f"*$B${AR_['减值减记固定资产占比']}")                              # 期末固资
                    w[f"{yc(t,6)}{r}"]=(f"={yc(t,0)}{r}*{A}!$B${GR['应收周转天数 DSO']}/365"
                                        f"+{yc(t,1)}{r}*{A}!$B${GR['存货周转天数 DIO']}/365"
                                        f"+{yc(t,0)}{r}*{A}!$B${GR['其他流动资产 / 营收']}"
                                        f"-{yc(t,1)}{r}*{A}!$B${GR['应付周转天数 DPO']}/365"
                                        f"-{yc(t,0)}{r}*{A}!$B${GR['其他流动负债 / 营收']}")            # NWC
                    w[f"{yc(t,7)}{r}"]=(f"={yc(t,2)}{r}*(1-$B${AR_['实际税率']})+{yc(t,4)}{r}"
                                        f"+{yc(t,0)}{r}*$B${AR_['摊销率']}-{yc(t,5)}{r}"
                                        f"-({yc(t,6)}{r}-{prev_nwc})"
                                        f"+{yc(t,0)}{r}*$B${AR_['非现金其他经营损益率']}")              # FCFF
                    for k in range(8): w[f"{yc(t,k)}{r}"].number_format=NUM; w[f"{yc(t,k)}{r}"].font=F_FX
                pv="+".join(f"{yc(t,7)}{r}/(1+{wexp})^({t}-0.5*$B${AR_['期中折现开关']})" for t in range(1,NY+1))
                w[f"{SC['PV']}{r}"]=f"={pv}"
                _tebit=(f"({yc(NY,2)}{r}+{yc(NY,0)}{r}*$B${AR_['非现金其他经营损益率']})")   # 正常化终值 EBIT
                w[f"{SC['ROIC']}{r}"]=f"={_tebit}*(1-$B${AR_['终值税率']})/({yc(NY,6)}{r}+{yc(NY,3)}{r})"
                w[f"{SC['RR']}{r}"]=f"={gtexp}/{SC['ROIC']}{r}"
                w[f"{SC['TF']}{r}"]=f"={_tebit}*(1+{gtexp})*(1-$B${AR_['终值税率']})*(1-{SC['RR']}{r})"
                w[f"{SC['TV']}{r}"]=f"={SC['TF']}{r}/({wexp}-{gtexp})"
                w[f"{SC['PVTV']}{r}"]=f"={SC['TV']}{r}/(1+{wexp})^({NY}-0.5*$B${AR_['期中折现开关']})"
                w[f"{SC['EV0']}{r}"]=f"={SC['PV']}{r}+{SC['PVTV']}{r}"
                w[f"{SC['EV']}{r}"]=(f"={SC['EV0']}{r}*(1+{wexp})^$B${AR_['滚动年数']}"
                                     f"-{yc(1,7)}{r}*$B${AR_['滚动年数']}*(1+{wexp})^($B${AR_['滚动年数']}/2)")
                w[f"{SC['GX']}{r}"]=(f"={SC['EV']}{r}-$B${AR_['净负债']}+$B${AR_['非经营资产']}"
                                     f"+$B${AR_['募资净额现值']}")
                w[f"{SC['MI']}{r}"]=f"=IF($B${AR_['少数股东方式']}=1,$B${AR_['账面少数股东权益']},{SC['GX']}{r}*$B${AR_['少数股东占比']})"
                w[f"{SC['EQ']}{r}"]=f"={SC['GX']}{r}-{SC['MI']}{r}"
                w[f"{SC['PS']}{r}"]=f"={SC['EQ']}{r}/$B${AR_['总股本']}"
                for n,c_ in SC.items():
                    w[f"{c_}{r}"].font=F_FX
                    w[f"{c_}{r}"].number_format=PCT2 if n in("ROIC","RR") else (CNY if n=="PS" else NUM)
                r+=1
        r+=1
        GRIDS.append(dict(title=title,xs=xs,ys=ys,xlabel=xlabel,ylabel=ylabel,start=start,note=note))
        return start

    gt=DC["g"]
    ke=DC["rf"]+DC["beta"]*DC["erp"]
    wacc_c=round((1-DC["wd"])*ke+DC["wd"]*DC["kd"]*(1-GEN["tax"]),6)   # 与 5_DCF 完全一致
    gs =[round(gt-.01,4),round(gt-.005,4),gt,round(gt+.005,4),round(gt+.01,4)]
    gxs=[-.10,-.05,0.0,.05,.10]          # 营收增速：相对生效路径的平移
    gms=[-.04,-.02,0.0,.02,.04]          # 毛利率：相对生效路径的平移
    ws_=[-.02,-.01,0.0,.012,.022]          # WACC：相对生效 WACC 的偏移（轴格为公式，避免四舍五入）
    _g=list(cal["judg"]["growth"]); _m=list(cal["judg"]["gm"])
    _IT=cal.get("interim")
    if _IT and _IT.get("share"):        # 锚定开启时首年生效值由中期反推
        _fy_r=_IT["rev"]/_IT["share"]
        _fy_c=_IT["cogs"]+(_fy_r-_IT["rev"])*(1-_IT["gm"])
        _g[0]=_fy_r/cal["rev"][cal["periods"]["annual"][-1]]-1
        _m[0]=1-_fy_c/_fy_r
    gp="｜".join(f"{x*100:.1f}%" for x in _g)
    mp="｜".join(f"{x*100:.1f}%" for x in _m)
    block("模块 A：营收增速平移 × 永续增长率 g",gxs,gs,"营收增速 Δ","永续 g","g_gterm",
          f"行 = 对生效营收增速路径整体平移的百分点；列 = 永续增长率。Δ=0 行必然复现基准。生效路径：{gp}")
    block("模块 B：毛利率平移 × 永续增长率 g",gms,gs,"毛利率 Δ","永续 g","gm_gterm",
          f"行 = 对生效毛利率路径整体平移的百分点；列 = 永续增长率。Δ=0 行必然复现基准。生效路径：{mp}")
    block("模块 C：营收增速平移 × 毛利率平移",gxs,gms,"营收增速 Δ","毛利率 Δ","g_gm",
          "两个判断项互相对撞——通常是最有决策价值的一张。中心格 (0,0) 必然复现基准。")
    block("模块 D：WACC × 毛利率平移",ws_,gms,"WACC","毛利率 Δ","wacc_gm",
          "行 = WACC 绝对值（中间一行 = 生效 WACC）；列 = 毛利率平移。中心格必然复现基准。")
    cal["_grids"]=GRIDS; cal["_engine_ps"]=SC["PS"]
    _grid_tables(wb, cal, D_)


def _grid_tables(wb, cal, D_):
    """把 4 张敏感性展示表写到 5_DCF 页，取数自「6_敏感性引擎」。"""
    w=wb["5_DCF"]; NY=cal["_rows"]["NY"]; PSC=cal["_engine_ps"]; E="'6_敏感性引擎'"
    r=D_["GSTART"]
    NOTE=3+NY
    def sec(rr,t):
        w.cell(rr,1,t).font=F_HDR
        for c in range(1,3+NY): w.cell(rr,c).fill=P_N
        return rr+1
    for gi,G in enumerate(cal["_grids"]):
        r=sec(r,f"{G['title']}　—　每股内在价值")
        w.cell(r,1,G["note"]).font=Font(size=9,color="C00000"); r+=1
        cc=w.cell(r,1,f"{G['xlabel']} \\ {G['ylabel']}"); cc.font=F_COL; cc.fill=P_B
        for j,yv in enumerate(G["ys"]):
            c2=w.cell(r,2+j,yv); c2.font=F_COL; c2.fill=P_B; c2.number_format=PCT2
        r+=1
        for i,xv in enumerate(G["xs"]):
            src0=G["start"]+i*len(G["ys"])
            c1=w.cell(r,1,f"={E}!$B${src0}" if G["xlabel"]=="WACC" else xv)
            c1.font=F_COL; c1.fill=P_G; c1.number_format=PCT2
            for j in range(len(G["ys"])):
                src=G["start"]+i*len(G["ys"])+j
                c2=w.cell(r,2+j,f"={E}!{PSC}{src}"); c2.font=F_FX; c2.number_format=CNY
            r+=1
        # 基准行列高亮提示
        w.cell(r,1,"★ 中心格（Δ=0 / 生效WACC，且 g=生效值）必然等于上方「每股内在价值」——不等即模型有误").font=Font(size=9,color="006400")
        r+=2
    w.cell(r,1,"免责声明：本模型为研究性分析工具，非投资建议。判断项假设由使用者设定，非公司指引或市场一致预期。").font=Font(size=9,color="C00000")


# ============================================================
# 7_财务比率与杜邦：全部为活公式，引用三张报表；含恒等式自检与图表
# ============================================================
def _ratios(wb, cal, D_):
    M,PR = cal["meta"],cal["periods"]
    R=cal["_rows"]; RF=cal["_ref"]
    HC,CC,NH,NY = R["HC"],R["CC"],R["NH"],R["NY"]
    HL,YE = PR["labels"],PR["proj"]
    ALL=HC+CC
    P="'2_利润表'"; B_="'3_资产负债表'"; K="'4_现金流量表'"; DS="'5_DCF'"
    RV,RC,RGP,ROP,RPBT,RTAX,RNI,RMI,RNIP,REBIT = (RF[k] for k in
        ("RV","RC","RGP","ROP","RPBT","RTAX","RNI","RMI","RNIP","REBIT"))
    w=wb.create_sheet("7_财务比率与杜邦"); w.sheet_properties.tabColor="548235"
    w.column_dimensions["A"].width=32
    for i in range(2,2+NH+NY): w.column_dimensions[CL(i)].width=12
    w.cell(1,1,f"{M['name']} {M['code']} — 主要财务比率与杜邦分析").font=Font(bold=True,size=14,color=NAVY)
    w.cell(2,1,"全部为活公式，取自三张报表；改「1_假设」本页与下方图表自动重算。历史列为实际、预测列为模型输出。").font=Font(size=9,italic=True)
    def hdr(r,title):
        w.cell(r,1,title).font=F_HDR
        for c in range(1,2+NH+NY): w.cell(r,c).fill=P_N
        r+=1
        w.cell(r,1,"指标").font=F_COL; w.cell(r,1).fill=P_B
        for i,y in enumerate(HL+YE):
            cc=w.cell(r,2+i,y); cc.font=F_COL; cc.alignment=Alignment(horizontal="center")
            cc.fill=P_G if i<NH else P_B
        return r+1
    def line(r,label,fx,fmt=PCT,bold=False,note=None,fill=None):
        c0=w.cell(r,1,label); c0.font=Font(bold=bold,size=10); c0.border=BX
        if note: c0.comment=Comment(note,"Template")
        for i,c in enumerate(ALL):
            prev = ALL[i-1] if i>0 else None
            f=fx(c,prev,i)
            cc=w.cell(r,2+i, f if f else None); cc.number_format=fmt
            cc.font=Font(bold=True,size=10) if bold else (F_H if i<NH else F_FX)
            if fill: cc.fill=fill
        return r+1
    def avg(sheet,row,c,prev):
        return f"AVERAGE({sheet}!{prev}{row},{sheet}!{c}{row})" if prev else f"{sheet}!{c}{row}"
    IE=lambda x:f"=IFERROR({x},\"\")"
    r=4
    r=hdr(r,"一、盈利能力")
    ROWS={}
    r=line(r,"毛利率",       lambda c,p,i: IE(f"{P}!{c}{RGP}/{P}!{c}{RV}"))
    r=line(r,"营业利润率",   lambda c,p,i: IE(f"{P}!{c}{ROP}/{P}!{c}{RV}"))
    ROWS["ebitm"]=r
    r=line(r,"EBIT 利润率",  lambda c,p,i: IE(f"{P}!{c}{REBIT}/{P}!{c}{RV}"))
    ROWS["npm"]=r
    r=line(r,"归母净利率",   lambda c,p,i: IE(f"{P}!{c}{RNIP}/{P}!{c}{RV}"))
    ROWS["roe"]=r
    r=line(r,"ROE（归母，平均净资产）", lambda c,p,i: IE(f"{P}!{c}{RNIP}/{avg(B_,25,c,p)}"),PCT,True)
    ROWS["roa"]=r
    r=line(r,"ROA（净利润 / 平均总资产）", lambda c,p,i: IE(f"{P}!{c}{RNI}/{avg(B_,16,c,p)}"))
    ROWS["roic"]=r
    r=line(r,"ROIC（税后 / 投入资本）",
           lambda c,p,i: IE(f"{P}!{c}{REBIT}*(1-{DS}!$B${D_['TT']})/({B_}!{c}31+{B_}!{c}12)"),PCT,True,
           "投入资本 = 营运资本 NWC + 固定资产净额；与 DCF 终值 ROIC 同口径")
    ROWS["wacc"]=r
    r=line(r,"（对照）WACC", lambda c,p,i: f"={DS}!$B${D_['WC']}",PCT,False,"ROIC 持续低于 WACC 即为价值毁灭")
    r+=1
    r=hdr(r,"二、营运效率")
    ROWS["dso"]=r
    r=line(r,"应收周转天数 DSO", lambda c,p,i: IE(f"{B_}!{c}8/{P}!{c}{RV}*365"),"0.0")
    ROWS["dio"]=r
    r=line(r,"存货周转天数 DIO", lambda c,p,i: IE(f"{B_}!{c}9/{P}!{c}{RC}*365"),"0.0")
    ROWS["dpo"]=r
    r=line(r,"应付周转天数 DPO", lambda c,p,i: IE(f"{B_}!{c}19/{P}!{c}{RC}*365"),"0.0")
    ROWS["ccc"]=r
    r=line(r,"现金转换周期 CCC", lambda c,p,i: IE(f"{B_}!{c}8/{P}!{c}{RV}*365+{B_}!{c}9/{P}!{c}{RC}*365-{B_}!{c}19/{P}!{c}{RC}*365"),
           "0.0",True,"DSO + DIO − DPO；为负说明占用上游资金")
    ROWS["ato"]=r
    r=line(r,"总资产周转率", lambda c,p,i: IE(f"{P}!{c}{RV}/{avg(B_,16,c,p)}"),MX)
    r=line(r,"固定资产周转率", lambda c,p,i: IE(f"{P}!{c}{RV}/{avg(B_,12,c,p)}"),MX)
    r+=1
    r=hdr(r,"三、偿债与资本结构")
    r=line(r,"资产负债率", lambda c,p,i: IE(f"{B_}!{c}24/{B_}!{c}16"))
    r=line(r,"净负债 / 归母权益", lambda c,p,i: IE(f"{B_}!{c}30/{B_}!{c}25"))
    r=line(r,"有息负债 / EBITDA", lambda c,p,i: IE(f"({B_}!{c}18+{B_}!{c}21+{B_}!{c}22)/({P}!{c}{REBIT}+{K}!{c}7)"),MX)
    r=line(r,"EBIT 利息保障倍数", lambda c,p,i: IE(f"{P}!{c}{REBIT}/{P}!{c}17"),MX)
    ROWS["em"]=r
    r=line(r,"权益乘数（总资产 / 归母权益）", lambda c,p,i: IE(f"{avg(B_,16,c,p)}/{avg(B_,25,c,p)}"),MX)
    r+=1
    r=hdr(r,"四、现金流质量")
    r=line(r,"经营现金流 / 净利润", lambda c,p,i: IE(f"{K}!{c}13/{P}!{c}{RNI}"),MX,False,"持续 <1 说明利润未转化为现金")
    r=line(r,"自由现金流 / 营收", lambda c,p,i: IE(f"{K}!{c}24/{P}!{c}{RV}"))
    r=line(r,"资本开支 / 折旧", lambda c,p,i: IE(f"-{K}!{c}14/{K}!{c}7"),MX,False,">1 为扩张，≈1 为维持")
    r=line(r,"FCFF / 税后EBIT", lambda c,p,i: IE(f"{K}!{c}25/({P}!{c}{REBIT}*(1-{DS}!$B${D_['TT']}))"),MX)
    r+=1
    r=hdr(r,"五、成长性")
    r=line(r,"营业收入增速", lambda c,p,i: IE(f"{P}!{c}{RV}/{P}!{p}{RV}-1") if p else None)
    r=line(r,"归母净利增速", lambda c,p,i: IE(f"{P}!{c}{RNIP}/{P}!{p}{RNIP}-1") if p else None)
    r=line(r,"EBIT 增速",   lambda c,p,i: IE(f"{P}!{c}{REBIT}/{P}!{p}{REBIT}-1") if p else None)
    r+=1
    # ---------- 杜邦 ----------
    r=hdr(r,"六、杜邦三因子分解（归母 ROE）")
    d3=r
    r=line(r,"① 归母净利率 = 归母净利 / 营收", lambda c,p,i: IE(f"{P}!{c}{RNIP}/{P}!{c}{RV}"))
    r=line(r,"② 总资产周转率 = 营收 / 平均总资产", lambda c,p,i: IE(f"{P}!{c}{RV}/{avg(B_,16,c,p)}"),MX)
    r=line(r,"③ 权益乘数 = 平均总资产 / 平均归母权益", lambda c,p,i: IE(f"{avg(B_,16,c,p)}/{avg(B_,25,c,p)}"),MX)
    r=line(r,"① × ② × ③ = 归母 ROE", lambda c,p,i: IE(f"{c}{d3}*{c}{d3+1}*{c}{d3+2}"),PCT,True)
    chk3=r
    r=line(r,"★ 三因子恒等式校验（连乘 − 直接计算）", lambda c,p,i: IE(f"{c}{d3+3}-{c}{ROWS['roe']}"),Z,True,
           "必须为 0",P_O)
    r+=1
    r=hdr(r,"七、杜邦五因子分解")
    d5=r
    r=line(r,"① 税负比率 = 净利润 / 利润总额", lambda c,p,i: IE(f"{P}!{c}{RNI}/{P}!{c}{RPBT}"),MX)
    r=line(r,"② 利息负担 = 利润总额 / EBIT", lambda c,p,i: IE(f"{P}!{c}{RPBT}/{P}!{c}{REBIT}"),MX)
    r=line(r,"③ EBIT 利润率 = EBIT / 营收", lambda c,p,i: IE(f"{P}!{c}{REBIT}/{P}!{c}{RV}"))
    r=line(r,"④ 总资产周转率", lambda c,p,i: IE(f"{P}!{c}{RV}/{avg(B_,16,c,p)}"),MX)
    r=line(r,"⑤ 权益乘数", lambda c,p,i: IE(f"{avg(B_,16,c,p)}/{avg(B_,25,c,p)}"),MX)
    r=line(r,"⑥ 归母占比 = 归母净利 / 净利润", lambda c,p,i: IE(f"{P}!{c}{RNIP}/{P}!{c}{RNI}"),MX,False,
           "五因子原式得全口径 ROE，乘此项转为归母口径")
    r=line(r,"①×②×③×④×⑤×⑥ = 归母 ROE",
           lambda c,p,i: IE(f"{c}{d5}*{c}{d5+1}*{c}{d5+2}*{c}{d5+3}*{c}{d5+4}*{c}{d5+5}"),PCT,True)
    chk5=r
    r=line(r,"★ 五因子恒等式校验（连乘 − 直接计算）", lambda c,p,i: IE(f"{c}{d5+6}-{c}{ROWS['roe']}"),Z,True,
           "必须为 0",P_O)
    cal["_ratio_rows"]=dict(ROWS=ROWS,d3=d3,d5=d5,chk3=chk3,chk5=chk5,last=r)
    _charts(w, cal, ROWS, d3, r+2)



# ============================================================
# 图表样式：对齐卖方研究报告版式
#   深红/卡其/藏蓝三色主调 · 标题左对齐红色加粗 · 图例置顶
#   仅浅色横向网格 · 无边框无填充 · 折线带圆点 · 数据标签直出
# ============================================================
C_RED="9E2B25"; C_KHAKI="C4A85A"; C_NAVY="1F3864"; C_CORAL="C0504D"
C_SLATE="8497B0"; C_GREY="A6A6A6"; C_GRID="D9D9D9"; C_TXT="595959"
PALETTE=[C_RED, C_KHAKI, C_NAVY, C_CORAL, C_SLATE, C_GREY]

def _rt(text, size=1100, bold=True, color=C_RED, align="l"):
    cp=CharacterProperties(sz=size, b=bold, solidFill=color)
    p=Paragraph(pPr=ParagraphProperties(defRPr=cp, algn=align), endParaRPr=cp,
                r=[RegularTextRun(t=text, rPr=cp)])
    t=Title(tx=Text(rich=RichText(p=[p])))
    t.overlay=False
    # 手工锚定到左上角：避免标题框居中后与其他元素争位
    t.layout=Layout(manualLayout=ManualLayout(xMode="edge", yMode="edge", x=0.01, y=0.01))
    return t

def _axis_text(size=900, color=C_TXT):
    cp=CharacterProperties(sz=size, solidFill=color)
    return RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), endParaRPr=cp)])

def _skin(ch, title, num_fmt=None, labels=False, label_fmt=None, h=8.0, w_=17.5):
    ch.title=_rt(title)
    ch.height=h; ch.width=w_; ch.style=None
    ch.graphical_properties=GraphicalProperties(noFill=True, ln=LineProperties(noFill=True))
    ch.y_axis.majorGridlines=ChartLines(spPr=GraphicalProperties(
        ln=LineProperties(solidFill=C_GRID, w=6350)))
    ch.x_axis.majorGridlines=None
    for ax in (ch.x_axis, ch.y_axis):
        ax.spPr=GraphicalProperties(ln=LineProperties(solidFill=C_GRID, w=6350))
        ax.txPr=_axis_text()
        ax.majorTickMark="none"; ax.minorTickMark="none"
    ch.y_axis.delete=False; ch.x_axis.delete=False
    ch.layout=Layout(manualLayout=ManualLayout(
        xMode="edge", yMode="edge", x=0.06, y=0.14, w=0.90, h=0.68))
    if num_fmt: ch.y_axis.numFmt=num_fmt
    if ch.legend is not None:
        ch.legend.position="b"          # 底部：与标题彻底分离，杜绝重叠
        ch.legend.overlay=False
        ch.legend.txPr=_axis_text(900, C_TXT)
    if labels:
        ch.dLbls=DataLabelList()
        ch.dLbls.showVal=True; ch.dLbls.showSerName=False; ch.dLbls.showCatName=False
        ch.dLbls.showLegendKey=False; ch.dLbls.showBubbleSize=False
        ch.dLbls.txPr=_axis_text(800, C_TXT)
        if label_fmt: ch.dLbls.numFmt=label_fmt
    return ch

def _bar_series(ref, name, color):
    s=Series(ref, title=name)
    s.graphicalProperties=GraphicalProperties(solidFill=color, ln=LineProperties(noFill=True))
    return s

def _line_series(ref, name, color, marker=True):
    s=Series(ref, title=name); s.smooth=False
    s.graphicalProperties=GraphicalProperties(ln=LineProperties(solidFill=color, w=22000))
    if marker:
        s.marker=Marker(symbol="circle", size=6,
                        spPr=GraphicalProperties(solidFill=color,
                                                 ln=LineProperties(solidFill=color)))
    else:
        s.marker=Marker(symbol="none")
    return s


def _charts(w, cal, ROWS, d3, start):
    """5 张趋势图 + 1 张杜邦对比图。全部引用单元格区域，模型重算后自动跟随。"""
    R=cal["_rows"]; NH,NY=R["NH"],R["NY"]; n=NH+NY
    PR=cal["periods"]; HL,YE=PR["labels"],PR["proj"]
    CATS=Reference(w, min_col=2, max_col=1+n, min_row=5, max_row=5)
    wi=w.parent["2_利润表"]; RF=cal["_ref"]
    ref=lambda sheet,row: Reference(sheet, min_col=2, max_col=1+n, min_row=row, max_row=row)
    w.cell(start-1,1,"图表引用单元格区域，模型重算后自动更新").font=Font(size=9,italic=True,color="808080")

    # ① 营收（柱，深红）+ 归母净利（线，藏蓝，次轴）——对应参考图「出货量持续增长」版式
    bar=BarChart(); bar.type="col"; bar.gapWidth=60
    bar.series.append(_bar_series(ref(wi,RF["RV"]), "营业总收入", C_RED))
    bar.set_categories(CATS)
    _skin(bar, "营业收入与归母净利润（亿元）", labels=True, label_fmt="#,##0")
    ln=LineChart()
    ln.series.append(_line_series(ref(wi,RF["RNIP"]), "归母净利润", C_NAVY))
    ln.y_axis.axId=200; ln.y_axis.crosses="max"
    ln.y_axis.majorGridlines=None; ln.y_axis.spPr=GraphicalProperties(ln=LineProperties(solidFill=C_GRID,w=6350))
    ln.y_axis.txPr=_axis_text()
    bar += ln
    w.add_chart(bar, f"A{start}")

    # ② 盈利能力（多线，不打标签——11 期 × 4 线会糊）
    ch=LineChart()
    for row,name,col in [(ROWS["ebitm"]-2,"毛利率",C_RED),(ROWS["ebitm"]-1,"营业利润率",C_KHAKI),
                         (ROWS["ebitm"],"EBIT利润率",C_CORAL),(ROWS["npm"],"归母净利率",C_NAVY)]:
        ch.series.append(_line_series(ref(w,row), name, col))
    ch.set_categories(CATS); _skin(ch, "盈利能力趋势", num_fmt="0%")
    w.add_chart(ch, f"K{start}")

    # ③ 价值创造：ROE / ROIC vs WACC（WACC 用灰色虚线基准）
    ch=LineChart()
    ch.series.append(_line_series(ref(w,ROWS["roe"]), "ROE（归母）", C_RED))
    ch.series.append(_line_series(ref(w,ROWS["roic"]), "ROIC（税后）", C_NAVY))
    s=_line_series(ref(w,ROWS["wacc"]), "WACC（基准线）", C_GREY, marker=False)
    s.graphicalProperties.ln.dashStyle="dash"
    ch.series.append(s)
    ch.set_categories(CATS); _skin(ch, "价值创造：ROIC 高于 WACC 才创造价值", num_fmt="0%")
    w.add_chart(ch, f"A{start+17}")

    # ④ 杜邦两个倍数项
    ch=LineChart()
    ch.series.append(_line_series(ref(w,d3+1), "总资产周转率", C_KHAKI))
    ch.series.append(_line_series(ref(w,d3+2), "权益乘数", C_NAVY))
    ch.set_categories(CATS); _skin(ch, "杜邦：总资产周转率与权益乘数", num_fmt="0.00", labels=True, label_fmt="0.00")
    w.add_chart(ch, f"K{start+17}")

    # ⑤ 营运资本周转天数
    ch=LineChart()
    for row,name,col in [(ROWS["dso"],"DSO 应收",C_RED),(ROWS["dio"],"DIO 存货",C_KHAKI),
                         (ROWS["dpo"],"DPO 应付",C_SLATE),(ROWS["ccc"],"现金转换周期",C_NAVY)]:
        ch.series.append(_line_series(ref(w,row), name, col))
    ch.set_categories(CATS); _skin(ch, "营运资本周转天数（天）", num_fmt="0")
    w.add_chart(ch, f"A{start+34}")

    # ⑥ 杜邦三因子：基期 vs 预测末期 —— 对应参考图「产业链各环节毛利率分配变化」版式
    base_col=CL(1+len(PR["annual"]))          # 最近年报列
    end_col=CL(1+n)                            # 末期预测列
    sr=start+34
    w.cell(sr,13,"杜邦三因子对比（图⑥数据源）").font=Font(bold=True,size=10,color=NAVY)
    w.cell(sr+1,13,"因子").font=F_COL; w.cell(sr+1,13).fill=P_B
    w.cell(sr+1,14,HL[len(PR["analysis"]) if False else len(PR["annual"])-1]).font=F_COL
    w.cell(sr+1,14).fill=P_B
    w.cell(sr+1,15,YE[-1]).font=F_COL; w.cell(sr+1,15).fill=P_B
    for k,(lab,rw) in enumerate([("归母净利率",d3),("总资产周转率",d3+1),("权益乘数",d3+2)]):
        w.cell(sr+2+k,13,lab).font=Font(size=10)
        w.cell(sr+2+k,14,f"={base_col}{rw}").number_format="0.00"
        w.cell(sr+2+k,15,f"={end_col}{rw}").number_format="0.00"
    ch=BarChart(); ch.type="col"; ch.gapWidth=80
    d1=Reference(w, min_col=14, max_col=14, min_row=sr+1, max_row=sr+4)
    d2=Reference(w, min_col=15, max_col=15, min_row=sr+1, max_row=sr+4)
    s1=Series(d1, title_from_data=True); s1.graphicalProperties=GraphicalProperties(solidFill=C_SLATE, ln=LineProperties(noFill=True))
    s2=Series(d2, title_from_data=True); s2.graphicalProperties=GraphicalProperties(solidFill=C_RED, ln=LineProperties(noFill=True))
    ch.series.append(s1); ch.series.append(s2)
    ch.set_categories(Reference(w, min_col=13, min_row=sr+2, max_row=sr+4))
    _skin(ch, "杜邦三因子：基期 vs 预测末期", labels=True, label_fmt="0.00")
    w.add_chart(ch, f"K{start+34}")
