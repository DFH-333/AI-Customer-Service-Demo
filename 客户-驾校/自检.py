# -*- coding: utf-8 -*-
"""驾校版自检 —— 只测检索（不调大模型），几秒钟出结果。

    python 客户-驾校\自检.py

两类检查：
    应该答上来的   —— 这些问题命中不了资料，说明资料漏了说法，去加一条。
    应该拒答的     —— 这些瞎问的问题要是答上来了，说明门槛太松，很危险。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import app

KB = os.path.join(HERE, "知识库.md")
app.apply_client_cfg(KB)          # 先套上 客户.json 里的自定义同义词，再测
shop, chunks = app.load_kb(KB)

SHOULD_ANSWER = [
    "报名多少钱？", "多久能拿证？", "外地户口能报吗？", "周末能练车吗？",
    "训练场在哪？", "挂科了怎么办？", "能分期吗？", "学费里都包含什么？",
    "多大年纪能学？", "科目二考哪几项？", "怎么退学？", "教练能换吗？",
    "视力要求多少？", "有没有班车接送？",
]
SHOULD_REFUSE = [
    "你们驾校有食堂吗？", "能不能先学后付？", "你们招不招厨师？",
    "附近有没有篮球场？", "能不能帮我报个税？",
]


def hits(q):
    return [h["title"] for h in app.retrieve(q, chunks) if h["confident"]]


print(f"客户：{shop}")
print(f"话题：{len(chunks)} 个")
bad = 0

print("\n【应该答上来】")
for q in SHOULD_ANSWER:
    h = hits(q)
    if not h:
        bad += 1
    print(f"  {'OK' if h else '××'}  {q:<14} → {'、'.join(h[:2]) if h else '（答不上来）'}")

print("\n【应该拒答】")
for q in SHOULD_REFUSE:
    h = hits(q)
    if h:
        bad += 1
    print(f"  {'OK' if not h else '××'}  {q:<14} → {'、'.join(h[:2]) if h else '（正确拒答）'}")

print(f"\n{'== 全部通过 ==' if bad == 0 else f'== {bad} 条没过，看上面 ×× =='}")
