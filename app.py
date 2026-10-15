# -*- coding: utf-8 -*-
"""
AI 客服 Demo —— 本地跑，只用 Python 标准库，不需要 pip install 任何东西。

    python app.py            然后浏览器打开 http://127.0.0.1:8765
    启动.bat                 双击也行（会自动开浏览器）

工作原理（两件事）：
    1. 检索：把 知识库.md 切成一个个话题，按问题去匹配最相关的几段。
    2. 生成：把匹配到的段落当"资料"喂给大模型，让它用客服的口吻回答。
       大模型调不通时自动降级为"纯检索"模式 —— 演示现场永远不会报错。

联网需要的两个环境变量（你这台机器上已经有了）：
    ANTHROPIC_BASE_URL   例如 https://api.deepseek.com/anthropic
    ANTHROPIC_AUTH_TOKEN 你的 API Key
没有这两个也能跑，只是走纯检索模式。
"""

import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KB_PATH = os.path.join(BASE_DIR, "知识库.md")
TOP_K = 3          # 送给大模型的资料段数
PORT_START = 8765

# 外观默认值。每个客户的目录里可以放一个 客户.json 覆盖它们：
#   {"icon": "🚗", "starters": ["报名多少钱？", "多久能拿证？"]}
DEFAULT_ICON = "🤖"
DEFAULT_STARTERS = ["你们几点关门？", "有包间吗？能坐几个人？", "人均多少钱？",
                    "停车方便吗？", "外卖送不送？", "能开发票吗？", "孩子在能吃辣吗？"]

# ---------------------------------------------------------------- .env 读取

def load_dotenv(path=None):
    """读同目录下的 .env，塞进 os.environ。没有这个文件就安静跳过。

    已经设好的环境变量优先，不会被 .env 覆盖 —— 免得一个填错的 .env
    把机器上本来配好的密钥顶掉。只用标准库，不引入 python-dotenv。
    """
    path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception as e:
        print(f"  [提醒] .env 读不了（{e}），忽略。", flush=True)
        return
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


load_dotenv()

# ---------------------------------------------------------------- 资料加载

def load_kb(path):
    """读知识库，切成 {客户名, [话题块]}。话题块 = ## 标题 + 正文。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    shop = "我们的店"
    chunks = []
    title = None
    body = []

    def flush():
        if title and "".join(body).strip():
            chunks.append({
                "title": title,
                "text": "\n".join(l for l in body if l.strip()),
            })

    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            shop = s[2:].strip()
        elif s.startswith("## "):
            flush()
            title, body = s[3:].strip(), []
        elif s.startswith(">"):          # 说明性注释，不算资料
            continue
        else:
            body.append(line)
    flush()
    return shop, chunks


def load_client_cfg(kb_path):
    """和知识库放一起的 客户.json（可选）：覆盖图标和快捷问题。"""
    p = os.path.join(os.path.dirname(os.path.abspath(kb_path)), "客户.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception as e:
        print(f"  [提醒] 客户.json 读不了（{e}），改用默认外观。", flush=True)
        return {}


def apply_client_cfg(kb_path):
    """读客户配置 → 返回 (图标, 快捷问题)，并把客户自定义的同义词并进 SYNONYMS。

    app.py 和 自检.py 都走这一个口子，免得两边的检索结果对不上。
    """
    cfg = load_client_cfg(kb_path)
    extra = cfg.get("synonyms")
    if isinstance(extra, dict):
        SYNONYMS.update({str(k): str(v) for k, v in extra.items()})
    return cfg.get("icon") or DEFAULT_ICON, cfg.get("starters") or DEFAULT_STARTERS


PUNCT = re.compile(r"""[，。！？、；：""''（）()《》【】,\.!?;:~～\-—…“”‘’\[\]{}<>/\\|+*#%^&@$\s]""")


def norm(s):
    return PUNCT.sub("", s)


def bigrams(s):
    s = norm(s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


# 顾客不会用资料里的词。检索前先把口语替换成资料里的说法。
SYNONYMS = {
    "wifi": "网络 上网", "无线": "网络 wifi", "停车": "车位 地下停车场 停车费",
    "洗手间": "厕所 卫生间", "几点": "营业时间 开门 打烊", "关门": "打烊 营业时间",
    "多少钱": "价格 人均 收费", "贵": "价格 人均", "外卖": "配送 美团 饿了么",
    "打包": "带走 打包盒", "订": "预订 订座 预约", "预约": "订座 预订",
    "包间": "包厢 大桌", "发票": "开票 电子发票", "孩子": "小孩 儿童 宝宝椅",
    "辣": "辣度 微辣 特辣 减辣", "团购": "优惠券 折扣 打折", "优惠": "折扣 活动 团购",
    "电话": "联系方式 微信 订座", "在哪": "地址 怎么走 位置", "怎么走": "地址 位置",
    "招聘": "招人 应聘 服务员", "招人": "招聘 应聘 服务员 包吃", "招工": "招聘 应聘 服务员",
    "应聘": "招聘 招人", "找工作": "招聘 应聘", "加盟": "合作 加盟费", "生日": "长寿面 生日宴",
    "过敏": "忌口 花生", "厕所": "洗手间 卫生间", "车位": "停车", "酒": "酒水",
    "消费": "人均 价格", "送不送": "外卖 配送", "有没有": "", "能不能": "",
    # 同一件事的不同说法，字面上一个二元组都不重叠 —— 不加就会答错或答不上来
    "小孩": "儿童 小朋友 宝宝椅", "小朋友": "儿童 小孩", "儿童": "小朋友 小孩",
    "宝宝": "宝宝椅 儿童", "狗": "小型犬 宠物 牵绳", "宠物": "小型犬 狗",
}


def expand_query(q):
    """口语 → 资料用词，用于检索（不影响给大模型看的原文）。"""
    extra = []
    for k, v in SYNONYMS.items():
        if k in q and v:
            extra.append(v)
    return q + " " + " ".join(extra) if extra else q


def retrieve(question, chunks, k=TOP_K):
    """返回最相关的资料块，带匹配度。

    confident 的判据是实测定出来的（见 自检）：
    命中 2 个以上二元组，或者命中了标题 —— 真问题几乎都满足，
    瞎问（"有篮球场吗"）命中的是正文里的无关词，会落在门槛外。
    """
    qb = bigrams(expand_query(question))
    if not qb:
        return []
    scored = []
    for c in chunks:
        kt = bigrams(c["title"] * 3 + c["text"])   # 标题出现三次 = 加权
        if not kt:
            continue
        hit = len(qb & kt)
        if hit == 0:
            continue
        t_hit = len(qb & bigrams(c["title"]))
        score = hit / (len(kt) ** 0.5) * (1 + 0.6 * t_hit)
        scored.append({"title": c["title"], "text": c["text"],
                       "score": score, "hit": hit, "t_hit": t_hit,
                       "confident": hit >= 2 or t_hit >= 1})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


def extract_answer(question, chunks):
    """降级模式：不调大模型，直接从资料里挑最相关的一句原话当回答。"""
    hits = [h for h in retrieve(question, chunks, k=2) if h["confident"]]
    if not hits:
        return None, []
    qb = bigrams(expand_query(question))
    best, second = None, None
    for c in hits:
        for line in c["text"].splitlines():
            line = line.strip().lstrip("-").strip()
            if not line:
                continue
            sc = len(qb & bigrams(line))
            if sc == 0:
                continue
            if best is None or sc > best[0]:
                best, second = (sc, line, c["title"]), best
            elif second is None or sc > second[0]:
                second = (sc, line, c["title"])
    if best is None:
        # 一行都没对上：宁可把这个话题整个端出去，也不要只取第一行 ——
        # 一个话题里常常说了好几件事（比如「宠物和小孩」），第一行未必是问的那件。
        c = hits[0]
        lines = [l.strip().lstrip("-").strip()
                 for l in c["text"].splitlines() if l.strip()]
        return " ".join(lines), [c["title"]]
    ans = best[1]
    if second and second[2] == best[2] and len(ans) + len(second[1]) < 90:
        ans = ans + " " + second[1]
    return ans, [best[2]]


# ---------------------------------------------------------------- 大模型

_GOOD_MODEL = None   # 记下哪一个模型名调通了，后面直接用


def _env_key():
    return (os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY"))


def llm_ready():
    return bool(os.environ.get("ANTHROPIC_BASE_URL", "").strip() and _env_key())


def build_system(shop):
    return (
        f'你是"{shop}"的在线客服，在微信上回答顾客的问题。\n'
        "要求：\n"
        "1. 只根据下面给你的【资料】回答，不要编造资料里没有的信息。\n"
        "2. 资料里没有答案的，直接说\"这个我这边不太确定，帮您转人工问一下哈\"，不要猜。\n"
        "3. 口语化、亲切、简短，1~3 句话，可以用\"哈、哦、～\"这类语气词。\n"
        "4. 绝对不要提\"资料\"\"文档\"\"根据提供的信息\"这类字眼。\n"
        "5. 价格、时间、电话这类具体信息必须准确，资料里怎么写的就怎么说。"
    )


def llm_answer(question, chunks, shop):
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    key = _env_key()
    if not base or not key:
        return None, "no-key"

    ctx = "\n\n".join(f"【{c['title']}】\n{c['text']}" for c in chunks) or "（无相关资料）"
    user_msg = f"【资料】\n{ctx}\n\n【顾客的问题】\n{question}"

    global _GOOD_MODEL
    if _GOOD_MODEL:
        models = [_GOOD_MODEL]
    else:
        models = []
        for m in (os.environ.get("DEMO_MODEL"),
                  os.environ.get("ANTHROPIC_MODEL"),
                  "deepseek-chat"):
            if m and m not in models:
                models.append(m)
        models = models[:2]

    last_err = "unknown"
    for model in models:
        payload = {
            "model": model,
            "max_tokens": 512,
            "system": build_system(shop),
            "messages": [{"role": "user", "content": user_msg}],
        }
        req = request.Request(
            base + "/v1/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": key,
                "authorization": "Bearer " + key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text").strip()
            if text:
                _GOOD_MODEL = model
                return text, "llm:" + model
            last_err = "empty"
        except error.HTTPError as e:
            try:
                last_err = f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}"
            except Exception:
                last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        print(f"  [大模型] {model} 失败：{last_err}", flush=True)
    return None, last_err


def find_phone(chunks):
    for c in chunks:
        m = re.search(r"0\d{2,3}[-\s]?\d{3,4}\s?\d{4}", c["text"])
        if m:
            return m.group(0)
    return ""


# ---------------------------------------------------------------- 网页界面

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__SHOP__ · 在线客服</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;margin:0}
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#dfe3e8;display:flex;
     justify-content:center;align-items:center;flex-direction:column}
.phone{width:420px;height:780px;max-height:97vh;background:#ededed;display:flex;
       flex-direction:column;border-radius:14px;overflow:hidden;box-shadow:0 14px 50px rgba(0,0,0,.22)}
.bar{background:#07c160;color:#fff;padding:13px 15px;display:flex;align-items:center;gap:10px;flex:0 0 auto}
.bar .ava{width:34px;height:34px;border-radius:6px;background:rgba(255,255,255,.28);
          display:grid;place-items:center;font-size:16px}
.bar .nm{font-size:15px;font-weight:600}
.bar .sb{font-size:11px;opacity:.88;margin-top:2px}
.chat{flex:1 1 auto;overflow-y:auto;padding:16px 12px 4px}
.msg{display:flex;margin-bottom:14px;gap:8px}
.msg.me{flex-direction:row-reverse}
.bub{max-width:77%;padding:9px 12px;border-radius:8px;font-size:14px;line-height:1.6;
     word-break:break-word;white-space:pre-wrap;position:relative}
.msg.ai .bub{background:#fff;color:#191919}
.msg.me .bub{background:#95ec69;color:#191919}
.msg.ai .bub::before,.msg.me .bub::before{content:"";position:absolute;top:11px;width:0;height:0;
     border:5px solid transparent}
.msg.ai .bub::before{left:-5px;border-right-color:#fff}
.msg.me .bub::before{right:-5px;border-left-color:#95ec69}
.ava2{width:32px;height:32px;border-radius:6px;flex:0 0 auto;display:grid;place-items:center;
      font-size:13px;color:#fff;font-weight:600}
.msg.ai .ava2{background:#07c160}
.msg.me .ava2{background:#6c8ebf}
.src{font-size:11px;color:#a0a0a0;margin-top:6px;padding-top:5px;border-top:1px dashed #e8e8e8}
.chips{flex:0 0 auto;padding:8px 12px 2px;display:flex;gap:6px;flex-wrap:wrap}
.chip{font-size:12px;color:#07c160;border:1px solid #b7e6c9;background:#fff;border-radius:14px;
      padding:6px 11px;cursor:pointer;user-select:none}
.chip:hover{background:#07c160;color:#fff;border-color:#07c160}
.inbar{flex:0 0 auto;display:flex;gap:8px;padding:10px 12px 14px}
input{flex:1;border:0;outline:0;border-radius:6px;padding:11px 12px;font-size:14px;font-family:inherit}
button{border:0;background:#07c160;color:#fff;border-radius:6px;padding:0 20px;font-size:14px;
       cursor:pointer;font-family:inherit}
button:disabled{opacity:.45;cursor:default}
.dots span{display:inline-block;width:5px;height:5px;margin-right:3px;background:#c3c3c3;border-radius:50%;
           animation:bl 1.2s infinite}
.dots span:nth-child(2){animation-delay:.2s}.dots span:nth-child(3){animation-delay:.4s}
@keyframes bl{0%,60%,100%{opacity:.3;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}
.tip{font-size:11px;color:#9aa4ae;margin-top:10px;text-align:center}
</style>
</head>
<body>
<div class="phone">
  <div class="bar">
    <div class="ava">__ICON__</div>
    <div><div class="nm">__SHOP__</div><div class="sb">在线 · 有问题随时问我</div></div>
  </div>
  <div class="chat" id="chat"></div>
  <div class="chips" id="chips"></div>
  <div class="inbar">
    <input id="q" placeholder="想问点什么…" autocomplete="off">
    <button id="send">发送</button>
  </div>
</div>
<div class="tip">AI 客服演示 · 回答自动生成，仅供内部演示</div>
<script>
const chat = document.getElementById('chat'), q = document.getElementById('q'),
      send = document.getElementById('send'), chips = document.getElementById('chips');
const SHOP = "__SHOP__";
const STARTERS = __STARTERS__;
const ICON = "__ICON__";

function bubble(role, text, src) {
  const m = document.createElement('div');
  m.className = 'msg ' + (role === 'me' ? 'me' : 'ai');
  const a = document.createElement('div');
  a.className = 'ava2'; a.textContent = role === 'me' ? '我' : ICON;
  const b = document.createElement('div');
  b.className = 'bub'; b.textContent = text;
  if (src) { const s = document.createElement('div'); s.className = 'src'; s.textContent = src; b.appendChild(s); }
  m.appendChild(a); m.appendChild(b); chat.appendChild(m);
  chat.scrollTop = chat.scrollHeight;
  return m;
}
function thinking() {
  const m = document.createElement('div');
  m.className = 'msg ai';
  m.innerHTML = '<div class="ava2">' + ICON + '</div><div class="bub"><span class="dots"><span></span><span></span><span></span></span></div>';
  chat.appendChild(m); chat.scrollTop = chat.scrollHeight; return m;
}
async function ask(text) {
  text = (text || '').trim(); if (!text) return;
  bubble('me', text);
  q.value = ''; send.disabled = true;
  const t = thinking();
  try {
    const r = await fetch('/api/ask', {method:'POST', headers:{'content-type':'application/json'},
                                       body: JSON.stringify({q: text})});
    const d = await r.json();
    t.remove();
    let s = '';
    if (d.sources && d.sources.length) s = '依据：' + d.sources.join(' · ');
    if (d.mode === 'local') s += (s ? ' · ' : '') + '本地匹配';
    if (s && d.ms) s += ' · ' + (d.ms / 1000).toFixed(1) + ' 秒';
    bubble('ai', d.answer, s);
  } catch (e) {
    t.remove(); bubble('ai', '网络有点问题，再问一次哈～');
  }
  send.disabled = false; q.focus();
}
send.onclick = () => ask(q.value);
q.addEventListener('keydown', e => { if (e.key === 'Enter') ask(q.value); });
STARTERS.forEach(s => {
  const c = document.createElement('div');
  c.className = 'chip'; c.textContent = s;
  c.onclick = () => ask(s);
  chips.appendChild(c);
});
bubble('ai', '您好，我是' + SHOP + '的客服，有什么想了解的都可以问我～');
q.focus();
</script>
</body>
</html>
"""


def page_html(shop, icon=DEFAULT_ICON, starters=None):
    return (PAGE
            .replace("__SHOP__", shop)
            .replace("__ICON__", icon)
            .replace("__STARTERS__",
                     json.dumps(starters or DEFAULT_STARTERS, ensure_ascii=False)))


# ---------------------------------------------------------------- HTTP 服务

def handle_ask(question, shop, chunks):
    t0 = time.time()
    hits = [h for h in retrieve(question, chunks) if h["confident"]]

    # 资料里确实没有这一条 —— 不去猜，也不浪费一次模型调用。
    if not hits:
        phone = find_phone(chunks)
        return {"answer": "这个我这边不太确定，帮您转人工问一下哈～"
                          + (f"也可以直接打 {phone} 问一下。" if phone else ""),
                "sources": [], "mode": "no-match", "ms": int((time.time() - t0) * 1000)}

    answer, mode = None, "local"
    if llm_ready():
        answer, mode = llm_answer(question, hits, shop)
    if answer is None:
        answer, titles = extract_answer(question, chunks)
        if answer is None:
            return {"answer": "这个我这边不太确定，帮您转人工问一下哈～",
                    "sources": [], "mode": "no-match",
                    "ms": int((time.time() - t0) * 1000)}
        sources = titles
    else:
        sources = [c["title"] for c in hits[:2]]
    return {
        "answer": answer,
        "sources": sources,
        "mode": mode,
        "ms": int((time.time() - t0) * 1000),
    }


def make_handler(shop, chunks, icon=DEFAULT_ICON, starters=None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            raw = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.startswith("/api/health"):
                self._send(200, json.dumps({"llm": llm_ready(), "model": _GOOD_MODEL}))
            elif self.path in ("/", "/index.html"):
                self._send(200, page_html(shop, icon, starters), "text/html; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self.path.startswith("/api/ask"):
                self._send(404, json.dumps({"answer": "not found"}, ensure_ascii=False))
                return
            n = int(self.headers.get("content-length") or 0)
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
                question = (data.get("q") or "").strip()
            except Exception:
                question = ""
            if not question:
                self._send(200, json.dumps({"answer": "你想问点什么呢？",
                                            "sources": [], "mode": "local", "ms": 0},
                                           ensure_ascii=False))
                return
            try:
                res = handle_ask(question, shop, chunks)
            except Exception as e:
                print(f"  [错误] {type(e).__name__}: {e}", flush=True)
                res = {"answer": "我这边卡了一下，再问一次哈～", "sources": [],
                       "mode": "error", "ms": 0}
            print(f"  Q: {question}\n  A: {res['answer']}   [{res['mode']} {res['ms']}ms]",
                  flush=True)
            self._send(200, json.dumps(res, ensure_ascii=False))

        def log_message(self, *a):
            pass

    return Handler


def main():
    kb_path = sys.argv[1] if len(sys.argv) > 1 else KB_PATH
    if not os.path.isabs(kb_path):
        kb_path = os.path.join(BASE_DIR, kb_path)
    if not os.path.exists(kb_path):
        print(f"找不到知识库文件：{kb_path}")
        input("按回车退出…")
        return

    icon, starters = apply_client_cfg(kb_path)

    shop, chunks = load_kb(kb_path)
    print(f"客户：{shop}")
    print(f"资料：{len(chunks)} 个话题" + (f"（{'、'.join(c['title'] for c in chunks[:5])}…）" if len(chunks) > 5 else ""))
    if llm_ready():
        print(f"大模型：已接通（{os.environ.get('ANTHROPIC_BASE_URL')}）")
    else:
        print("大模型：未配置 —— 会走纯检索模式（能演示，回答是资料原句）")

    port, httpd = PORT_START, None
    for p in range(PORT_START, PORT_START + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p),
                                        make_handler(shop, chunks, icon, starters))
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print("端口都被占用了，先关掉别的程序再试。")
        input("按回车退出…")
        return

    url = f"http://127.0.0.1:{port}"
    print(f"\n打开浏览器访问：{url}")
    print("演示时按 Ctrl+C 停止。\n")
    if os.environ.get("DEMO_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
