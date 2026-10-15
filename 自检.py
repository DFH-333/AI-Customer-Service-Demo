# -*- coding: utf-8 -*-
"""改完 知识库.md 之后跑一下这个，看看常见的问法能不能答上来。

    python 自检.py

它不联网检查、直接调 app.py 里的同一套逻辑（走的是大模型，有密钥的话），
所以这里通了，网页上就通。
"""

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app

# 想让客户问的问题 —— 换成你目标客户的真实问题
SHOULD_ANSWER = [
    "你们几点关门？", "几点开门", "有没有包间，能坐几个人", "人均多少钱",
    "停车方便吗", "外卖送不送", "能开发票吗", "小孩能吃吗",
    "能刷抖音团购券吗", "生日有优惠吗", "你们招人吗", "怎么走",
]
# 资料里没有的 —— 必须拒答，不能编
SHOULD_REFUSE = [
    "你们店里有篮球场吗", "老板在吗", "有没有预制菜", "明天股票会涨吗",
]

# 内容断言：命中了话题 ≠ 答对了话。
# 上面两组只查「找没找到」，这里查「挑出来的那句话对不对」。
# 用 extract_answer 而不是 handle_ask —— 后者在有密钥时会让大模型改写答案，
# 断言就会跟着飘。这里要钉死的是检索层，所以固定走本地选句那条路。
# (问题, 答案里必须出现, 答案里绝不能出现)
SHOULD_CONTAIN = [
    ("小孩能吃吗",   ["儿童"],    ["犬", "牵绳"]),   # 别把「宠物和小孩」的第一行当答案
    ("能带狗吗",     ["小型犬"],  ["儿童套餐"]),      # 狗 / 小型犬 也得能互相认出来
    ("几点开门",     ["11:00"],   []),
    ("人均多少钱",   ["65"],      []),
]


def main():
    if not os.path.exists(app.KB_PATH):
        print(f"找不到 {app.KB_PATH}")
        return 1

    shop, chunks = app.load_kb(app.KB_PATH)
    print(f"客户：{shop}")
    print(f"话题：{len(chunks)} 个\n")

    ok = fail = 0

    # ---- 第一层：找得到 / 拒得掉 ----
    for label, questions, want in (("该答上来的", SHOULD_ANSWER, True),
                                   ("该拒答的", SHOULD_REFUSE, False)):
        print(f"===== {label} =====")
        for q in questions:
            r = app.handle_ask(q, shop, chunks)
            refused = r["mode"] == "no-match"
            good = refused != want
            ok += good
            fail += not good
            src = "、".join(r["sources"]) if r["sources"] else "—"
            print(f"{'OK ' if good else '!! '}{q}")
            print(f"     {r['answer']}")
            print(f"     [{r['mode']} {r['ms']}ms] 依据={src}")
        print()

    # ---- 第二层：答的话对不对 ----
    print("===== 答的话对不对（检索层选句）=====")
    for q, must, must_not in SHOULD_CONTAIN:
        ans, _ = app.extract_answer(q, chunks)
        ans = ans or ""
        missing = [w for w in must if w not in ans]
        forbidden = [w for w in must_not if w in ans]
        good = not missing and not forbidden
        ok += good
        fail += not good
        print(f"{'OK ' if good else '!! '}{q}  →  {ans or '（答不上来）'}")
        if missing:
            print(f"     缺了：{'、'.join(missing)}")
        if forbidden:
            print(f"     混进了不该有的：{'、'.join(forbidden)}")
    print()

    print(f"通过 {ok} 条，不过关 {fail} 条")
    if fail:
        print("找错的通常是：这句问法缺少对应的同义词 —— "
              "到 app.py 的 SYNONYMS 里加一条就行。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
