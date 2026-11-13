# -*- coding: utf-8 -*-
"""心事本 → 记忆星图（单文件 HTML，数据内联，零外部依赖，file:// 双击可开）。

规矩（照着他发来的那份规格 + 两个实物版本）：
1. 节点必须是真的：每条 = 心事本里真实存在的一条记录，不许编造、不许演示数据。
2. 每条边必须答得出「凭什么连」，每条边带一个 why：
   ① 内容引用  ② 共同关键词（交集 ≥2）  ③ 分类/项目归属  ④ 同源且时间相邻（2 小时内）
3. 三轨道：内圈稳定（事实）/ 中圈待办（约定·分寸）/ 外圈归档（念想）；中间那颗是总核。
4. 大小看权重、亮度看多久没被想起；金色恒星永不遗忘、不参与淘汰。
5. **展示 ≠ 注入**：这张图只负责看得见，进上下文的永远是 recall 挑出来的那几条。
6. 遗忘推演滑杆：往后拖，星星按各自半衰期暗下去，连线一起暗。
7. 自检：节点数 / 边数 / 孤立占比；孤立 >10% 就回去补边的语义。
8. 性能：devicePixelRatio 钳到 1，边按权重分桶。

用法：python tools/star_map.py [--out 心事本/星图.html] [--open]
"""
import argparse
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402
ROOT = os.path.dirname(os.path.abspath(__file__))
MIND = _P.MIND

STOP = set("""的 了 就 也 都 还 和 跟 与 及 或 但 而 是 在 有 没 不 很 太 又 再 才 已经 一下 一个 一条 这个 那个
我们 你们 他们 自己 什么 怎么 因为 所以 如果 但是 然后 之后 之前 现在 今天 明天 昨天 时候 一点
可以 应该 需要 觉得 知道 看到 听过 说完 说清 说话""".split())

# 三轨道：内圈稳定、中圈待办、外圈归档
ORBIT = {"事实": 235, "分寸": 285, "约定": 325, "念想": 400, "分类": 150, "日期": 330}


def load_jsonl(name):
    p = os.path.join(MIND, name)
    out = []
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            d["_src"] = name
            d["_line"] = i
            out.append(d)
    return out


def tokens(text):
    """中文切 2~4 字；英文/型号整词保留——以前把 IndexTTS2 切成 In/Ind/Inde，看着像乱码。"""
    text = text or ""
    out = set()
    # 先把“英文/型号/数字”整段抠出来（IndexTTS2、RVC、BM25、FTS5 这种）
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_+\-]{1,}", text):
        lw = w.lower()
        if len(lw) >= 2 and lw not in STOP:
            out.add(lw)
    # 再把中文按 2~4 字滑窗切开
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        for n in (2, 3, 4):
            for i in range(len(seg) - n + 1):
                w = seg[i:i + n]
                if w in STOP:
                    continue
                out.add(w)
    return out


def _grams(w, n=3):
    return {w[i:i+n] for i in range(max(0, len(w)-n+1))}


def pick_chips(toks, limit=8):
    """面板上的小标签：只留“最长、且彼此不重叠”的几个。
    以前会出现 不再 / 不再换 / 不再换回 / 再换回云 这种同一段的四五个变体。"""
    arr = sorted(toks, key=len, reverse=True)
    out = []
    for w in arr:
        gw = _grams(w)
        clash = False
        for o in out:
            if w in o or o in w or (gw & _grams(o)):
                clash = True
                break
        if clash:
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return sorted(out, key=lambda x: (-len(x), x))


def build():
    recs = []
    for name, tag in (("notes.jsonl", "事实"), ("thoughts.jsonl", "念想"),
                      ("promises.jsonl", "约定"), ("tacts.jsonl", "分寸")):
        for r in load_jsonl(name):
            txt = r.get("text") or r.get("content") or ""
            if not txt:
                continue
            at = r.get("at") or ""
            try:
                ts = time.mktime(time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                ts = 0
            recs.append({
                "id": "%s#%s" % (tag, r.get("id", r["_line"])),
                "kind": tag,
                "text": txt,
                "t": ts,
                "tstr": at[:16].replace("T", " "),
                "pinned": 1 if (r.get("pinned") or r.get("kind") == "rule") else 0,
                "importance": int(r.get("importance", 3 if tag == "约定" else 2) or 2),
                "src": name,
                "key": sorted(tokens(txt))[:14],
                "chips": pick_chips(tokens(txt)),
            })
    if not recs:
        return [], [], []

    # 关键词集合（供边用）
    for r in recs:
        r["kw"] = set(r["key"])

    edges = []

    # 日期星：把“哪一天”也做成一颗星，能顺着它回看那天想过什么
    days = {}
    for r in recs:
        d = (r["tstr"] or "")[:10]
        if len(d) == 10:
            days.setdefault(d, []).append(r)
    for d, arr in sorted(days.items())[-45:]:
        did = "day:" + d
        recs.append({
            "id": did, "kind": "日期", "text": d, "t": max(x["t"] for x in arr),
            "tstr": d, "pinned": 0, "importance": 1, "src": "day", "key": [],
            "kw": set(), "day": True, "count": len(arr),
        })
        for r in arr:
            edges.append({"s": did, "t": r["id"], "w": 0.55,
                          "why": "④同一天：这条是 %s 记下的（当天共 %d 条）" % (d, len(arr))})

    # ① 内容引用：A 正文出现 B 的关键词（≥3 字的词才算，避免噪音）
    for a in recs:
        for b in recs:
            if a is b:
                continue
            hit = [w for w in b["kw"] if len(w) >= 3 and w in a["text"]]
            if len(hit) >= 2:
                edges.append({"s": a["id"], "t": b["id"], "w": 1,
                              "why": "①内容引用：这条里出现了「%s」" % "、".join(hit[:2])})
    # ② 共同关键词：交集 ≥2
    seen = set()
    for i, a in enumerate(recs):
        for b in recs[i + 1:]:
            inter = [w for w in a["kw"] if w in b["kw"] and len(w) >= 2]
            if len(inter) >= 2:
                k = tuple(sorted((a["id"], b["id"])))
                if k in seen:
                    continue
                seen.add(k)
                edges.append({"s": a["id"], "t": b["id"], "w": 2,
                              "why": "②共同关键词：%s" % "、".join(inter[:3])})
    # ④ 同源且 2 小时内
    for i, a in enumerate(recs):
        for b in recs[i + 1:]:
            if a["src"] != b["src"] or not a["t"] or not b["t"]:
                continue
            gap = abs(a["t"] - b["t"])
            # 只有挨得很近（20 分钟内）才算“同一段里记的”；以前放到 2 小时，等于把
            # 同一天的东西全连成一片，理由面板就被这种边刷满了
            if 0 < gap <= 1200:
                edges.append({"s": a["id"], "t": b["id"], "w": 0.35,
                              "why": "④同源相邻：相隔 %d 分钟" % max(1, int(gap // 60))})

    # ③ 分类归属 + 核心
    hubs = {}
    for r in recs:
        hubs.setdefault(r["kind"], {"id": "hub:" + r["kind"], "kind": "分类",
                                    "text": "「%s」" % r["kind"], "t": 0, "tstr": "",
                                    "pinned": 0, "importance": 1, "src": "hub",
                                    "key": [], "kw": set()})
    for r in recs:
        edges.append({"s": hubs[r["kind"]]["id"], "t": r["id"], "w": 0.3,
                      "why": "③分类归属：这条属于「%s」" % r["kind"]})
    core = {"id": "core", "kind": "核心", "text": "总核：所有记忆加起来的那一颗",
            "t": 0, "tstr": "", "pinned": 1, "importance": 5, "src": "core",
            "key": [], "kw": set()}
    for h in hubs.values():
        edges.append({"s": "core", "t": h["id"], "w": 0.8,
                      "why": "③核心：这一类记忆都挂在总核上"})

    # ⑤ 语义相近：读 embed.py 建好的向量（同一份数据、同一个 id），余弦高的连一条
    vpath = os.path.join(MIND, "vectors.json")
    if os.path.exists(vpath):
        try:
            vd = json.load(open(vpath, encoding="utf-8"))
            vec = {}
            for it in vd.get("items") or []:
                if it.get("id") and it.get("v"):
                    vec[it["id"]] = it["v"]
            ids = [r["id"] for r in recs if r["id"] in vec]
            cand = []
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    va, vb = vec[a], vec[b]
                    c = sum(x * y for x, y in zip(va, vb))
                    if c >= 0.62:
                        cand.append((c, a, b))
            cand.sort(reverse=True)
            seen = {}
            for c, a, b in cand:              # 每个节点最多 3 条语义边，别织成一张网
                if seen.get(a, 0) >= 3 or seen.get(b, 0) >= 3:
                    continue
                seen[a] = seen.get(a, 0) + 1
                seen[b] = seen.get(b, 0) + 1
                edges.append({"s": a, "t": b, "w": 0.5,
                              "why": "⑤语义相近：cos=%.2f（说的几乎是同一件事的不同侧面）" % c})
        except Exception:
            pass

    # 去重，留 why 最长的
    best = {}
    for e in edges:
        k = tuple(sorted((e["s"], e["t"])))
        if k not in best or len(e["why"]) > len(best[k]["why"]):
            best[k] = e
    edges = list(best.values())

    recs_all = recs + list(hubs.values()) + [core]
    deg = {}
    for e in edges:
        deg[e["s"]] = deg.get(e["s"], 0) + 1
        deg[e["t"]] = deg.get(e["t"], 0) + 1
    iso = [r["id"] for r in recs if deg.get(r["id"], 0) == 0]
    for r in recs_all:
        r["deg"] = deg.get(r["id"], 0)
    return recs_all, edges, iso


HTML = """<!doctype html>
<meta charset="utf-8">
<title>心事本 · 记忆星图</title>
<style>
 html,body{margin:0;height:100%;background:#05080f;color:#cfe3ff;
   font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif;overflow:hidden}
 canvas{display:block;width:100vw;height:100vh}
body.vert canvas{width:auto;height:100vh}
 body.clean #bar, body.clean #hud, body.clean #panel{opacity:0;pointer-events:none;transition:opacity .3s}
body.embed #bar, body.embed #hud, body.embed #panel{display:none!important}
body.embed{overflow:hidden}
#hud{position:fixed;left:16px;bottom:14px;font-size:12px;color:#7c93b5;user-select:none}
 #hud b{color:#9fd0ff}
 #keys{margin-top:4px;font-size:11px;color:#6d86a4}
 #keys b{color:#9fd0ff;font-weight:600}
 #bar{position:fixed;left:16px;top:14px;background:rgba(8,16,28,.86);border:1px solid #16293f;
   border-radius:10px;padding:10px 12px;font-size:12px;color:#8fb3d9;user-select:none}
 #bar input[type=range]{width:190px;vertical-align:middle}
 #bar button{background:#12263c;border:1px solid #23415f;color:#9fd0ff;border-radius:6px;
   padding:2px 8px;margin-right:4px;font-size:12px;cursor:pointer}
 #bar button.on{background:#1d4467;border-color:#3f7fb5;color:#e8f4ff}
 #legend{margin-top:6px;color:#6f88a8}
 #panel{position:fixed;right:16px;top:16px;width:340px;max-height:82vh;overflow:auto;
   background:rgba(8,16,28,.94);border:1px solid #1d3450;border-radius:10px;padding:12px 14px;
   display:none;font-size:13px}
 #panel button{margin:6px 0 2px;background:#12243a;color:#bcd6f5;border:1px solid #24405f;border-radius:6px;padding:3px 9px;font-size:11px;cursor:pointer}
#panel button:hover{background:#1a3552}
#panel .bodytxt{line-height:1.55}
#panel h4{margin:0 0 6px;color:#9fd0ff;font-size:14px}
 #panel .why{color:#8fb3d9;font-size:12px;margin:3px 0 3px 10px}
 #panel .kw span{display:inline-block;background:#12263c;border-radius:4px;padding:1px 6px;
   margin:2px 4px 0 0;font-size:12px;color:#8fc7ff}
 #panel .dim{color:#6f88a8;font-size:12px}
</style>
<canvas id="c"></canvas>
<div id="bar">
  <div>遗忘推演：往后拖 <b id="daylab">0</b> 天
    <input id="days" type="range" min="0" max="365" value="0" step="1"></div>
  <div style="margin-top:6px">
    视野：<button id="b0" class="on">全部</button><button id="b1">一度关联</button><button id="b2">二度关联</button><button id="bT">文字：开</button><button id="bL" class="on">线条：开</button>
  </div>
  <div id="legend">内圈稳定 · 中圈待办 · 外圈归档 · 金色＝恒星（永不遗忘）</div>
  <div id="keys"><b>V</b> 竖屏 9:16　<b>T</b> 文字开关　<b>H</b> 隐藏界面　<b>滚轮</b> 缩放　<b>中键</b> 复位　<b>点星</b> 只看它那条星座</div>
</div>
<div id="hud"></div>
<div id="panel"></div>
<script>
const DATA = __DATA__;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const DPR = 1;           // 性能红线：高清会拖卡整机
let W = 0, H = 0;
let VERT = /[?&]v=1/.test(location.search);
let HIDEUI0 = false;
function resize(){
  if(VERT){
    // 竖屏：9:16，居中，两边留黑——录视频用
    const availH = innerHeight, availW = innerWidth;
    H = availH; W = Math.min(availW, Math.round(availH*9/16));
    cv.style.position='fixed'; cv.style.left=((availW-W)/2)+'px'; cv.style.top='0px';
    cv.style.width = W+'px'; cv.style.height = H+'px';
    document.body.classList.add('vert');
    cv.width = W*DPR; cv.height = H*DPR;
    document.body.style.background='#000';
  } else {
    W = innerWidth; H = innerHeight;
    cv.style.position=''; cv.style.left=''; cv.style.top='';
    cv.style.width=''; cv.style.height='';
    document.body.classList.remove('vert');
    cv.width = W*DPR; cv.height = H*DPR;
  }
  ctx.setTransform(DPR,0,0,DPR,0,0);
  // 竖屏时 HUD 跟着画布走，别飘到黑边里
  const off = VERT ? Math.round((innerWidth - W)/2) : 0;
  for(const id of ['bar','hud','panel']){
    const el = document.getElementById(id);
    if(!el) continue;
    el.style.left = VERT ? (off + 12) + 'px' : '';
    if(id==='panel') el.style.right = VERT ? (off + 12) + 'px' : '';
    if(id==='hud') el.style.left = VERT ? (off + 14) + 'px' : '';
  }
  document.body.classList.toggle('clean', HIDEUI);
}
let HIDEUI = false;
if (/[?&]embed=1/.test(location.search)) document.body.classList.add('embed');
// 被 DSH 面板全屏时：给顶上那排控件让出位置，并让 ESC 交回父窗口处理
(function(){
  const m = location.search.match(/[?&]hudtop=(\d+)/);
  if (m) {
    const top = Number(m[1]) + 'px';
    const bar = document.getElementById('bar');
    if (bar) bar.style.top = top;
    const hud = document.getElementById('hud');
    if (hud) hud.style.left = '14px';
  }
  if (/[?&]esc=1/.test(location.search)) {
    addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        try { parent.postMessage({ type: 'yumind', action: 'close' }, '*') } catch (err) {}
      }
    });
  }
})();
let drawLines = true;
document.getElementById('bL').onclick = ()=>{
  drawLines = !drawLines;
  const b = document.getElementById('bL');
  b.classList.toggle('on', drawLines);
  b.textContent = '线条：' + (drawLines ? '开' : '关');
};
// 给 DSH 面板用：父窗口 postMessage 直接操作这些开关（不用重载 iframe）
addEventListener('message', (ev) => {
  const d = ev.data || {};
  if (d.type !== 'yumind') return;
  try {
    if (typeof d.sim === 'number') {
      const el = document.getElementById('days');
      if (el) { el.value = String(d.sim); el.dispatchEvent(new Event('input')); }
    }
    if (typeof d.hop === 'number') {
      const el = document.getElementById('b' + d.hop);
      if (el) el.click();
    }
    if (typeof d.text === 'boolean' && SHOWTEXT !== d.text) document.getElementById('bT').click();
    if (typeof d.lines === 'boolean' && drawLines !== d.lines) document.getElementById('bL').click();
    if (typeof d.hideui === 'boolean' && HIDEUI !== d.hideui) { HIDEUI = d.hideui; document.body.classList.toggle('clean', HIDEUI); }
    if (typeof d.vert === 'boolean' && VERT !== d.vert) {
      VERT = d.vert;
      const u = new URL(location.href);
      if (VERT) u.searchParams.set('v','1'); else u.searchParams.delete('v');
      history.replaceState(null,'',u);
      resize();
    }
  } catch (e) { console.warn('yumind bridge', e) }
});
addEventListener('keydown', e=>{
  if(e.key==='h' || e.key==='H'){ HIDEUI = !HIDEUI; resize(); }
});
addEventListener('keydown', e=>{
  if(e.key==='v' || e.key==='V'){
    VERT = !VERT;
    const u = new URL(location.href);
    if(VERT) u.searchParams.set('v','1'); else u.searchParams.delete('v');
    history.replaceState(null,'',u);
    resize(); buildGalaxy();
  }
});
addEventListener('resize', resize); resize();

const byId = {};
DATA.nodes.forEach((n,i)=>{ n.i=i; byId[n.id]=n; });
const COLORS = {'事实':'#5aa9ff','念想':'#c9a4ff','约定':'#ffc14d','分寸':'#5fe0b0',
                '分类':'#e8f4ff','核心':'#ffd36b','日期':'#7fe3ff'};
const GA = 2.399963229728653;
// 每条轨道一个高度：内圈低、外圈高，转起来才有层次
const KIND_Y = {'事实':-95,'分寸':-25,'约定':40,'念想':120,'分类':20,'核心':0,'日期':-235};
DATA.nodes.forEach((n,i)=>{
  const r0 = n.orbit;
  const cnt = n.slotN || 1, j = n.slot || 0;
  const th = GA*0.10 + (j/cnt)*6.2832 + (n.kind==='念想'?0.9:0) + (n.kind==='约定'?2.1:0);
  if(n.kind==='核心'){                       // 总核钉在正中央，先把该有的字段补齐再返回
    n.bx=0; n.by=0; n.bz=0; n.x=0; n.y=0; n.z=0;
    n.color = COLORS['核心']; n.w = 99; n.rad = 7.5; n.core = true;
    return;
  }
  n.bx = Math.cos(th)*r0;
  n.bz = Math.sin(th)*r0;
  n.by = ((KIND_Y[n.kind]||0) + Math.sin(th*2)*r0*0.10) * 0.72;
  n.x=n.bx; n.y=n.by; n.z=n.bz;
  n.color = COLORS[n.kind] || '#8fb3d9';
  const w = (n.pinned ? 3 : 1) + n.imp*0.6 + Math.min(4, n.deg)*0.35;
  n.w = w; n.rad = 2.6 + Math.min(7, Math.sqrt(w)*3.2);
  n.core = (n.kind==='核心');
});
const E = DATA.edges.map(e=>({a:byId[e.s], b:byId[e.t], why:e.why, w:e.w||1})).filter(e=>e.a&&e.b);
const NEI = {};
E.forEach(e=>{ (NEI[e.a.id]=NEI[e.a.id]||[]).push(e.b.id); (NEI[e.b.id]=NEI[e.b.id]||[]).push(e.a.id); });

// ── 星芒精灵：一颗星画成「亮核 + 十字光刺 + 柔晕」，一次性烘焙，之后 blit
const SPR = {};
function hx(hex, a){
  const n = parseInt(hex.slice(1), 16);
  return 'rgba('+((n>>16)&255)+','+((n>>8)&255)+','+(n&255)+','+a+')';
}
function spriteOf(color, streak){
  const key = color + (streak ? '|s' : '');
  if(SPR[key]) return SPR[key];
  const S = 192, c = document.createElement('canvas');
  c.width = c.height = S;
  const g = c.getContext('2d'), m = S/2;

  // 1) 外晕：三层不同半径的柔光叠出来的“辉”，不是硬边圆
  const halo = g.createRadialGradient(m,m,0,m,m,m*0.92);
  halo.addColorStop(0.00, 'rgba(255,255,255,0.95)');
  halo.addColorStop(0.05, hx(color, 0.95));
  halo.addColorStop(0.16, hx(color, 0.42));
  halo.addColorStop(0.38, hx(color, 0.085));
  halo.addColorStop(0.62, hx(color, 0.035));
  halo.addColorStop(1.00, hx(color, 0));
  g.fillStyle = halo; g.beginPath(); g.arc(m,m,m*0.92,0,6.2832); g.fill();

  // 2) 一层很淡的“色差环”：外圈偏冷、内圈偏暖，像真的镜头吃进去的光
  g.globalCompositeOperation = 'lighter';
  const ring = g.createRadialGradient(m,m,m*0.26, m,m,m*0.46);
  ring.addColorStop(0.00, 'rgba(120,170,255,0)');
  ring.addColorStop(0.55, 'rgba(130,180,255,0.035)');
  ring.addColorStop(0.78, hx(color, 0.055));
  ring.addColorStop(1.00, 'rgba(255,190,140,0.02)');
  g.fillStyle = ring; g.beginPath(); g.arc(m,m,m*0.46,0,6.2832); g.fill();

  // 3) 极小的白热核：整颗星最亮的地方只有几个像素，这样才像“点”
  const coreg = g.createRadialGradient(m,m,0,m,m,m*0.10);
  coreg.addColorStop(0,'rgba(255,255,255,1)');
  coreg.addColorStop(0.55,'rgba(255,255,255,0.85)');
  coreg.addColorStop(1,'rgba(255,255,255,0)');
  g.fillStyle = coreg; g.beginPath(); g.arc(m,m,m*0.10,0,6.2832); g.fill();

  // 4) 只有核心/恒星才给一道极淡的横向光晕（变形镜那种，不是十字）
  if(streak){
    const lg = g.createLinearGradient(0,m,m*2,m);
    lg.addColorStop(0, hx(color,0));
    lg.addColorStop(0.5, hx(color,0.30));
    lg.addColorStop(1, hx(color,0));
    g.fillStyle = lg;
    g.beginPath(); g.ellipse(m, m, m*0.94, m*0.035, 0, 0, 6.2832); g.fill();
  }

  g.globalCompositeOperation = 'source-over';
  SPR[key] = c; return c;
}

let simDays = 0, focus = null, hop = 0;
let HEAT = {};                       // 焦点周围的“扩散热度”：近的亮、远的淡（HippoRAG 的 PPR 近似）
function computeHeat(){
  HEAT = {};
  if(!focus) return;
  HEAT[focus] = 1;
  const one = {};
  for(const e of E){
    if(e.a === focus) one[e.b] = Math.max(one[e.b]||0, 0.55*(e.w||0.5)*2);
    if(e.b === focus) one[e.a] = Math.max(one[e.a]||0, 0.55*(e.w||0.5)*2);
  }
  for(const k in one) HEAT[k] = Math.max(HEAT[k]||0, Math.min(1, one[k]));
  for(const m in one){
    for(const e of E){
      let o = null;
      if(e.a === m) o = e.b; else if(e.b === m) o = e.a;
      if(!o || o === focus) continue;
      const v = 0.30 * (e.w||0.5) * 2 * one[m];
      HEAT[o] = Math.max(HEAT[o]||0, Math.min(1, v));
    }
  }
}
document.getElementById('days').oninput = e => {
  simDays = +e.target.value; document.getElementById('daylab').textContent = simDays;
};
function setMode(k){ hop = k; focus = focus;
  ['b0','b1','b2'].forEach((id,i)=>document.getElementById(id).classList.toggle('on', i===k)); }
document.getElementById('b0').onclick=()=>setMode(0);
document.getElementById('b1').onclick=()=>setMode(1);
document.getElementById('b2').onclick=()=>setMode(2);
document.getElementById('bT').onclick=()=>{
  SHOWTEXT = !SHOWTEXT;
  document.getElementById('bT').textContent = '文字：' + (SHOWTEXT ? '开' : '关');
};
addEventListener('keydown', e=>{ if(e.key==='t'||e.key==='T'){ document.getElementById('bT').click(); } });

// 亮度 = 鲜活度：pinned 恒为 1；其余 2^(-(age+sim)/H)
function bright(n){
  if(n.pinned || n.core || n.kind==='分类') return 1;
  return Math.max(0.04, Math.pow(2, -(n.age + simDays) / n.H));
}
function visible(n){
  if(hop===0 || !focus) return true;
  if(n.id===focus) return true;
  if(hop>=1 && (NEI[focus]||[]).includes(n.id)) return true;
  if(hop>=2){
    for(const m of (NEI[focus]||[])) if((NEI[m]||[]).includes(n.id)) return true;
  }
  return false;
}

let rx=-0.30, ry=0.3, drag=false, lx=0, ly=0, mx=-9e9, my=-9e9;
cv.addEventListener('mousedown', e=>{
  if(e.button === 1){ e.preventDefault(); zoom = 1; rx = -0.30; ry = 0.3; drag = false; }
});
cv.addEventListener('auxclick', e=>{ if(e.button === 1) e.preventDefault(); });
cv.addEventListener('mousedown',e=>{drag=true;lx=e.clientX;ly=e.clientY;});
addEventListener('mouseup',()=>{drag=false;});
addEventListener('mousemove',e=>{
  if(drag){ ry += (e.clientX-lx)*0.006; rx += (e.clientY-ly)*0.006;
    rx = Math.max(-1.2, Math.min(1.2, rx)); lx=e.clientX; ly=e.clientY; }
  mx=e.clientX; my=e.clientY;
});
cv.addEventListener('click',()=>{ const n=pick(); focus = (n && n.id!==focus) ? n.id : null;
  if(focus && hop===0) setMode(1); if(!focus) setMode(0); computeHeat(); showPanel(byId[focus]||null); });

let zoom = 1, SHOWTEXT = true;
const ZOOM = () => Math.min(W, H) / (VERT ? 330 : 470) * zoom;
addEventListener('wheel', e=>{
  if(e.target.tagName !== 'CANVAS') return;       // 在面板上滚就让它滚面板
  e.preventDefault();
  zoom = Math.max(0.4, Math.min(1.9, zoom * (e.deltaY>0 ? 0.92 : 1.08)));
}, {passive:false});
function rot(p){
  const cy=Math.cos(ry), sy=Math.sin(ry), cx=Math.cos(rx), sx=Math.sin(rx);
  let x = p.x*cy - p.z*sy, z = p.x*sy + p.z*cy;
  let y = p.y*cx - z*sx; z = p.y*sx + z*cx;
  const k = ZOOM();
  return {x:x*k, y:y*k, z:z*k};
}
function pos(n){
  const b = bright(n);
  const drift = (1-b)*90;                       // 淡忘的星会往外飘
  const k = (n.orbit>0 ? (n.orbit+drift)/n.orbit : 1);
  return rot({x:n.bx*k, y:n.by*k, z:n.bz*k});
}

let hover=null;
function pick(){
  let best=null, bd=1e9;
  for(const n of DATA.nodes){
    if(!visible(n)) continue;
    const p = pos(n); const s = 520/(520 + p.z + 400);
    const sx = W/2 + p.x*s, sy = H/2 + p.y*s;
    const d = (sx-mx)**2 + (sy-my)**2;
    if(d < (n.rad*s+13)**2 && d < bd){ bd=d; best=n; }
  }
  return best;
}

const panel = document.getElementById('panel');
let curId = null, expandedId = null, expMinorFor = null;
function esc(t){ return String(t).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function brief(t, max){
  const clean = String(t).replace(/\s+/g,' ').trim();
  const parts = clean.split(/(?<=[。！？；;])/);
  let out = parts.slice(0,2).join('');
  if(out.length > max) out = out.slice(0, max);
  if(out.length < clean.length) out += '…';
  return out;
}
function showPanel(n){
  if(!n){ panel.style.display='none'; curId = null; return; }
  const same = (curId === n.id) && (panel.dataset.exp === String(expandedId));
  panel.style.display='block';
  if(same) return;                       // 同一个节点别每 90ms 重画，按钮才不会闪
  curId = n.id; panel.dataset.exp = String(expandedId);

  const rel = E.filter(e=>e.a===n||e.b===n);
  const major = rel.filter(e=>!e.why.startsWith('④'));
  const minor = rel.filter(e=>e.why.startsWith('④'));
  let ls = major.map(e=>{
    const other = e.a===n ? e.b : e.a;
    return `<div class="why">→ ${esc(other.text.slice(0,16))}…　${esc(e.why)}</div>`;
  }).join('');
  if(minor.length){
    const mins = minor.map(e=>parseInt((e.why.match(/([0-9]+) 分钟/)||[0,0])[1],10)||0);
    const span = Math.min(...mins) + '–' + Math.max(...mins);
    if(expMinorFor === n.id){
      ls += `<div class="why dim">—— 下面是 ${minor.length} 条“只是时间相邻”的边 ——</div>`;
      ls += minor.map(e=>{
        const other = e.a===n ? e.b : e.a;
        return `<div class="why dim">→ ${esc(other.text.slice(0,18))}…　${esc(e.why)}</div>`;
      }).join('');
      ls += `<button id="minbtn">收起这 ${minor.length} 条</button>`;
    } else {
      ls += `<button id="minbtn">＋ ${minor.length} 条只是时间相邻（${span} 分钟之内记下的）· 展开</button>`;
    }
  }
  const st = n.pinned ? '恒星 · 永不遗忘' : ('鲜活 ' + Math.round(bright(n)*100) + '%');
  const full = n.text || '';
  const shortened = !n.core && !n.kind.includes('分类') && full.length > 60;
  const shown = (shortened && expandedId !== n.id) ? brief(full, 56) : full;

  panel.innerHTML = `<h4>${esc(n.kind)}　${esc(n.tstr||'')}　${st}</h4>
    <div class="bodytxt">${esc(shown)}</div>
    ${shortened ? `<button id="expbtn">${expandedId===n.id ? '收起' : ('展开原文（' + full.length + ' 字）')}</button>` : ''}
    <div class="kw">${(n.chips||n.key||[]).map(k=>`<span>${esc(k)}</span>`).join('')}</div>
    <div class="dim" style="margin-top:8px">连了 ${rel.length} 条，其中 ${major.length} 条有内容上的理由</div>${ls}`;

  const btn = document.getElementById('expbtn');
  if(btn) btn.onclick = (ev)=>{ ev.stopPropagation();
    expandedId = (expandedId===n.id) ? null : n.id;
    panel.dataset.exp=''; curId=null; showPanel(n);
  };
  const mb = document.getElementById('minbtn');
  if(mb) mb.onclick = (ev)=>{ ev.stopPropagation();
    expMinorFor = (expMinorFor===n.id) ? null : n.id;
    panel.dataset.exp=''; curId=null; showPanel(n);
  };
}

// ── 银河背景：预渲染一次，之后只做整盘旋转（省 CPU；比每帧画几千粒子便宜）
const BG = document.createElement('canvas');
let BGS = 0;
const SPARKS = [];
const FG = [];              // 前景飞星：比背景转得快，制造位移视差
for(let i=0;i<46;i++){
  FG.push({a: Math.random()*6.2832, r: Math.min(innerWidth,innerHeight)*(0.20+Math.random()*0.75),
           s: Math.random()*1.6+0.5});
}
function buildGalaxy(){
  BGS = Math.ceil(VERT ? Math.max(W*2.6, H*1.15) : Math.max(W,H)*1.55);
  BG.width = BGS; BG.height = BGS;
  const g = BG.getContext('2d');
  const cx = BGS/2, cy = BGS/2;
  const base = g.createRadialGradient(cx,cy,0,cx,cy,BGS*0.62);
  base.addColorStop(0,'#0a1230'); base.addColorStop(0.42,'#060a1a'); base.addColorStop(1,'#02040a');
  g.fillStyle = base; g.fillRect(0,0,BGS,BGS);

  // ── 星云：用柏林噪声的分形叠加（fBm）算云量，再做一次域扭曲。
  //    低分辨率算好再放大——比逐像素画大图便宜两个数量级，糊一点反而更像气体。
  (function(){
    const NW = 260, NH = 208;
    const nc = document.createElement('canvas'); nc.width = NW; nc.height = NH;
    const ng = nc.getContext('2d');
    const img = ng.createImageData(NW, NH);
    const d = img.data;

    function h2(x,y){ const n = Math.sin(x*127.1 + y*311.7) * 43758.5453; return n - Math.floor(n); }
    function vn(x,y){
      const xi = Math.floor(x), yi = Math.floor(y), xf = x-xi, yf = y-yi;
      const u = xf*xf*(3-2*xf), v = yf*yf*(3-2*yf);
      const a = h2(xi,yi), b = h2(xi+1,yi), c = h2(xi,yi+1), e = h2(xi+1,yi+1);
      return a*(1-u)*(1-v) + b*u*(1-v) + c*(1-u)*v + e*u*v;
    }
    // 分形噪声两个关键参数（视频里就点这两个名）：
    //   lacunarity=2 —— 每层网格密一倍；persistence=0.5 —— 每层次要减半
    // 层数固定后要把总和归一，否则对比度会跟层数跑（它说这个只能手工试）
    const LAC = 2.0, PERS = 0.5, OCT = 5;
    const FBMMAX = (1 - Math.pow(PERS, OCT)) / (1 - PERS) * 0.5;
    function fbm(x,y){
      let s = 0, amp = 0.5, f = 1;
      for(let i=0;i<OCT;i++){ s += amp*vn(x*f, y*f); f *= LAC; amp *= PERS; }
      return s / FBMMAX;          // 归一到 0..1，对比度不随层数漂
    }
    for(let py=0; py<NH; py++){
      for(let px=0; px<NW; px++){
        const nx = px/NW - 0.5, ny = py/NH - 0.5;
        const rr = Math.hypot(nx, ny*1.1) * 2;
        // 域扭曲：先拿噪声去推坐标，云的形状就不会一格一格的
        const wx = fbm(nx*3.1 + 11.3, ny*3.1 + 7.7);
        const wy = fbm(nx*3.1 + 41.1, ny*3.1 + 3.3);
        const q  = fbm(nx*4.2 + wx*2.2, ny*4.2 + wy*2.2);
        let dens = Math.max(0, q - 0.46) / 0.54;          // 只保留浓的部分
        dens = Math.pow(dens, 1.55);
        const edge = Math.max(0, 1 - Math.pow(rr/0.78, 2.0));   // 往盘外淡出
        dens *= edge * edge;
        const i = (py*NW + px)*4;
        // 云的颜色在紫、蓝、青之间按另一层噪声过渡
        const t = fbm(nx*1.7 + 5.5, ny*1.7 + 2.2);
        const cr = 96 + 120*t, cg = 84 + 70*t, cb = 210 - 40*t;
        d[i]   = cr; d[i+1] = cg; d[i+2] = cb;
        d[i+3] = Math.min(255, dens*255*0.62);
      }
    }
    ng.putImageData(img, 0, 0);
    g.save();
    g.globalCompositeOperation = 'lighter';
    g.imageSmoothingEnabled = true;
    g.globalAlpha = 1.0;
    g.drawImage(nc, cx - BGS*0.52, cy - BGS*0.52, BGS*1.04, BGS*1.04);
    g.restore();
  })();

  // 三条对数螺旋臂：r = r0·e^(bθ)，越往外颜色越冷、越淡
  g.globalCompositeOperation = 'lighter';
  const ARMS = 3, N = 5200, SPIN = 2.05, R0 = BGS*0.040, RMAX = BGS*0.30;
  // 星色按比例抽：多数是白/蓝白，少量黄橙红——跟恒星分类的分布一个意思，不是全白
  const TYPES = [[0.42,'255,248,232'],[0.26,'188,214,255'],[0.16,'255,214,150'],
                 [0.10,'255,170,120'],[0.06,'255,132,110']];
  function pickType(){
    let u = Math.random();
    for(const [p,c] of TYPES){ if(u < p) return c; u -= p; }
    return TYPES[0][1];
  }
  for(let i=0;i<N;i++){
    const r = R0 + Math.pow(Math.random(), 0.55)*RMAX;
    const arm = i % ARMS;
    const th0 = (arm/ARMS)*6.2832 + Math.log(r/R0)*SPIN;
    const th = th0 + (Math.random()-0.5)*0.46*(0.45 + r/RMAX);
    const rr = r*(1 + (Math.random()-0.5)*0.11);
    const x = cx + Math.cos(th)*rr;
    const y = cy + Math.sin(th)*rr*0.60;          // 压扁成盘（galaxy thickness）
    const t = Math.min(1, r/RMAX);
    // 大小按幂律：小的多、大的少（真实星团就长这样）
    const size = 0.35 + Math.pow(Math.random(), 3.2)*2.6;
    const alpha = (0.05 + 0.30*(1-t)) * (0.55 + Math.pow(Math.random(),2)*0.9);
    const col = Math.random() < (0.35 + 0.45*t) ? pickType() : (Math.round(255-150*t)+','+Math.round(206-96*t)+','+Math.round(126+116*t));
    g.fillStyle = 'rgba('+col+','+(alpha*0.55).toFixed(3)+')';
    g.beginPath(); g.arc(x, y, size, 0, 6.2832); g.fill();
  }

  // 中央核球
  const core = g.createRadialGradient(cx,cy,0,cx,cy,BGS*0.095);
  core.addColorStop(0,'rgba(255,244,214,.78)');
  core.addColorStop(0.22,'rgba(255,206,140,.34)');
  core.addColorStop(0.52,'rgba(240,150,90,.12)');
  core.addColorStop(1,'rgba(255,140,70,0)');
  g.fillStyle = core; g.beginPath(); g.arc(cx,cy,BGS*0.14,0,6.2832); g.fill();
  g.globalCompositeOperation = 'source-over';

  // 尘埃暗带：烘进背景，随星系一起转、一起缩（不再单独跟屏幕走）
  (function(){
    g.save();
    g.filter = 'blur(6px)';
    for(const [rot0, wdt, al] of [[0.42,0.0075,0.16],[-0.48,0.0055,0.12]]){
      g.save();
      g.translate(W/2, H/2); g.rotate(rot0);
      const gr = g.createLinearGradient(-BGS*0.42,0,BGS*0.42,0);
      gr.addColorStop(0,'rgba(6,9,18,0)');
      gr.addColorStop(0.45,'rgba(6,9,18,'+al+')');
      gr.addColorStop(0.62,'rgba(6,9,18,'+al+')');
      gr.addColorStop(1,'rgba(6,9,18,0)');
      g.strokeStyle = gr; g.lineWidth = BGS*wdt; g.lineCap='round';
      g.beginPath(); g.moveTo(-BGS*0.40,0);
      g.quadraticCurveTo(0, BGS*0.045, BGS*0.40, 0);
      g.stroke();
      g.restore();
    }
    g.restore();
  })();

  // 远处的其它星系：几个极淡的椭圆，有旋臂的、有侧看的，撑出尺度感
  (function(){
    const FAR0 = [
      [0.12,0.18,0.090,0.55,-0.40,'170,190,255'],
      [0.88,0.14,0.075,1.90, 0.90,'190,175,255'],
      [0.06,0.88,0.100,2.40,-0.60,'255,205,170'],
      [0.94,0.86,0.085,0.90, 0.70,'180,220,255'],
      [0.50,0.06,0.055,0.30, 0.90,'255,235,210'],
      [0.28,0.92,0.070,-0.80, 0.60,'205,190,255'],
      [0.96,0.50,0.060,1.20, 0.80,'170,210,255'],
      [0.04,0.50,0.065,2.80, 0.70,'196,180,255']
    ];
    for(const [fx,fy,fr,rot,flat, col] of FAR0){
      const x = BGS*fx, y = BGS*fy, rr = BGS*fr;
      g.save(); g.translate(x,y); g.rotate(rot);
      const rg = g.createRadialGradient(0,0,0, 0,0,rr);
      rg.addColorStop(0, 'rgba('+col+',.55)');
      rg.addColorStop(0.35,'rgba('+col+',.16)');
      rg.addColorStop(1,   'rgba('+col+',0)');
      g.fillStyle = rg; g.scale(1, flat); g.beginPath(); g.arc(0,0,rr,0,6.2832); g.fill();
      g.restore();
    }
  })();

  // 散落的远景星（撑景深，不在旋臂上）
  for(let i=0;i<8200;i++){          // 远景极暗的一层：只做“密度”，不抢记忆星
    const a = Math.random()*6.2832, r = Math.pow(Math.random(),0.55)*BGS*0.62;
    const x = W/2 + Math.cos(a)*r, y = H/2 + Math.sin(a)*r*0.42;
    g.fillStyle = 'rgba(186,206,240,'+(0.010+Math.random()*0.055).toFixed(3)+')';
    g.beginPath(); g.arc(x,y,Math.random()*0.55,0,6.2832); g.fill();
  }
  for(let i=0;i<2600;i++){          // 中景：亮度幂律，少数几个明显
    const a = Math.random()*6.2832, r = Math.pow(Math.random(),0.62)*BGS*0.46;
    const x = W/2 + Math.cos(a)*r, y = H/2 + Math.sin(a)*r*0.40;
    const lum = Math.pow(Math.random(), 2.6);
    const warm = Math.random() < 0.12;
    g.fillStyle = warm
      ? 'rgba(255,'+(196+Math.random()*40|0)+','+(150+Math.random()*50|0)+','+(0.05+lum*0.45).toFixed(3)+')'
      : 'rgba(214,230,255,'+(0.05+lum*0.55).toFixed(3)+')';
    g.beginPath(); g.arc(x,y,0.55+lum*1.5,0,6.2832); g.fill();
  }
  for(let i=0;i<0;i++){
    const a = Math.random()*6.2832, rr = Math.random()*BGS*0.72;
    const x = cx+Math.cos(a)*rr, y = cy+Math.sin(a)*rr*0.85;
    g.fillStyle = 'rgba(206,226,255,'+(0.02+Math.random()*0.17).toFixed(3)+')';
    g.beginPath(); g.arc(x,y,Math.random()*0.70,0,6.2832); g.fill();
  }

  // 会闪的几颗
  SPARKS.length = 0;
  for(let i=0;i<110;i++){
    SPARKS.push({a: Math.random()*6.2832, r: Math.random()*BGS*0.46,
                 s: Math.random()*1.3+0.35, p: Math.random()*6.2832});
  }
}
buildGalaxy();
addEventListener('resize', ()=>{ buildGalaxy(); });

let t=0;
function step(){ t++; if(!drag) ry += 0.0009; draw(); requestAnimationFrame(step); }

function draw(){
  ctx.fillStyle='#02040a'; ctx.fillRect(0,0,W,H);
  // 银河整盘慢转（背景只 blit，不重画）
  ctx.save();
  ctx.translate(W/2, H/2);
  ctx.rotate(ry*0.55);
  // 背景的缩放不低于“刚好盖住屏幕”，否则缩小时会看到位图边界
  const cover = Math.max(W/BGS, H/BGS) * 1.02;
  const bz = Math.max(zoom, cover);
  ctx.scale(bz, bz);
  ctx.drawImage(BG, -BGS/2, -BGS/2);
  ctx.restore();
  // 暗角：把注意力收到中间那片星系上
  const vg = ctx.createRadialGradient(W/2,H/2,Math.min(W,H)*0.28, W/2,H/2,Math.max(W,H)*0.78);
  vg.addColorStop(0,'rgba(0,0,0,0)'); vg.addColorStop(1,'rgba(0,0,0,0.55)');
  ctx.fillStyle = vg; ctx.fillRect(0,0,W,H);
  // 前景飞星：近处的转得更快更亮，制造视差
  ctx.globalCompositeOperation = 'lighter';
  for(const s of FG){
    const a = s.a + ry*1.76;   // 前景转得更快 = 视差
    const x = W/2 + Math.cos(a)*s.r, y = H/2 + Math.sin(a)*s.r*0.72;
    if(x < -40 || x > W+40 || y < -40 || y > H+40) continue;
    const g2 = ctx.createRadialGradient(x,y,0,x,y,s.s*3);
    g2.addColorStop(0,'rgba(235,246,255,.55)');
    g2.addColorStop(1,'rgba(180,215,255,0)');
    ctx.fillStyle = g2; ctx.beginPath(); ctx.arc(x,y,s.s*3,0,6.2832); ctx.fill();
  }
  ctx.globalCompositeOperation = 'source-over';

  // 几颗会闪的
  ctx.fillStyle='#eaf4ff';
  const rot = ry*0.55;
  for(const s of SPARKS){
    const a = s.a + rot;
    const x = W/2 + Math.cos(a)*s.r, y = H/2 + Math.sin(a)*s.r*0.60;
    const tw = 0.25 + 0.75*Math.abs(Math.sin(t*0.018 + s.p));
    ctx.globalAlpha = tw*0.85;
    ctx.beginPath(); ctx.arc(x,y,s.s,0,6.2832); ctx.fill();
  }
  ctx.globalAlpha = 1;

  const hov = hover;

  // 边：按权重分桶；亮度跟着两端一起衰减
  const buckets = {};
  for(const e of E){
    const lit = (visible(e.a) && visible(e.b)) ? 1 : 0;
    const key = (Math.round((e.w||1)*2)/2).toFixed(1) + '|' + lit;
    (buckets[key]=buckets[key]||[]).push(e);
  }
  for(const key of Object.keys(buckets)){
    const arr=buckets[key];
    const [kv, lit] = key.split('|');
    if(lit === '0' && hop !== 0 && focus) { /* 焦点外：只留极淡的底 */ }
    const aBase = (lit === '1' ? (0.022+0.05*Number(kv)) : 0.006);
    ctx.lineWidth = 0.45 + 0.30*Number(kv);
    // 边：受“线条”开关控制
    if(!drawLines) continue;
    ctx.beginPath(); ctx.strokeStyle='rgba(122,168,232,'+aBase+')';
    for(const e of arr){
      const p=pos(e.a), q=pos(e.b);
      const x1=W/2+p.x, y1=H/2+p.y, x2=W/2+q.x, y2=H/2+q.y;
      // 往中心收一点的弧线，比直尺画出来的顺眼
      const mx=(x1+x2)/2, my=(y1+y2)/2;
      const cx0=W/2+(mx-W/2)*0.855, cy0=H/2+(my-H/2)*0.855;
      ctx.moveTo(x1,y1); ctx.quadraticCurveTo(cx0,cy0,x2,y2);
    }
    ctx.stroke();
  }
  if(hov){
    ctx.beginPath(); ctx.strokeStyle='rgba(170,225,255,.55)'; ctx.lineWidth=1.1;
    for(const e of E){
      if(e.a!==hov && e.b!==hov) continue;
      const p=pos(e.a), q=pos(e.b);
      const x1=W/2+p.x, y1=H/2+p.y, x2=W/2+q.x, y2=H/2+q.y;
      const mx=(x1+x2)/2, my=(y1+y2)/2;
      ctx.moveTo(x1,y1); ctx.quadraticCurveTo(W/2+(mx-W/2)*0.855, H/2+(my-H/2)*0.855, x2,y2);
    }
    ctx.stroke();
  }

  const order = DATA.nodes.slice().sort((a,b)=>pos(a).z-pos(b).z);
  // （尘埃暗带删掉了：放大时看着像两条黑胶布，他问“这个黑色的条是啥”——宁可少一层，不要假）

  // （尘埃已烘进背景位图，见 buildGalaxy）

  // 盘光辉：沿三条旋臂撒柔光，让盘面自己发光（星点之外的第二层结构）
  (function(){
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for(let arm=0; arm<3; arm++){
      for(let t=0.06; t<0.92; t+=0.03){
        const rr = BGS*0.30*t;
        const th = (arm/3)*6.2832 + Math.log(rr/(BGS*0.05)+1)*2.05 + ry*0.25;
        const x = W/2 + Math.cos(th)*rr;
        const y = H/2 + Math.sin(th)*rr*0.42;
        const rad = BGS*(0.010 + 0.016*t);
        const al = (1-t)*0.020 + 0.004;
        const g2 = ctx.createRadialGradient(x,y,0,x,y,rad);
        const warm = 1-t;
        g2.addColorStop(0, 'rgba('+(230+25*warm|0)+','+(210+20*warm|0)+',255,'+al.toFixed(3)+')');
        g2.addColorStop(1, 'rgba(120,150,255,0)');
        ctx.fillStyle = g2;
        ctx.beginPath(); ctx.arc(x,y,rad,0,6.2832); ctx.fill();
      }
    }
    ctx.restore();
  })();

  // 中央核球：一团暖色光压在正中，任何角度看它都在中心
  (function(){
    const rr0 = Math.min(W,H)*0.10, rr1 = rr0*2.4;
    const g2 = ctx.createRadialGradient(W/2,H/2,0,W/2,H/2,rr1);
    g2.addColorStop(0,'rgba(255,246,222,'+(0.20/Math.pow(zoom,0.55)).toFixed(3)+')');
    g2.addColorStop(0.22,'rgba(255,206,150,'+(0.10/Math.pow(zoom,0.55)).toFixed(3)+')');
    g2.addColorStop(0.55,'rgba(255,170,110,.07)');
    g2.addColorStop(1,'rgba(255,150,90,0)');
    ctx.fillStyle = g2; ctx.beginPath(); ctx.arc(W/2,H/2,rr1,0,6.2832); ctx.fill();
  })();

  // 总核的横向光晕：变形镜头那种一道很淡的横光，比画圆圈高级
  const cn = byId[__COREID__];
  if(cn){
    const cp = pos(cn), cx2 = W/2 + cp.x*0.9, cy2 = H/2 + cp.y*0.9;
    const br = 0.92 + 0.08*Math.sin(t*0.0012);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.translate(cx2, cy2); ctx.scale(6.2*br, 1);
    const sg = ctx.createRadialGradient(0,0,0,0,0,150);
    sg.addColorStop(0,'rgba(255,232,198,.20)');
    sg.addColorStop(0.45,'rgba(255,206,150,.06)');
    sg.addColorStop(1,'rgba(255,190,120,0)');
    ctx.fillStyle = sg;
    ctx.beginPath(); ctx.arc(0,0,150,0,6.2832); ctx.fill();
    ctx.restore();
  }

  for(const n of order){
    const p = pos(n);
    const s = 520/(520 + p.z + 400);
    const x = W/2 + p.x*s, y = H/2 + p.y*s;
    const b = bright(n);
    const r = Math.max(1.2, n.rad*s*(0.45+0.75*Math.sqrt(b)));
    const inFocus = visible(n);
    const heat = HEAT[n.id] || 0;
    const alpha = (0.25 + 0.75*b) * (0.45 + 0.55*s)
                * (inFocus ? (0.55 + 0.75*heat) : 0.10);
    const isHub = (n.pinned || n.core);
    const tw = 0.9 + 0.1*Math.sin(t*0.03 + n.i);          // 轻微呼吸/闪烁
    const size = Math.max(isHub?34:22, r * (isHub ? 12 : 8.8) * tw * (0.55 + 0.55*Math.sqrt(b))) * (1 + 0.35*heat);
    ctx.globalCompositeOperation = 'lighter';             // 加色混合：亮的地方真会“烧”起来
    ctx.globalAlpha = alpha * (isHub ? 1 : 0.92);
    ctx.drawImage(spriteOf(n.color, isHub), x - size/2, y - size/2, size, size);
    const cs = size*0.42;
    ctx.globalAlpha = Math.min(1, alpha*1.15);
    ctx.drawImage(spriteOf(n.color, false), x - cs/2, y - cs/2, cs, cs);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    if(SHOWTEXT && inFocus && (n.core || n.pinned || n.kind==='分类' || n===hov)){
      ctx.fillStyle='rgba(214,234,255,'+(0.55+0.4*s)+')';
      ctx.font=(n.core?'13px':(n.kind==='分类'?'12px':'11px'))+' "Microsoft YaHei",sans-serif';
      const label = n.core ? '总核' : (n.kind==='分类' ? n.text.replace(/[「」]/g,'') : n.text.slice(0,10));
      if(n.kind==='分类'){                                             // 分类名沿半径往外推，别挤在核上
        const dx = x - W/2, dy2 = y - H/2, dl = Math.hypot(dx,dy2) || 1;
        ctx.textAlign = dx >= 0 ? 'left' : 'right';
        ctx.fillText(label, x + dx/dl*40, y + dy2/dl*40 + 4);
        ctx.textAlign = 'left';
      }
      else if(n.core) ctx.fillText(label, x + 12, y + 20);              // 总核的名字放核球下缘
      else ctx.fillText(label, x + r + 5, y + 4);
    }
  }
}

setInterval(()=>{ hover = pick(); showPanel(hover || (focus ? byId[focus] : null)); }, 90);
document.getElementById('hud').innerHTML = (()=>{
  const M = DATA.meta || {};
  const sem = M.semantic ? ` · <b>语义边 ${M.semantic}</b> 条（cos ${M.minCos}–${M.maxCos}，均值 ${M.avgCos}）` : '';
  return `<b>${DATA.nodes.length}</b> 个节点 · <b>${DATA.edges.length}</b> 条边${sem} · 生成于 ${DATA.built}　中键复位 · 拖拽旋转 · 滚轮缩放 · 点一颗星只看它那条星座`;
})();
(function(){
  const z = location.search.match(/[?&]zoom=([0-9.]+)/);
  if(z) zoom = Math.max(0.35, Math.min(1.9, parseFloat(z[1])));
  const m = location.search.match(/[?&]focus=([^&]+)/);
  if(m){ const id = decodeURIComponent(m[1]); if(byId[id]){ focus = id; setMode(1); computeHeat(); } }
})();
step();
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(MIND, "星图.html"))
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    recs, edges, iso = build()
    if not recs:
        print("心事本里还没有记录，先 xin.py add 记几条")
        return 1

    now = time.time()
    nodes = []
    for r in recs:
        imp = max(1, min(5, int(r.get("importance") or 2)))
        age = (now - r["t"]) / 86400.0 if r["t"] else 0.0
        nodes.append({
            "id": r["id"], "kind": r["kind"], "text": r["text"], "tstr": r["tstr"],
            "pinned": int(r["pinned"]), "imp": imp, "deg": r.get("deg", 0),
            "key": r["key"], "chips": r.get("chips", []), "orbit": ORBIT.get(r["kind"], 180),
            "age": round(max(0.0, age), 2),
            "H": 30.0 * (1 + 2 * imp),
        })
    # 同一类的节点均匀排在自己那条轨道上，别靠随机撒点
    from collections import defaultdict
    ring = defaultdict(list)
    for n in nodes:
        ring[n["kind"]].append(n)
    for kind, arr in ring.items():
        arr.sort(key=lambda x: x["tstr"] or "")
        for j, n in enumerate(arr):
            n["slot"] = j
            n["slotN"] = len(arr)

    for e in edges:
        e["sem"] = bool(e["why"].startswith("⑤"))
    sem = [e for e in edges if e["sem"]]
    coses = []
    for e in sem:
        try:
            coses.append(float(e["why"].split("cos=")[1].split("（")[0]))
        except Exception:
            pass
    meta = {"semantic": len(sem),
            "avgCos": round(sum(coses) / len(coses), 3) if coses else 0,
            "minCos": min(coses) if coses else 0,
            "maxCos": max(coses) if coses else 0}
    data = {"nodes": nodes, "edges": edges, "meta": meta,
            "built": time.strftime("%Y-%m-%d %H:%M")}
    core_id = next((n["id"] for n in nodes if n["kind"] == "核心"), "")
    html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__COREID__", json.dumps(core_id))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)

    ratio = len(iso) / float(len(recs) - 2) if len(recs) > 2 else 0.0
    print("节点 %d（含核心/分类）· 边 %d · 孤立 %d (%.0f%%) · 文件 %.1f KB"
          % (len(nodes), len(edges), len(iso), ratio * 100,
             os.path.getsize(a.out) / 1024.0))
    if ratio > 0.10:
        print("⚠ 孤立节点超过 10%%：边的语义定少了，回去补规则，别急着调动画")
    if a.open:
        os.startfile(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
