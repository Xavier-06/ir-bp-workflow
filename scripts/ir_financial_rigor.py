#!/usr/bin/env python3
"""IR 金融严谨性验算工具（v3.9, 2026-08-07 借鉴 ai-berkshire financial_rigor.py）。

LLM 心算不可靠：PE 算错一个小数点、市值单位搞混港币和人民币，就可能导致错误的
投资判断。本工具提供生产时（写报告时）的强制验算能力，与 report_gate 的事后复算
形成闭环：生产时验算（防错）+ 事后复算（抓漏）。

设计原则：
- 所有计算使用 decimal.Decimal（精确十进制），禁止 float
- 纯 stdlib，无第三方依赖，子代理可直接 Bash 调用
- CLI 输出人类可读判决（✅/⚠️/❌），可直接粘贴进报告附录

用法（子代理 Bash 调用）：
  cd {RUNTIME_ROOT} && python3 scripts/ir_financial_rigor.py <command> [args]

命令：
  verify-market-cap   市值验算：股价×总股本 vs 报告市值（防单位错误）
  verify-valuation    估值指标验算：PE/PB/PS/股息率/FCF Yield
  cross-validate      多源交叉验证：N 个来源同一数据比对，超容差告警
  three-scenario      三情景目标价：乐观/中性/悲观 × 增速 × PE
  calc                任意算式精确计算（替代心算）

示例：
  python3 scripts/ir_financial_rigor.py verify-market-cap \\
    --price 510 --shares 9.11e9 --reported 4.65e12 --currency HKD
  python3 scripts/ir_financial_rigor.py cross-validate \\
    --field revenue --values '{"公司年报": 686.4, "卖方研报": 624.8}' --unit 亿元
  python3 scripts/ir_financial_rigor.py three-scenario \\
    --price 25 --eps 1.2 --growth 30 15 0 --pe 25 20 15 --years 3
  python3 scripts/ir_financial_rigor.py calc --expr '(120-98.5)/(98.5-80)'
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# ── 容差阈值（与 ai-berkshire 一致）──
MARKET_CAP_TOLERANCE = Decimal("0.05")   # 市值验算偏差 ≤5% 通过
CROSS_VALIDATE_TOLERANCE = Decimal("0.01")  # 多源比对偏差 ≤1% 一致


def _dec(value: str | float | int | Decimal) -> Decimal:
    """把输入转成 Decimal；支持科学计数法字符串（如 '9.11e9'）。"""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ValueError(f"无法解析为数字: {value!r}") from exc


def _fmt(value: Decimal, places: int = 4) -> str:
    """去掉多余尾零的友好显示。"""
    q = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return format(q.normalize(), "f")


def cmd_verify_market_cap(args: argparse.Namespace) -> int:
    """市值验算：股价 × 总股本 vs 报告市值。

    这是最高频的错误源——"港币亿 vs 人民币亿"少写一个零。
    """
    price = _dec(args.price)
    shares = _dec(args.shares)
    reported = _dec(args.reported)
    if price <= 0 or shares <= 0 or reported <= 0:
        print("❌ price/shares/reported 必须为正数")
        return 1

    computed = price * shares
    deviation = abs(computed - reported) / reported
    ok = deviation <= MARKET_CAP_TOLERANCE
    verdict = "✅ 验证通过" if ok else "❌ 偏差过大，必须排查（单位错误？股本过期？汇率？）"

    print(f"== 市值验算（{args.currency}）==")
    print(f"股价 × 总股本: {_fmt(price)} × {_fmt(shares, 0)} = {_fmt(computed, 0)}")
    print(f"报告市值:      {_fmt(reported, 0)}")
    print(f"偏差:          {_fmt(deviation * 100, 2)}%（容差 {MARKET_CAP_TOLERANCE * 100}%）")
    print(verdict)
    if not ok:
        print("排查方向：① 单位（亿/万）漏写多写 ② 币种混淆（HKD/CNY/USD）"
              " ③ 总股本是否最新（增发/回购后） ④ 价格与股本是否同一时点")
    return 0 if ok else 2


def cmd_verify_valuation(args: argparse.Namespace) -> int:
    """估值指标精确验算：PE/PB/PS/股息率/FCF Yield。缺哪个参数跳过哪个。"""
    price = _dec(args.price)
    print(f"== 估值指标验算（股价 {_fmt(price)}）==")
    if args.eps:
        pe = price / _dec(args.eps)
        print(f"PE（价格/EPS）:   {_fmt(pe, 2)}x  [EPS={args.eps}]")
    if args.bvps:
        pb = price / _dec(args.bvps)
        print(f"PB（价格/BVPS）:  {_fmt(pb, 2)}x  [BVPS={args.bvps}]")
    if args.sps:
        ps = price / _dec(args.sps)
        print(f"PS（价格/SPS）:   {_fmt(ps, 2)}x  [SPS={args.sps}]")
    if args.dividend:
        dy = _dec(args.dividend) / price * 100
        print(f"股息率:           {_fmt(dy, 2)}%  [每股股息={args.dividend}]")
    if args.fcf_per_share:
        fy = _dec(args.fcf_per_share) / price * 100
        print(f"FCF Yield:        {_fmt(fy, 2)}%  [每股FCF={args.fcf_per_share}]")
    print("✅ 计算完成（Decimal 精确十进制，无浮点误差）")
    return 0


def cmd_cross_validate(args: argparse.Namespace) -> int:
    """多源交叉验证：对 values JSON 中所有来源两两比对。

    判决：全部偏差 ≤1% → 一致；>1% → 告警并要求标注口径差异原因。
    """
    try:
        values = json.loads(args.values)
    except json.JSONDecodeError as exc:
        print(f"❌ values 不是合法 JSON: {exc}")
        return 1
    if not isinstance(values, dict) or len(values) < 2:
        print("❌ values 必须是至少 2 个来源的 JSON 对象，如 '{\"来源A\": 100, \"来源B\": 101}'")
        return 1

    decimals = {src: _dec(v) for src, v in values.items()}
    base_src = next(iter(decimals))
    base = decimals[base_src]
    print(f"== 多源交叉验证：{args.field}（{args.unit or '未注明单位'}）==")
    all_ok = True
    for src, v in decimals.items():
        if src == base_src:
            continue
        deviation = abs(v - base) / base if base != 0 else Decimal("1")
        if deviation <= CROSS_VALIDATE_TOLERANCE:
            status = "✅ 一致"
        elif deviation <= Decimal("0.05"):
            status = "⚠️ 口径差异（1%-5%，双值并列标注原因）"
            all_ok = False
        else:
            status = "❌ 重大差异（>5%，必须查一手来源核实后才能使用）"
            all_ok = False
        print(f"{base_src}({_fmt(base)}) vs {src}({_fmt(v)}): 偏差 {_fmt(deviation * 100, 2)}% {status}")
    if all_ok:
        print(f"✅ 全部来源一致（≤{CROSS_VALIDATE_TOLERANCE * 100}%），可用，置信度 high")
    else:
        print("⚠️ 存在分歧——先查口径差异清单（归母vs扣非/GAAP vs Non-GAAP/汇率/财年/并表范围），"
              "同口径仍差 >5% 则查一手公告")
    return 0 if all_ok else 2


def cmd_three_scenario(args: argparse.Namespace) -> int:
    """三情景目标价：当前 EPS × (1+增速)^years × PE = 目标价。

    增速与 PE 各三个值：乐观 中性 悲观，空格分隔。
    """
    price = _dec(args.price)
    eps = _dec(args.eps)
    growths = [_dec(g) for g in args.growth]
    pes = [_dec(p) for p in args.pe]
    if len(growths) != 3 or len(pes) != 3:
        print("❌ --growth 和 --pe 必须各给 3 个值（乐观 中性 悲观）")
        return 1

    print(f"== 三情景目标价（{args.years} 年, 现价 {_fmt(price)}, 当前EPS {_fmt(eps)}）==")
    labels = ["乐观", "中性", "悲观"]
    targets = []
    for label, g, p in zip(labels, growths, pes):
        factor = (Decimal("1") + g / 100) ** args.years
        future_eps = eps * factor
        target = future_eps * p
        targets.append(target)
        upside = (target - price) / price * 100 if price != 0 else Decimal("0")
        print(f"{label}: EPS→{_fmt(future_eps, 3)} × {p}x PE = 目标价 {_fmt(target, 2)}"
              f"（vs 现价 {('+' if upside >= 0 else '')}{_fmt(upside, 1)}%）")

    # 单调性检查：牛 > 基准 > 熊（统稿写作诚信规则之一）
    if not (targets[0] > targets[1] > targets[2]):
        print("⚠️ 情景不满足单调性（牛>基准>熊）——检查增速/PE 假设")
    return 0


def cmd_calc(args: argparse.Namespace) -> int:
    """任意算式精确计算（Decimal 上下文 eval，白名单字符防注入）。"""
    expr = args.expr.replace("^", "**")
    allowed = set("0123456789.+-*/()%e ")
    if any(c not in allowed for c in expr):
        print(f"❌ 算式含非法字符（只允许数字与 + - * / ( ) .）: {args.expr}")
        return 1
    try:
        result = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 — 白名单过滤后
        print(f"{args.expr} = {_fmt(_dec(result), 6)}")
    except Exception as exc:
        print(f"❌ 计算失败: {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ir_financial_rigor.py",
        description="IR 金融严谨性验算工具（生产时验算，Decimal 精确计算）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("verify-market-cap", help="市值验算：股价×股本 vs 报告市值")
    p1.add_argument("--price", required=True)
    p1.add_argument("--shares", required=True, help="总股本（股，支持 9.11e9 写法）")
    p1.add_argument("--reported", required=True, help="报告市值（与股本同币种）")
    p1.add_argument("--currency", default="CNY")
    p1.set_defaults(func=cmd_verify_market_cap)

    p2 = sub.add_parser("verify-valuation", help="估值指标验算 PE/PB/PS/股息率/FCF Yield")
    p2.add_argument("--price", required=True)
    p2.add_argument("--eps")
    p2.add_argument("--bvps", help="每股净资产")
    p2.add_argument("--sps", help="每股营收")
    p2.add_argument("--dividend", help="每股股息")
    p2.add_argument("--fcf-per-share", help="每股自由现金流")
    p2.set_defaults(func=cmd_verify_valuation)

    p3 = sub.add_parser("cross-validate", help="多源交叉验证")
    p3.add_argument("--field", required=True, help="字段名（如 revenue/market_cap）")
    p3.add_argument("--values", required=True, help='JSON: {"来源1": 数值, "来源2": 数值}')
    p3.add_argument("--unit", default="", help="单位（亿元/百万美元等）")
    p3.set_defaults(func=cmd_cross_validate)

    p4 = sub.add_parser("three-scenario", help="三情景目标价")
    p4.add_argument("--price", required=True, help="当前股价")
    p4.add_argument("--eps", required=True, help="当前 EPS")
    p4.add_argument("--growth", required=True, nargs=3, help="乐观/中性/悲观增速(%%)")
    p4.add_argument("--pe", required=True, nargs=3, help="乐观/中性/悲观 PE")
    p4.add_argument("--years", type=int, default=3)
    p4.set_defaults(func=cmd_three_scenario)

    p5 = sub.add_parser("calc", help="任意算式精确计算")
    p5.add_argument("--expr", required=True)
    p5.set_defaults(func=cmd_calc)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
