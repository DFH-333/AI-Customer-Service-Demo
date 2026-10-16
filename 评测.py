# -*- coding: utf-8 -*-
"""评测拒答闸门：召回率 / 拒答准确率 / 阈值扫描曲线。

    python 评测.py                          跑一遍，打印指标表和阈值扫描
    python 评测.py --svg 评测曲线.svg        顺便把曲线写成 SVG
    python 评测.py 客户-驾校                 评另一家客户（读它目录里的 评测集.json）

只走检索层，不调大模型 —— 结果可复现，也不需要密钥。

两个指标是分开的，因为它们失败的含义完全不同：
    召回率      该答的问题，答上来了多少      —— 低了 = 产品不好用
    拒答准确率  该拒的问题，拒掉了多少        —— 低了 = 产品有害（在胡说）
把两者平均成一个「准确率」就把这两件事抹平了，所以这里分开报。
"""

import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app  # noqa: E402

# CI 用的下限 = 当前实测值再往下留一点余量。它的作用是「别改坏了」，
# 不是「达标」—— 定一个现在够不着的数，CI 只会永远红着，那就等于没有 CI。
FLOOR_RECALL = 0.95
FLOOR_REFUSE = 0.60
FLOOR_REFUSE_UNRELATED = 0.90   # 「完全无关」那一类才是线上真会遇到的


# ------------------------------------------------------------------ 打分

def hit_titles(question, chunks):
    """这条问题命中了哪些话题（只算够格回答的）。"""
    return [h["title"] for h in app.retrieve(question, chunks) if h["confident"]]


def score(evalset, chunks):
    """在当前阈值下打分，返回一个指标字典。"""
    answered = evalset["该答"]
    refused = evalset["该拒"]

    got = 0          # 正确话题出现在命中里
    top1 = 0         # 正确话题排第一
    missed = []
    for case in answered:
        q, want = case["q"], case["话题"]
        titles = hit_titles(q, chunks)
        if want in titles:
            got += 1
            if titles[0] == want:
                top1 += 1
        else:
            missed.append((q, want, titles))

    ok = 0
    leaked = {"完全无关": [], "词面撞车": []}
    for case in refused:
        cls = case.get("类", "完全无关")
        q = case["q"]
        titles = hit_titles(q, chunks)
        if not titles:
            ok += 1
        else:
            leaked.setdefault(cls, []).append((q, titles))

    n_ans, n_ref = len(answered), len(refused)

    # 两类该拒的难度差着量级，必须分开报 —— 合起来平均会把真问题抹平。
    # 「完全无关」是门外汉随口一问，线上真会遇到的多是这种；
    # 「词面撞车」是专门造出来跟资料共用词的，考的是这道门的上限。
    by_class = {}
    for cls in ("完全无关", "词面撞车"):
        total = sum(1 for c in refused if c["类"] == cls)
        bad_n = len(leaked[cls])
        by_class[cls] = {
            "总数": total, "拒掉": total - bad_n,
            "准确率": (total - bad_n) / total if total else 0.0,
        }

    return {
        "召回率": got / n_ans if n_ans else 0.0,
        "top1命中率": top1 / n_ans if n_ans else 0.0,
        "拒答准确率": ok / n_ref if n_ref else 0.0,
        "该答总数": n_ans, "命中数": got,
        "该拒总数": n_ref, "拒掉数": ok,
        "分类": by_class,
        "漏答": missed, "漏拒": leaked,
    }


def at(evalset, chunks, hit_min, title_hit):
    """换一组阈值跑一次，跑完恢复原状。"""
    app.CONF_HIT_MIN, app.CONF_TITLE_HIT = hit_min, title_hit
    try:
        return score(evalset, chunks)
    finally:
        app.CONF_HIT_MIN = CONF_HIT_MIN_DEFAULT
        app.CONF_TITLE_HIT = CONF_TITLE_HIT_DEFAULT


# 扫描表上值得细看的三格 —— 不是随手挑的，是权衡曲线的拐点附近
CANDIDATES = [
    (2, True,  "命中≥2 + 标题"),
    (3, True,  "命中≥3 + 标题"),
    (2, False, "命中≥2，标题不算"),
]


def scan(evalset, chunks):
    """扫「命中下限 × 标题算不算数」这一组阈值。"""
    rows = []
    for title_hit in (True, False):
        for hit_min in (1, 2, 3, 4, 5):
            app.CONF_HIT_MIN = hit_min
            app.CONF_TITLE_HIT = title_hit
            r = score(evalset, chunks)
            rows.append({
                "命中下限": hit_min, "标题算命中": title_hit,
                "召回率": r["召回率"], "top1命中率": r["top1命中率"],
                "拒答准确率": r["拒答准确率"],
            })
    app.CONF_HIT_MIN = CONF_HIT_MIN_DEFAULT
    app.CONF_TITLE_HIT = CONF_TITLE_HIT_DEFAULT
    return rows


# ------------------------------------------------------------------ 打印

def pct(x):
    return f"{x * 100:5.1f}%"


def print_candidates(evalset, chunks):
    """光看数字选不了工作点 —— 得看掉的是哪几条题。"""
    print("=" * 64)
    print("候选工作点：拧紧一档，掉的是哪几条问法？")
    print("=" * 64)
    for hit_min, title_hit, name in CANDIDATES:
        r = at(evalset, chunks, hit_min, title_hit)
        lost = "、".join(q for q, _, _ in r["漏答"]) or "—"
        leak = "、".join(q for q, _ in r["漏拒"]["完全无关"] + r["漏拒"]["词面撞车"]) or "—"
        print(f"{name}")
        print(f"    召回 {pct(r['召回率'])}  拒答 {pct(r['拒答准确率'])}")
        print(f"    答不上来的：{lost}")
        print(f"    还是答错的：{leak}")
    print()
    print("  看「答不上来的」那一行：如果掉的是「有包间吗」这种短问句，")
    print("  那就是把最常见的问法推出门了 —— 光看百分比看不出来这件事。")
    print()


def print_report(cur, rows, ablate):
    print("=" * 64)
    print("拒答闸门评测")
    print("=" * 64)
    print(f"当前阈值：命中下限 {CONF_HIT_MIN_DEFAULT}，标题算命中 = {CONF_TITLE_HIT_DEFAULT}")
    print()
    print(f"  召回率      {pct(cur['召回率'])}   "
          f"({cur['命中数']}/{cur['该答总数']} 答上来了，且找对了话题)")
    print(f"  top1 命中率 {pct(cur['top1命中率'])}   (正确话题排在第一位)")
    print(f"  拒答准确率  {pct(cur['拒答准确率'])}   "
          f"({cur['拒掉数']}/{cur['该拒总数']} 拒掉了)")
    for cls in ("完全无关", "词面撞车"):
        d = cur["分类"][cls]
        print(f"      · {cls:<5} {pct(d['准确率'])}  ({d['拒掉']}/{d['总数']})")
    print()
    print("  这两行差得越多，越说明问题不在阈值、在词面：")
    print("  「儿童看护」和「儿童套餐」共用「儿童」两个字，检索层分不出哪个店里有。")
    print()

    for label, cases in (("完全无关", cur["漏拒"]["完全无关"]),
                         ("词面撞车", cur["漏拒"]["词面撞车"])):
        if cases:
            print(f"  漏拒 · {label}（{len(cases)} 条）：")
            for q, titles in cases:
                print(f"     ✗ {q}  →  被答成了「{'、'.join(titles)}」")
    if cur["漏答"]:
        print(f"  漏答（{len(cur['漏答'])} 条）：")
        for q, want, titles in cur["漏答"]:
            got = "、".join(titles) if titles else "（一句话都没找到）"
            print(f"     ✗ {q}  →  想要「{want}」，实际 {got}")
    print()

    print("=" * 64)
    print("阈值扫描：这道门拧紧一点会怎样")
    print("=" * 64)
    print(f"{'命中下限':<9}{'标题算命中':<11}{'召回率':<9}{'top1':<9}{'拒答准确率':<9}")
    print("-" * 64)
    for r in rows:
        mark = " ←当前" if (r["命中下限"] == CONF_HIT_MIN_DEFAULT
                            and r["标题算命中"] == CONF_TITLE_HIT_DEFAULT) else ""
        print(f"{r['命中下限']:<12}{('是' if r['标题算命中'] else '否'):<14}"
              f"{pct(r['召回率']):<11}{pct(r['top1命中率']):<11}"
              f"{pct(r['拒答准确率']):<9}{mark}")
    print()

    print("=" * 64)
    print("消融：召回率是靠算法，还是靠那张手写的同义词表？")
    print("=" * 64)
    print(f"  有同义词表  召回率 {pct(ablate['有'])}")
    print(f"  清空同义词表 召回率 {pct(ablate['无'])}")
    print(f"  ⇒ 差额 {pct(ablate['有'] - ablate['无'])} 是手写规则买的，不是算法泛化出来的")
    if ablate["靠同义词的"]:
        print(f"  这 {len(ablate['靠同义词的'])} 条问法只在有同义词表时才答得上来"
              f"（换一家客户就得重配）：")
        print("     " + "、".join(ablate["靠同义词的"]))
    print()


# ------------------------------------------------------------------ SVG 曲线

W, H = 720, 480
PAD_L, PAD_R, PAD_T, PAD_B = 78, 30, 56, 64


def _xy(recall, refuse):
    x = PAD_L + recall * (W - PAD_L - PAD_R)
    y = H - PAD_B - refuse * (H - PAD_T - PAD_B)
    return x, y


def write_svg(rows, path, cur):
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="sans-serif">']
    out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    out.append(f'<text x="{W/2}" y="28" text-anchor="middle" font-size="17" '
               f'font-weight="bold" fill="#1F3864">拒答闸门：召回率 vs 拒答准确率</text>')
    out.append(f'<text x="{W/2}" y="46" text-anchor="middle" font-size="12" '
               f'fill="#595959">右上方更好；每个点是一组阈值</text>')

    # 网格 + 轴刻度
    for i in range(0, 11):
        v = i / 10
        x, y = _xy(v, 0)
        out.append(f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{H-PAD_B}" '
                   f'stroke="#EDEDED" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{H-PAD_B+18}" text-anchor="middle" '
                   f'font-size="11" fill="#595959">{int(v*100)}%</text>')
        x2, y2 = _xy(0, v)
        out.append(f'<line x1="{PAD_L}" y1="{y2:.1f}" x2="{W-PAD_R}" y2="{y2:.1f}" '
                   f'stroke="#EDEDED" stroke-width="1"/>')
        out.append(f'<text x="{PAD_L-8}" y="{y2+4:.1f}" text-anchor="end" '
                   f'font-size="11" fill="#595959">{int(v*100)}%</text>')

    out.append(f'<line x1="{PAD_L}" y1="{H-PAD_B}" x2="{W-PAD_R}" y2="{H-PAD_B}" '
               f'stroke="#808080" stroke-width="1.5"/>')
    out.append(f'<line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{H-PAD_B}" '
               f'stroke="#808080" stroke-width="1.5"/>')
    out.append(f'<text x="{(PAD_L+W-PAD_R)/2:.0f}" y="{H-16}" text-anchor="middle" '
               f'font-size="13" fill="#1F3864">召回率（该答的答上来了多少）</text>')
    out.append(f'<text x="18" y="{(PAD_T+H-PAD_B)/2:.0f}" text-anchor="middle" '
               f'font-size="13" fill="#1F3864" '
               f'transform="rotate(-90 18 {(PAD_T+H-PAD_B)/2:.0f})">'
               f'拒答准确率（该拒的拒掉了多少）</text>')

    # 把同一系列的点连起来 —— 这些点不是散落的，是一条权衡曲线，
    # 连上线才看得出「想要拒得更准，就必须放弃多少召回」。
    for title_hit, color in ((True, "#2E75B6"), (False, "#7F8C8D")):
        pts = sorted((r for r in rows if r["标题算命中"] == title_hit),
                     key=lambda r: r["命中下限"])
        d = " ".join(f"{_xy(r['召回率'], r['拒答准确率'])[0]:.1f},"
                     f"{_xy(r['召回率'], r['拒答准确率'])[1]:.1f}" for r in pts)
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" '
                   f'stroke-width="1.6" stroke-opacity="0.55"/>')

    # 数据点
    for r in rows:
        x, y = _xy(r["召回率"], r["拒答准确率"])
        is_cur = (r["命中下限"] == CONF_HIT_MIN_DEFAULT
                  and r["标题算命中"] == CONF_TITLE_HIT_DEFAULT)
        color = "#C0392B" if is_cur else ("#2E75B6" if r["标题算命中"] else "#7F8C8D")
        rad = 7 if is_cur else 4.5
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad}" fill="{color}" '
                   f'fill-opacity="0.9" stroke="#ffffff" stroke-width="1.5"/>')
        label = f'h{ r["命中下限"] }' + ('' if r["标题算命中"] else ' 无题')
        out.append(f'<text x="{x:.1f}" y="{y-10:.1f}" text-anchor="middle" '
                   f'font-size="10" fill="{color}">{esc(label)}</text>')

    # 图例
    lg = [("当前阈值", "#C0392B"), ("标题算命中", "#2E75B6"), ("标题不算命中", "#7F8C8D")]
    for i, (t, c) in enumerate(lg):
        x = PAD_L + 8 + i * 150
        y = H - PAD_B - 22
        out.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{c}"/>')
        out.append(f'<text x="{x+12}" y="{y+4}" font-size="11" fill="#404040">{esc(t)}</text>')

    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


# ------------------------------------------------------------------ 主流程

CONF_HIT_MIN_DEFAULT = app.CONF_HIT_MIN
CONF_TITLE_HIT_DEFAULT = app.CONF_TITLE_HIT


def parse_args():
    """`评测.py [客户目录] [--svg 输出.svg]`，客户目录不带就评根目录这一家。"""
    argv = list(sys.argv[1:])
    svg = None
    if "--svg" in argv:
        i = argv.index("--svg")
        svg = argv[i + 1] if i + 1 < len(argv) else "评测曲线.svg"
        del argv[i:i + 2 if i + 1 < len(argv) else i + 1]
    client = argv[0] if argv else ""
    return client, svg


def main():
    client, svg_out = parse_args()
    base = os.path.join(HERE, client) if client else HERE
    evalset_path = os.path.join(base, "评测集.json")

    if not os.path.exists(evalset_path):
        print(f"找不到评测集：{evalset_path}")
        print("（每家客户在它自己的目录里放一份 评测集.json）")
        return 1
    with open(evalset_path, "r", encoding="utf-8") as f:
        evalset = json.load(f)

    kb = os.path.join(base, evalset["知识库"])
    shop, chunks = app.load_kb(kb)

    app.apply_client_cfg(kb)
    cur = score(evalset, chunks)

    # 消融：把同义词表清空再打一次分
    saved = dict(app.SYNONYMS)
    before = cur["召回率"]
    app.SYNONYMS.clear()
    no_syn = score(evalset, chunks)
    app.SYNONYMS.update(saved)
    ablate = {"有": before, "无": no_syn["召回率"],
              "靠同义词的": [q for q, _, _ in no_syn["漏答"]]}

    rows = scan(evalset, chunks)
    print_report(cur, rows, ablate)
    print_candidates(evalset, chunks)

    if svg_out:
        p = svg_out if os.path.isabs(svg_out) else os.path.join(base, svg_out)
        write_svg(rows, p, cur)
        print(f"曲线已写入：{p}")

    bad = []
    if cur["召回率"] < FLOOR_RECALL:
        bad.append(f"召回率 {pct(cur['召回率'])} < 下限 {pct(FLOOR_RECALL)}")
    if cur["拒答准确率"] < FLOOR_REFUSE:
        bad.append(f"拒答准确率 {pct(cur['拒答准确率'])} < 下限 {pct(FLOOR_REFUSE)}")
    unrel = cur["分类"]["完全无关"]["准确率"]
    if unrel < FLOOR_REFUSE_UNRELATED:
        bad.append(f"完全无关的拒答率 {pct(unrel)} < 下限 {pct(FLOOR_REFUSE_UNRELATED)}")
    if bad:
        print("不合格：" + "；".join(bad))
        return 1
    print(f"三项都在下限之上（召回 ≥{pct(FLOOR_RECALL).strip()}、"
          f"拒答 ≥{pct(FLOOR_REFUSE).strip()}、完全无关拒答 ≥{pct(FLOOR_REFUSE_UNRELATED).strip()}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
