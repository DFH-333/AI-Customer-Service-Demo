# -*- coding: utf-8 -*-
"""改完 知识库.md 之后跑一下这个，看看常见的问法能不能答上来。

    python 自检.py

它不联网检查、直接调 app.py 里的同一套逻辑（走的还是大模型），
所以这里通了，网页上就通。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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


def main():
    if not os.path.exists(app.KB_PATH):
        print(f"找不到 {app.KB_PATH}")
        return
    shop, chunks = app.load_kb(app.KB_PATH)
    print(f"客户：{shop}")
    print(f"话题：{len(chunks)} 个\n")

    ok = bad = 0
    for label, questions, want in (("该答上来的", SHOULD_ANSWER, True),
                                   ("该拒答的", SHOULD_REFUSE, False)):
        print(f"===== {label} =====")
        for q in questions:
            r = app.handle_ask(q, shop, chunks)
            refused = r["mode"] == "no-match"
            good = refused != want
            ok += good
            bad += not good
            src = "、".join(r["sources"]) if r["sources"] else "—"
            print(f"{'OK ' if good else '!! '}{q}")
            print(f"     {r['answer']}")
            print(f"     [{r['mode']} {r['ms']}ms] 依据={src}")
        print()

    print(f"通过 {ok} 条，不过关 {bad} 条")
    if bad:
        print("不过关的通常是：这句问法缺少对应的同义词 —— "
              "到 app.py 的 SYNONYMS 里加一条就行。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
