/**
 * dsh-nebula-mind —— 客户端半边
 *
 * 左栏两个图标位：
 *   「记忆星图」→ 参数配置（透明卡片式）
 *   「心境」    → 仪表盘（环形表 + 数值卡 + 七维 + 沉海分布）
 */
window.__ModuleLoader__.load({
  id: 'dsh-nebula-mind',
  factory: (require) => {
    const React = require('react')
    const h = React.createElement
    const { useState, useEffect, useCallback } = React
    const API = 'api/dsh-nebula-mind'

    async function get(p) {
      const r = await fetch(API + p, { credentials: 'include' })
      if (!r.ok) throw new Error(p + ' ' + r.status)
      return await r.json()
    }
    async function post(p, body) {
      const r = await fetch(API + p, {
        method: 'POST', credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      })
      return await r.json()
    }

    // ── 玻璃卡片风格：跟「上下文洞察」那种透明面板对齐 ──
    const C = {
      wrap: {
        height: '100%', overflow: 'auto', padding: '14px 16px', fontSize: 12, color: '#cfe2f7',
        // 壁纸要留着，但字得有地方站：一层很淡的深色，到底部略深一点
        background: 'linear-gradient(180deg, rgba(4,10,20,.40), rgba(4,10,20,.58))',
        textShadow: '0 1px 2px rgba(0,0,0,.5)',
      },
      head: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 12 },
      title: { fontSize: 15, color: '#eaf4ff', letterSpacing: '.5px' },
      sub: { color: '#6f8aa6', fontSize: 11 },
      grid: { display: 'grid', gap: 10 },
      card: {
        padding: '12px 14px', borderRadius: 12,
        background: 'linear-gradient(180deg, rgba(120,180,255,.09), rgba(120,180,255,.03))',
        border: '1px solid rgba(120,180,255,.22)',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,.05)',
      },
      cardK: { color: '#8fb0cf', fontSize: 11, marginBottom: 6 },
      big: { color: '#eaf6ff', fontSize: 24, fontWeight: 650, fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 },
      unit: { fontSize: 11, color: '#7f9ab5', marginLeft: 3 },
      row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '3px 0' },
      k: { color: '#93aec9' },
      v: { color: '#bfe4ff', fontVariantNumeric: 'tabular-nums' },
      track: { height: 5, borderRadius: 3, background: 'rgba(255,255,255,.06)', overflow: 'hidden', marginTop: 5 },
      fill: (w, c) => ({ height: '100%', width: Math.max(0, Math.min(100, w)) + '%', background: c, borderRadius: 3 }),
      input: {
        width: 78, padding: '3px 6px', borderRadius: 6, border: '1px solid rgba(120,180,255,.22)',
        background: 'rgba(8,18,30,.55)', color: '#dbe9f7', fontSize: 12, textAlign: 'right',
      },
      btn: {
        padding: '6px 14px', borderRadius: 8, border: '1px solid rgba(120,180,255,.35)',
        background: 'linear-gradient(180deg, rgba(90,169,255,.28), rgba(90,169,255,.12))',
        color: '#dcefff', cursor: 'pointer', fontSize: 12,
      },
      btn2: {
        padding: '6px 14px', borderRadius: 8, border: '1px solid rgba(120,180,255,.2)',
        background: 'rgba(255,255,255,.03)', color: '#9db4cc', cursor: 'pointer', fontSize: 12,
      },
      chip: {
        display: 'inline-block', padding: '2px 9px', margin: '0 6px 6px 0', borderRadius: 999,
        background: 'rgba(120,180,255,.10)', border: '1px solid rgba(120,180,255,.18)', color: '#a9c9e8',
      },
      note: { fontSize: 10.5, color: '#7e95ad', marginTop: 4, lineHeight: 1.5 },
    }
    const DIMC = {
      happy: '#ffd479', calm: '#7fe0b0', sweet: '#ffa9d1', heartache: '#b9a6ff',
      worry: '#ffb86b', sad: '#7fb6ff', jealous: '#c8f17f',
    }
    const DIMN = { happy: '开心', calm: '平静', sweet: '甜', heartache: '心疼', worry: '担心', sad: '难过', jealous: '吃醋' }

    // ISO 时间转本地 "MM-DD HH:MM"。以前直接 slice，结果把 UTC 当成北京时间显示，
    // 差整整 8 小时——看着像"半天没更新"，其实是同一刻。
    const fmtAt = (s) => {
      if (!s) return ''
      const t = new Date(String(s))
      if (isNaN(t.getTime())) return String(s).replace('T', ' ').slice(5, 16)
      const p = (n) => String(n).padStart(2, '0')
      return p(t.getMonth() + 1) + '-' + p(t.getDate()) + ' ' + p(t.getHours()) + ':' + p(t.getMinutes())
    }

    // 距今多久，说人话
    const agoCN = (s) => {
      if (!s) return ''
      const t = new Date(String(s))
      if (isNaN(t.getTime())) return ''
      const m = Math.max(0, Math.round((Date.now() - t.getTime()) / 60000))
      if (m < 1) return '刚刚'
      if (m < 60) return m + ' 分钟前'
      const h = Math.floor(m / 60)
      if (h < 24) return h + ' 小时前'
      return Math.floor(h / 24) + ' 天前'
    }

    // 环形表：心情 0–100
    function Ring({ v, size = 118 }) {
      const r = size / 2 - 9, c = 2 * Math.PI * r
      const p = Math.max(0, Math.min(100, v)) / 100
      const col = v >= 75 ? '#7fe0b0' : v >= 45 ? '#6fc0ff' : '#ffa9a0'
      return h('svg', { width: size, height: size, viewBox: `0 0 ${size} ${size}` },
        h('circle', { cx: size / 2, cy: size / 2, r, fill: 'none', stroke: 'rgba(255,255,255,.07)', strokeWidth: 9 }),
        h('circle', {
          cx: size / 2, cy: size / 2, r, fill: 'none', stroke: col, strokeWidth: 9,
          strokeLinecap: 'round', strokeDasharray: `${c * p} ${c}`,
          transform: `rotate(-90 ${size / 2} ${size / 2})`,
          style: { filter: `drop-shadow(0 0 6px ${col}66)` },
        }),
        h('text', {
          x: '50%', y: '50%', dy: '0.36em', textAnchor: 'middle',
          fill: '#eaf6ff', fontSize: 26, fontWeight: 650, fontVariantNumeric: 'tabular-nums',
        }, v.toFixed(1)),
      )
    }

    // ───────── 心境：仪表盘 ─────────
    const HOR = [
      ['noradrenaline', '去甲肾上腺素', 'Noradrenaline', '2 min', '#ff9f7a'],
      ['dopamine', '多巴胺', 'Dopamine', '4 min', '#ffd166'],
      ['gaba', 'GABA', 'GABA', '15 min', '#8fe3c8'],
      ['endorphin', '内啡肽', 'Endorphin', '20 min', '#c9a4ff'],
      ['oxytocin', '催产素', 'Oxytocin', '30 min', '#ff9ec7'],
      ['cortisol', '皮质醇', 'Cortisol', '60 min', '#ff7b7b'],
      ['serotonin', '血清素', 'Serotonin', '24 h', '#7db8ff'],
    ]
    const DIMDESC = { happy: '轻快', calm: '平稳', sweet: '想靠近', heartache: '揪心', worry: '悬着', sad: '低落', jealous: '酸' }

    function Gauge(props) {
      const S = Number(props.size) || 132
      const v = Math.max(0, Math.min(100, Number(props.v) || 0))
      const r = S * 0.41, c = 2 * Math.PI * r, pct = v / 100
      const col = v >= 75 ? '#6ee7b7' : v >= 45 ? '#7db8ff' : '#ff8f8f'
      const fs = Math.round(S * 0.20)
      const cc = S / 2
      return h('svg', { width: S, height: S, viewBox: '0 0 ' + S + ' ' + S },
        h('circle', { cx: cc, cy: cc, r, fill: 'none', stroke: 'rgba(120,150,190,.18)', strokeWidth: S * 0.068 }),
        h('circle', {
          cx: cc, cy: cc, r, fill: 'none', stroke: col, strokeWidth: S * 0.068, strokeLinecap: 'round',
          strokeDasharray: (c * pct).toFixed(1) + ' ' + c.toFixed(1),
          transform: 'rotate(-90 ' + cc + ' ' + cc + ')', style: { filter: 'drop-shadow(0 0 6px ' + col + ')' },
        }),
        h('text', { x: cc, y: cc + fs * 0.36, textAnchor: 'middle', fill: '#eaf3ff', fontSize: fs, fontWeight: 600 }, v.toFixed(1)),
        h('text', { x: cc, y: cc + fs * 1.15, textAnchor: 'middle', fill: '#7f97b5', fontSize: Math.round(fs * 0.42) }, props.label || ''))
    }

    // 七瓣花：每瓣一个维度，长度是强度。比七根横条省一屏，也更有"活物"的感觉。
    function Petal({ dims, max, size = 168 }) {
      const S = size, cc = S / 2
      const keys = Object.keys(DIMN)
      const seg = (Math.PI * 2) / keys.length
      const gap = 0.055
      const inner = S * 0.13, outerMax = S * 0.45
      const pt = (a, r) => [cc + r * Math.cos(a), cc + r * Math.sin(a)]
      const petals = keys.map((k, i) => {
        const v = Math.max(0, Math.min(1, (Number(dims[k]) || 0) / max))
        const r1 = inner + (outerMax - inner) * v
        const a0 = -Math.PI / 2 + i * seg + gap / 2
        const a1 = a0 + seg - gap
        const [x0, y0] = pt(a0, r1), [x1, y1] = pt(a1, r1)
        const [x2, y2] = pt(a1, inner), [x3, y3] = pt(a0, inner)
        const f = (n) => n.toFixed(1)
        const d = `M${f(x0)},${f(y0)} A${f(r1)},${f(r1)} 0 0 1 ${f(x1)},${f(y1)}`
          + ` L${f(x2)},${f(y2)} A${f(inner)},${f(inner)} 0 0 0 ${f(x3)},${f(y3)} Z`
        return h('path', {
          key: k, d, fill: DIMC[k], stroke: DIMC[k], strokeWidth: 1,
          opacity: 0.26 + 0.66 * v,
        })
      })
      return h('svg', { width: S, height: S, viewBox: '0 0 ' + S + ' ' + S },
        h('circle', { cx: cc, cy: cc, r: inner, fill: 'rgba(8,18,32,.62)', stroke: 'rgba(120,180,255,.22)' }),
        ...petals)
    }

    function Mood() {
      const [st, setSt] = useState(null)
      useEffect(() => {
        const pull = () => get('/state').then(setSt).catch(() => {})
        pull()
        const t = setInterval(pull, 30000)
        return () => clearInterval(t)
      }, [])
      if (!st) return h('div', { style: C.wrap }, '读取中…')

      const m = st.mood || {}
      const dims = (st.dims && Object.values(st.dims).some((x) => Number(x) > 0)) ? st.dims : (m.dims || {})
      const hor = m.hormones || st.hormones || {}
      const value = Number(m.mood ?? 0)
      const surf = Number(m.surface ?? value)
      const under = Number(m.underlying ?? value)
      const unresolved = Number(m.unresolved ?? 0)
      const aff = Number(m.affection ?? 60)
      const dimsArr = Object.keys(DIMDESC).map((k) => [k, Number(dims[k] || 0)])
      const dmax = Math.max(6, ...dimsArr.map((d) => d[1]))

      const track = (v) => h('div', { style: C.track },
        h('div', { style: C.fill(Math.max(0, Math.min(100, v)), 'linear-gradient(90deg,#5aa9ff,#8fd3ff)') }))

      const kids = []

      kids.push(h('div', { key: 'hd', style: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 } },
        h('div', { style: { fontSize: 17, fontWeight: 650, color: '#eaf3ff' } }, '心境'),
        h('div', { style: { fontSize: 11, color: '#6d86a4', letterSpacing: '.4px' } }, 'MOOD STATE'),
        h('div', { style: { flex: '1 1 auto' } }),
        h('span', { style: C.chip }, '来源 · ' + (m.source || 'builtin')),
        h('span', { style: { ...C.chip, opacity: .8 } }, fmtAt(m.updated || st.updated))))

      // 顶部：一个环 + 四颗小参数。原来四个参数各占一张卡，像张表单；
      // 它们只是陪衬，不该和心情抢位置，压成一行 chip。
      const chipItem = (label, val, unit) => h('div', {
        key: label,
        style: {
          display: 'flex', alignItems: 'baseline', gap: 4, padding: '3px 9px', borderRadius: 8,
          background: 'rgba(120,180,255,.07)', border: '1px solid rgba(120,180,255,.14)',
        },
      },
        h('span', { style: { fontSize: 10.5, color: '#7e95ad' } }, label),
        h('span', { style: { fontSize: 13, color: '#cfe6ff', fontVariantNumeric: 'tabular-nums' } }, val),
        unit ? h('span', { style: { fontSize: 9.5, color: '#6f8aa6' } }, unit) : null)

      kids.push(h('div', { key: 'g1', style: { display: 'flex', gap: 12, alignItems: 'center' } },
        h('div', { style: { ...C.card, padding: '8px 10px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 } },
          h(Gauge, { v: value, label: '0 – 100', size: 104 })),
        h('div', { style: { flex: '1 1 auto', display: 'flex', flexDirection: 'column', gap: 8 } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' } },
            h('span', { style: { fontSize: 11, color: '#8fb0cf' } }, '主色'),
            h('span', {
              style: {
                padding: '1px 8px', borderRadius: 999, fontSize: 11,
                color: DIMC[m.top_dim] || '#a9c9e8',
                background: (DIMC[m.top_dim] || '#a9c9e8') + '1f',
                border: '1px solid ' + (DIMC[m.top_dim] || '#a9c9e8') + '44',
              },
            }, (DIMN[m.top_dim] || m.top_dim || '—') + ' · ' + (DIMDESC[m.top_dim] || ''))),
          h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6 } },
            chipItem('语速', String(m.rate ?? 0), '%'),
            chipItem('音量', String(m.volume ?? 0), 'dB'),
            chipItem('黏人', Number(m.clingy ?? 0).toFixed(2)),
            chipItem('距离', Number(m.distance ?? 0).toFixed(2))))))

      kids.push(h('div', { key: 'dual', style: { ...C.card, marginTop: 10 } },
        h('div', { style: C.cardK }, '双层情绪 · Surface / Underlying'),
        h('div', { style: C.note }, '表层是这一句之后的即时反应，底色是这几天攒下来的；两个合起来才是此刻。'),
        h('div', { style: { display: 'flex', gap: 16, marginTop: 10 } },
          h('div', { style: { flex: 1 } },
            h('div', { style: C.row }, h('span', { style: C.k }, '表层 surface'), h('span', { style: C.v }, surf.toFixed(1))),
            track(surf)),
          h('div', { style: { flex: 1 } },
            h('div', { style: C.row }, h('span', { style: C.k }, '底色 underlying'), h('span', { style: C.v }, under.toFixed(1))),
            track(under)))))

      // 激素：七根小柱子。原来是七行横条 + 七种颜色，那是整块面板最大的噪音源；
      // 改成同一色相按半衰期渐变，从左到右越来越慢，一眼能看出快慢。
      const horBar = HOR.map((it, i) => {
        const v = Number(hor[it[0]] ?? 50)
        const d = v - 50
        const hgt = Math.max(6, Math.min(100, v) * 0.52)
        // 高于基线的用亮蓝，低于基线的压暗——不然七根柱子看起来一样高
        const col = d >= 0 ? 'hsl(212, 72%, ' + (44 + i * 5) + '%)' : 'hsl(212, 26%, ' + (36 + i * 4) + '%)'
        return h('div', {
          key: it[0], title: it[1] + ' · 半衰期 ' + it[3],
          style: { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 },
        },
          h('div', { style: { fontSize: 9.5, color: d >= 0 ? '#bfe4ff' : '#8299b1', fontVariantNumeric: 'tabular-nums' } },
            (d >= 0 ? '+' : '') + d.toFixed(0)),
          h('div', { style: { height: 56, display: 'flex', alignItems: 'flex-end' } },
            h('div', { style: { width: 13, height: hgt, borderRadius: 3, background: col } })),
          h('div', { style: { fontSize: 9, lineHeight: 1.15, color: '#6f8aa6', textAlign: 'center', height: 21, wordBreak: 'break-all' } }, it[1]))
      })
      kids.push(h('div', { key: 'hor', style: { ...C.card, marginTop: 10 } },
        h('div', { style: C.cardK }, '激素 · 七条'),
        h('div', { style: { display: 'flex', gap: 6, alignItems: 'flex-end', marginTop: 8 } }, horBar),
        h('div', { style: C.note }, '不改心情，只改「我多容易被带动」。柱高是此刻离基线 50 多远；从左到右半衰期越来越长——最左边两分钟就散，最右边的要一天。')))

      kids.push(h('div', { key: 'dims', style: { ...C.card, marginTop: 10 } },
        h('div', { style: C.cardK }, '情绪 · 七瓣'),
        h('div', { style: { display: 'flex', gap: 12, alignItems: 'center', marginTop: 6 } },
          h('div', { style: { flex: '0 0 auto' } }, h(Petal, { dims, max: dmax })),
          h('div', { style: { flex: '1 1 auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 16px' } },
            dimsArr.map((it) => h('div', { key: it[0], style: { display: 'flex', alignItems: 'center', gap: 5 } },
              h('span', { style: { width: 7, height: 7, borderRadius: 2, background: DIMC[it[0]], flex: '0 0 auto', opacity: .9 } }),
              h('span', { style: { fontSize: 10.5, color: '#93aec9', flex: '1 1 auto' } }, DIMN[it[0]]),
              h('span', { style: { fontSize: 10.5, color: DIMC[it[0]], fontVariantNumeric: 'tabular-nums' } }, it[1].toFixed(1))))))))

      const layerRows = (st.layers || []).map((L) => h('div', { key: L.name, style: { marginTop: 6 } },
        h('div', { style: C.row }, h('span', { style: C.k }, L.name), h('span', { style: C.v }, L.n + ' 条')),
        h('div', { style: C.track }, h('div', { style: C.fill((L.n / (st.total || 1)) * 100, 'linear-gradient(90deg,#5aa9ff,#8fd3ff)') }))))
      kids.push(h('div', { key: 's3', style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 } },
        h('div', { style: C.card },
          h('div', { style: C.cardK }, '未解开的结'),
          h('div', { style: { ...C.big, fontSize: 20 } }, String(unresolved)),
          h('div', { style: C.note }, '没消化完的事，压着底色')),
        h('div', { style: C.card },
          h('div', { style: C.cardK }, '好感'),
          h('div', { style: { ...C.big, fontSize: 20 } }, aff.toFixed(1)),
          track(aff),
          h('div', { style: C.note }, '慢变量，三十天才褪一半'))))

      kids.push(h('div', { key: 's4', style: { ...C.card, marginTop: 10 } },
        h('div', { style: C.cardK }, '沉海分布 · ' + (st.total || 0) + ' 条记忆'),
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5, marginTop: 6 } }, layerRows)))

      kids.push(h('div', { key: 'why', style: { ...C.card, marginTop: 10 } },
        h('div', { style: C.cardK }, '此刻的由来' + (m.reasonAt ? '  ·  ' + agoCN(m.reasonAt) : '')),
        h('div', { style: { color: '#a9c4de', lineHeight: 1.7 } }, String(st.reason || m.reason || '（还没记）').slice(0, 140))))

      return h('div', { className: 'yml', style: C.wrap }, kids)
    }

    // ───────── 记忆星图：参数配置 ─────────
    const GROUPS = [
      ['layers', 'L0–L4 沉海层 / 层级与阈值',
        '每层给一个检索权重：越浅越容易被想起。A 是“新鲜度”，低于阈值就往下一层沉。', [
        ['L0_core', 'L0 核心 · 检索权重'], ['L1_hot', 'L1 热 · 检索权重'],
        ['L2_warm', 'L2 温 · 检索权重'], ['L3_deep', 'L3 深水 · 检索权重'],
        ['L4_attic', 'L4 阁楼 · 检索权重'],
        ['A_hot', 'A ≥ ? 归热'], ['A_warm', 'A ≥ ? 归温'], ['A_deep', 'A ≥ ? 归深水'],
      ]],
      ['retrieval', '检索与融合 / Hybrid Retrieval',
        // 术语对齐：三路召回（lexical / dense / graph）→ RRF → 情绪调制乘子。
        // 情绪共振不是召回通道（没有候选集、不参与排名），只是对融合分数的缩放。
        // 依据：emotional-memory 把情绪当独立通道，LoCoMo F1 掉到 0.168（朴素 RAG 0.271）。
        '三路召回——字面 BM25（lexical）、语义向量（dense）、图扩散 PPR（graph）——各自排名后用 RRF（Reciprocal Rank Fusion）按名次融合；情绪共振不产生候选，只是融合后的调制乘子（affective modulation），用来缩放已排好的分数。', [
        ['w_bm25', '字面 BM25（lexical）'], ['w_vec', '语义向量（dense）'],
        ['w_graph', '图扩散 PPR（graph）'], ['w_emo', '情绪调制（乘子，不排名次）'],
        ['rrf_k', 'RRF 平滑常数 K'], ['graph_hop1', '图一跳衰减'], ['graph_hop2', '图二跳衰减'],
        ['ppr_damping', 'PPR 阻尼（越大越沿边走远）'], ['ppr_iters', 'PPR 迭代次数'],
      ]],
      ['sink', '沉海机制 / Sink & Lift',
        '被反复想起的会浮上来；情绪突然变强时，同一情绪的老记忆也会被拉回来。', [
        ['sink_hits_lift', '召回几次上浮一层'], ['lift_step', '召回强化幅度'],
        ['shock_delta', '情绪剧震阈值'], ['shock_pull', '剧震拉回到第几层'],
      ]],
      ['forget', '遗忘曲线 / Forgetting Curve',
        '保留率 A = 2^(−天数 / 半衰期)；半衰期 = 基数 × (1 + 重要度倍数 × 重要度)。', [
        ['half_base', '半衰期基数（天）'], ['half_imp', '重要度倍数'],
        ['half_emo', '情绪倍数'], ['min_w', '低于此权重不再下沉'],
      ]],
    ]

    function Curve({ base, impK, emoK }) {
      const W = 460, H = 150, P = 26
      const xMax = 180
      const y = (a) => H - P - a * (H - P * 1.4)
      const x = (d) => P + (d / xMax) * (W - P * 1.4)
      const line = (imp, col) => {
        const half = Math.max(1, base * (1 + impK * imp))
        const pts = []
        for (let d = 0; d <= xMax; d += 2) {
          const a = Math.pow(2, -d / half)
          pts.push(`${x(d).toFixed(1)},${y(a).toFixed(1)}`)
        }
        return h('polyline', { points: pts.join(' '), fill: 'none', stroke: col, strokeWidth: 2, opacity: .95 })
      }
      return h('div', null,
        h('svg', { width: '100%', viewBox: `0 0 ${W} ${H}`, style: { display: 'block' } },
          [0, .25, .5, .75, 1].map((a) => h('line', {
            key: a, x1: P, x2: W - 6, y1: y(a), y2: y(a),
            stroke: 'rgba(255,255,255,.07)', strokeWidth: 1,
          })),
          [0, .5, 1].map((a) => h('text', {
            key: 't' + a, x: 4, y: y(a) + 4, fill: '#6f8aa6', fontSize: 9,
          }, Math.round(a * 100) + '%')),
          [0, 60, 120, 180].map((d) => h('text', {
            key: 'x' + d, x: x(d), y: H - 6, fill: '#6f8aa6', fontSize: 9, textAnchor: 'middle',
          }, d + '天')),
          line(1, '#6fc0ff'), line(3, '#ffd479'), line(5, '#ff9fb0'),
        ),
        h('div', { style: { color: '#7f9ab5', marginTop: 4 } },
          '蓝＝重要度 1，黄＝3，粉＝5；半衰期分别是 ' +
          [1, 3, 5].map((i) => Math.round(base * (1 + impK * i)) + ' 天').join(' / ')))
    }

    const STAGES = [
      ['M0', '记忆库分层', '每条记忆补上权重、重要度、情绪、层级'],
      ['M1', '三路召回 + 情绪调制', '字面/语义/图三路排名融合，情绪只缩放分数'],
      ['M2', '沉海与上浮', '按新鲜度分层，被想起的浮上来'],
      ['M3', '情绪参数', '心情换算成语速、音量、黏人度、距离感'],
      ['M4', '界面与面板', '左侧栏两个面板 + 星图网页'],
      ['M5', '回归测试', '换个新会话，用你自己的问题问一遍——答不上来的那几处，就是该调权重的地方'],
    ]

    function Num({ k, label, value, onChange }) {
      return h('div', { style: C.row },
        h('span', { style: C.k }, label),
        h('input', {
          style: C.input, type: 'number', step: '0.01', value,
          onChange: (e) => onChange(k, Number(e.target.value)),
        }))
    }

    function Config() {
      const [cfg, setCfg] = useState(null)
      const [msg, setMsg] = useState('')
      const [full, setFull] = useState(false)
      const [sim, setSim] = useState(0)
      const [hop, setHop] = useState(0)
      const [text, setText] = useState(true)
      const [lines, setLines] = useState(true)
      const [st, setSt] = useState(null)
      const [q, setQ] = useState('')
      const [hits, setHits] = useState(null)
      const [busy, setBusy] = useState(false)
      const box = React.useRef(null)
      const big = React.useRef(null)

      useEffect(() => { get('/config').then((d) => setCfg(d.config)).catch((e) => setMsg(String(e))) }, [])
      useEffect(() => { get('/state').then((d) => setSt(d)).catch(() => {}) }, [])

      const search = async () => {
        const t = q.trim()
        if (!t) return
        setBusy(true); setHits(null)
        try {
          const r = await get('/recall?q=' + encodeURIComponent(t) + '&n=6')
          setHits(r && r.ok ? (r.lines || []) : ['检索失败：' + ((r && r.err) || '未知')])
        } catch (e) { setHits(['检索失败：' + String(e)]) }
        finally { setBusy(false) }
      }

      // ESC 退出全屏
      useEffect(() => {
        if (!full) return
        const onKey = (e) => { if (e.key === 'Escape') setFull(false) }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
      }, [full])

      // 全屏里按 ESC：星图会把消息递回来
      useEffect(() => {
        const onMsg = (e) => {
          const d = e.data || {}
          if (d.type === 'yumind' && d.action === 'close') setFull(false)
        }
        window.addEventListener('message', onMsg)
        return () => window.removeEventListener('message', onMsg)
      }, [])

      // 把开关推给星图（postMessage，不重载 iframe）
      useEffect(() => {
        for (const r of [box.current, big.current]) {
          try { r && r.contentWindow && r.contentWindow.postMessage({ type: 'yumind', sim, hop, text, lines }, '*') } catch (e) {}
        }
      }, [sim, hop, text, lines, full])

      const set = useCallback((k, v) => setCfg((c) => ({ ...c, [k]: v })), [])

      const save = async () => {
        setMsg('保存中…')
        try {
          const r = await get('/config?set=' + encodeURIComponent(JSON.stringify(cfg)))
          if (r && r.ok) {
            setCfg(r.config)
            const tk = r.task && r.task.ok ? ' · 计划任务已同步' : (r.task ? ' · 计划任务：' + (r.task.msg || '同步失败') : '')
            setMsg('已写入后台 · ' + (r.at || '').replace('T', ' ').slice(0, 19) + tk)
          }
          else setMsg('保存失败：' + ((r && r.err) || '未知'))
        } catch (e) {
          try {
            const r2 = await post('/config', cfg)
            if (r2 && r2.ok) { setCfg(r2.config); setMsg('已写入后台（POST 通道）') }
            else setMsg('保存失败：' + ((r2 && r2.err) || String(e)))
          } catch (e2) { setMsg('保存失败：' + String(e2)) }
        }
      }

      if (!cfg) return h('div', { style: C.wrap }, msg || '读取中…')

      const Bar = h('div', {
        style: {
          display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8,
          padding: '8px 10px', marginBottom: 10, borderRadius: 10,
          background: 'rgba(8,16,28,.55)', border: '1px solid rgba(120,180,255,.16)',
        },
      },
        h('span', { style: { color: '#93aec9' } }, '遗忘推演'),
        h('input', {
          type: 'range', min: 0, max: 365, value: sim,
          onChange: (e) => setSim(Number(e.target.value)),
          style: { width: 130, accentColor: '#5aa9ff' },
        }),
        h('span', { style: { ...C.v, width: 52 } }, sim + ' 天'),
        h('span', { style: { color: '#24425f' } }, '|'),
        h('span', { style: { color: '#93aec9' } }, '视野'),
        ...[0, 1, 2].map((k) => h('button', {
          key: k, onClick: () => setHop(k),
          style: { ...C.btn2, padding: '3px 10px', borderColor: hop === k ? 'rgba(120,180,255,.6)' : undefined, color: hop === k ? '#dcefff' : undefined },
        }, ['全部', '一度关联', '二度关联'][k])),
        h('span', { style: { color: '#24425f' } }, '|'),
        h('button', { style: { ...C.btn2, padding: '3px 10px' }, onClick: () => setText((v) => !v) }, '文字：' + (text ? '开' : '关')),
        h('button', { style: { ...C.btn2, padding: '3px 10px' }, onClick: () => setLines((v) => !v) }, '线条：' + (lines ? '开' : '关')),
        h('button', {
          style: { ...C.btn2, padding: '3px 10px' },
          onClick: () => { setSim(0); setHop(0); setText(true); setLines(true) },
        }, '重置视图'),
      )

      const skin = h('style', null, `
        .yml button{ transition: background .15s, border-color .15s, transform .12s; }
        .yml button:hover{ background: rgba(120,180,255,.20) !important; border-color: rgba(120,180,255,.62) !important; }
        .yml button:active{ transform: translateY(1px); }
      `)

      return h('div', { className: 'yml', style: C.wrap },
        skin,
        // 顶部提示条：保存结果不用滚到底才看得见
        msg ? h('div', {
          style: {
            position: 'sticky', top: 0, zIndex: 3, marginBottom: 8, padding: '6px 10px',
            borderRadius: 8, background: 'rgba(8,16,28,.85)', border: '1px solid rgba(120,180,255,.22)',
            color: '#a9d0f5', backdropFilter: 'blur(6px)',
          },
        }, msg) : null,

        h('div', { style: C.head },
          h('div', { style: C.title }, 'Memory Nebula · 参数'),
          h('div', { style: C.sub }, 'config → 心事本/mind-config.json')),

        // 空数据引导：装上还是空的，别让人以为坏了
        (st && st.total === 0) ? h('div', {
          style: {
            marginBottom: 10, padding: '12px 14px', borderRadius: 10, lineHeight: 1.7,
            background: 'rgba(10,22,38,.72)', border: '1px dashed rgba(120,180,255,.35)', color: '#bcd6f0',
          },
        },
          h('div', { style: { color: '#e5f2ff', fontWeight: 600, marginBottom: 4 } }, '还没有记忆 —— 这是第一颗星的位子'),
          h('div', null, '记下第一条，星图、召回、心境就会开始长。任选一种：'),
          h('div', { style: { marginTop: 6, fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 12, color: '#9fd0ff' } },
            'python python/xin.py add "今天他提到周五要交画册"'),
          h('div', { style: { fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 12, color: '#9fd0ff' } },
            'python python/doctor.py   # 看一眼这台机器缺什么'),
          h('div', { style: { marginTop: 6, color: '#8fb0d0', fontSize: 12 } },
            '也可以直接对我说「记住：……」—— 我会用 xin.py 写进去。'),
        ) : null,

        // 预览
        h('div', { style: { ...C.card, marginBottom: 8, padding: 8 } },
          h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 } },
            h('div', { style: { color: '#a9d0f5' } }, 'Nebula Preview'),
            h('button', { style: { ...C.btn2, padding: '3px 10px' }, onClick: () => setFull(true) }, '全屏预览')),
          h('iframe', {
            ref: box, src: API + '/star?embed=1',
            style: { width: '100%', height: 300, border: '1px solid rgba(120,180,255,.18)', borderRadius: 10, background: '#05090f', display: 'block' },
            title: 'Nebula Preview',
          })),
        Bar,

        // 搜索：面板里就能翻旧事（挪到遗忘推演下面——它只读，不参与沉浮）
        h('div', { style: { color: '#7089a3', fontSize: 12, lineHeight: 1.6, marginBottom: 6 } },
          '查旧账：这里搜多少次都不会让记忆上浮或下沉。沉浮只由夜间的巩固和真实的记录决定，' +
          '翻看不改变任何东西。'),

        // 搜索：面板里就能翻旧事
        h('div', {
          style: {
            display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10,
            padding: '8px 10px', borderRadius: 10,
            background: 'rgba(8,16,28,.55)', border: '1px solid rgba(120,180,255,.16)',
          },
        },
          h('input', {
            value: q, onChange: (e) => setQ(e.target.value),
            onKeyDown: (e) => { if (e.key === 'Enter') search() },
            placeholder: '翻旧事：关键词 / 一句话都行',
            style: {
              flex: 1, background: 'rgba(6,12,22,.8)', border: '1px solid rgba(120,180,255,.22)',
              borderRadius: 8, padding: '6px 10px', color: '#dcecff', outline: 'none',
            },
          }),
          h('button', { style: { ...C.btn2, padding: '5px 12px' }, onClick: search }, busy ? '检索中…' : '召回'),
        ),
        hits ? h('div', {
          style: {
            marginBottom: 10, padding: '8px 12px', borderRadius: 10,
            background: 'rgba(8,16,28,.55)', border: '1px solid rgba(120,180,255,.12)',
            color: '#c8ddf5', fontSize: 12.5, lineHeight: 1.65, whiteSpace: 'pre-wrap',
          },
        }, hits.length ? hits.join('\n') : '没找到（换几个词试试）') : null,


        ...GROUPS.map(([gid, title, desc, items]) => h('div', { key: gid, style: { ...C.card, marginBottom: 10 } },
          h('div', { style: { color: '#a9d0f5', marginBottom: 2 } }, title),
          h('div', { style: { color: '#7089a3', lineHeight: 1.7, marginBottom: 6 } }, desc),
          gid === 'forget' ? h(Curve, { base: cfg.half_base, impK: cfg.half_imp, emoK: cfg.half_emo }) : null,
          ...items.map(([k, label]) => h(Num, { key: k, k, label, value: cfg[k], onChange: set })),
        )),

        h('div', { ...C.card, marginBottom: 10 },
          h('div', { style: { color: '#a9d0f5', marginBottom: 2 } }, '夜间巩固 / Nightly Consolidation'),
          h('div', { style: { color: '#7089a3', lineHeight: 1.7, marginBottom: 6 } },
            '睡眠依赖的巩固：把当天的事归档、按权重往下沉、重算向量、重画星图。改成几点，会同步 Windows 计划任务。'),
          h('div', { style: { ...C.row, gap: 10 } },
            h('span', { style: { ...C.k, width: 132 } }, '巩固时刻（HH:MM）'),
            h('input', {
              type: 'time', value: cfg.nightly_at || '22:05',
              onChange: (e) => set('nightly_at', e.target.value),
              style: { background: 'rgba(12,24,40,.7)', border: '1px solid rgba(120,180,255,.28)',
                borderRadius: 8, color: '#cfe6ff', padding: '4px 8px', font: 'inherit' },
            }),
            h('button', {
              style: { ...C.btn2, padding: '3px 10px' },
              onClick: () => set('nightly_on', Number(cfg.nightly_on) ? 0 : 1),
            }, Number(cfg.nightly_on) ? '已启用' : '已停用')),
          h('div', { style: { color: '#5f7691', marginTop: 6, fontSize: 12 } },
            '改完点下面的「保存」，宿主会重建这个计划任务（/Create /F，幂等），不用手动动任务计划。')),

        h('div', { ...C.card, marginBottom: 10 },
          h('div', { style: { color: '#a9d0f5', marginBottom: 2 } }, 'Roadmap · 建设阶段'),
          h('div', { style: { color: '#7089a3', lineHeight: 1.7, marginBottom: 6 } },
            '六个阶段：记忆存储 → 混合检索 → 沉海与召回 → 情绪参数 → 界面 → 回归验证。'),
          h('div', {}, ...STAGES.map(([k, name, note]) => {
            const st = (cfg.stages || {})[k] || 'todo'
            const done = st === 'done', ready = st === 'ready'
            return h('div', { key: k, style: { ...C.row, alignItems: 'flex-start' } },
              h('span', { style: { ...C.chip, margin: 0, color: done ? '#8fe0b8' : (ready ? '#8fc7ff' : '#ffc98a') } }, done ? 'DONE' : (ready ? 'READY' : 'TODO')),
              h('span', { style: { flex: 1, marginLeft: 8, color: '#a9c4de' } },
                h('b', { style: { color: '#cfe2f7' } }, name),
                h('span', { style: { color: '#7089a3' } }, ' · ' + note)))
          }))),

        h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap' } },
          h('button', { style: C.btn2, onClick: save }, '保存'),
          h('button', {
            style: C.btn2,
            onClick: async () => {
              setMsg('恢复中…')
              try { const d = await get('/config?reset=1'); setCfg(d.config); setMsg('已恢复默认') }
              catch (e) { setMsg('恢复失败：' + String(e)) }
            },
          }, '恢复默认'),
          h('button', {
            style: C.btn2,
            onClick: async () => {
              setMsg('正在用默认浏览器打开…')
              try {
                const r = await get('/open')
                setMsg(r && r.ok ? '已打开浏览器 · ' + (r.url || '') : '打开失败')
              } catch (e) {
                // 最常见的原因：宿主还是旧的（没彻底重启），路由不存在 → 404
                setMsg('打开失败（宿主可能没重启）：也可以在浏览器里直接打开数据目录下的 星图.html')
              }
            },
          }, '浏览器打开星图'),
        ),

        // 全屏：只留 ESC 退出
        full ? h('div', {
          style: { position: 'fixed', left: 0, right: 0, top: 38, bottom: 0, zIndex: 9999, background: '#05090f' },
        },
          h('iframe', {
            ref: big, src: API + '/star?hudtop=58&esc=1',
            style: { width: '100%', height: '100%', border: '0', display: 'block' },
            title: 'Nebula Full',
          }),
          h('div', {
            style: {
              position: 'absolute', left: 12, top: 8, zIndex: 2, display: 'flex', alignItems: 'center', gap: 10,
            },
          },
            h('button', {
              style: { ...C.btn2, padding: '4px 12px', background: 'rgba(8,16,28,.75)' },
              onClick: () => setFull(false),
            }, '关闭 ✕'),
            h('span', { style: { color: '#8fb0cf', fontSize: 11, textShadow: '0 1px 2px #000' } }, 'ESC 退出'))) : null,
      )
    }

    // ── 设置页：心境来源 ──────────────────────────────────────────
    // 把 mind-config.json 的 mood 段做成界面：三选一 + 自动识别 + 测试。
    // 别人装了包不用手抠 JSON；认错了也能手动改路径和字段。
    const S = {
      wrap: { padding: '2px 0', fontSize: 12, color: '#cfe2f7' },
      title: { fontSize: 14, color: '#eaf4ff', marginBottom: 3 },
      sub: { color: '#6f8aa6', fontSize: 11, marginBottom: 12, lineHeight: 1.7 },
      box: {
        border: '1px solid rgba(120,180,255,.16)', borderRadius: 10, padding: '10px 12px',
        background: 'rgba(8,18,30,.35)', marginBottom: 10,
      },
      lbl: { color: '#8fb0cf', fontSize: 11, marginBottom: 5 },
      wide: {
        width: '100%', boxSizing: 'border-box', padding: '6px 9px', borderRadius: 7,
        border: '1px solid rgba(120,180,255,.22)', background: 'rgba(8,18,30,.6)',
        color: '#dbe9f7', fontSize: 12, outline: 'none',
      },
      row: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 },
      cand: {
        padding: '7px 10px', borderRadius: 8, border: '1px solid rgba(120,180,255,.14)',
        background: 'rgba(255,255,255,.025)', cursor: 'pointer', marginBottom: 6,
      },
      msg: { marginTop: 4, fontSize: 11.5, color: '#9fd8b0', lineHeight: 1.6 },
    }

    function Section() {
      const [tab, setTab] = useState('builtin')
      const [file, setFile] = useState('')
      const [map, setMap] = useState({})
      const [url, setUrl] = useState('')
      const [tmo, setTmo] = useState(800)
      const [list, setList] = useState([])
      const [msg, setMsg] = useState('')
      const [busy, setBusy] = useState(false)

      useEffect(() => {
        (async () => {
          try {
            const r = await get('/mood-sources')
            setList(r.list || [])
            const c = r.cfg || {}
            setTab(c.provider || 'builtin')
            if (c.file) setFile(String(c.file).replace(/\\/g, '/'))
            if (c.map) setMap(c.map)
            if (c.url) setUrl(c.url)
            if (c.timeoutMs) setTmo(c.timeoutMs)
          } catch (e) { setMsg('读不到配置：' + e.message) }
        })()
      }, [])

      const body = () => {
        const b = { provider: tab }
        if (tab === 'file') { b.file = file; b.map = map }
        if (tab === 'http') { b.url = url; b.timeoutMs = Number(tmo) || 800 }
        return b
      }

      const save = async () => {
        setBusy(true); setMsg('')
        try {
          const r = await post('/mood-config', body())
          setMsg(r.ok ? '存好了。下一次读状态就换这路（不用重启）。' : '存失败：' + (r.err || ''))
        } catch (e) { setMsg('存失败：' + e.message) }
        setBusy(false)
      }

      const test = async () => {
        setBusy(true); setMsg('')
        try {
          const r = await post('/mood-test', body())
          if (r.ok) {
            const g = r.got || {}
            const ds = g.dims || {}
            const n = Object.keys(ds).filter((k) => ds[k] > 0).length
            setMsg('读到 ' + (Math.round((g.value || 0) * 10) / 10) + ' · ' + n + '/7 维 · 来源 ' + (g.source || '?'))
          } else setMsg('测试失败：' + (r.err || ''))
        } catch (e) { setMsg('测试失败：' + e.message) }
        setBusy(false)
      }

      const chip = (k, label) => h('button', {
        key: k, onClick: () => { setTab(k); setMsg('') },
        style: Object.assign({}, C.chip, {
          cursor: 'pointer', fontSize: 12, padding: '5px 12px', margin: 0,
          background: tab === k ? 'rgba(93,168,255,.24)' : 'rgba(120,180,255,.07)',
          borderColor: tab === k ? 'rgba(120,190,255,.6)' : 'rgba(120,180,255,.18)',
          color: tab === k ? '#eaf4ff' : '#9db4cc',
        }),
      }, label)

      const pick = (c) => { setFile(c.path); setMap(c.map || {}); setTab('file'); setMsg('') }

      return h('div', { style: S.wrap },
        h('div', { style: S.title }, '心境来源'),
        h('div', { style: S.sub }, '这块心情从哪来。默认用自带的引擎——激素、双层情绪、沉海都靠它。也可以换成别的情绪插件。'),

        h('div', { style: { display: 'flex', gap: 8, marginBottom: 12 } },
          chip('builtin', '自带引擎'), chip('file', '读本地文件'), chip('http', '调用接口')),

        tab === 'builtin' ? h('div', { style: S.box },
          h('div', { style: { color: '#9fd8b0', fontSize: 11.5, marginBottom: 4 } }, '自带引擎 · 全功能'),
          h('div', { style: C.note }, '七维情绪、七种激素、双层情绪、沉海分布、没解开的结——只有这路能给。数据在 心事本/mood-state.json。'),
        ) : null,

        tab === 'file' ? h('div', { style: S.box },
          h('div', { style: S.lbl }, '状态文件路径'),
          h('div', { style: S.row },
            h('input', {
              style: Object.assign({}, S.wide, { flex: '1 1 auto' }),
              value: file, placeholder: 'C:/Users/你/.dsh/某某插件/state.json',
              onChange: (e) => setFile(e.target.value),
            })),
          h('div', { style: Object.assign({}, C.note, { marginBottom: 8 }) },
            '认得出两种形状：嵌套的 moods.__default__.mood，或者扁平的 value / mood + dims + reason。'),

          list.filter((c) => !c.builtin).length ? [
            h('div', { key: 'k', style: S.lbl }, '本机找到的（点一下就用它）'),
            ...list.filter((c) => !c.builtin).map((c) => h('div', { key: c.path, style: S.cand, onClick: () => pick(c) },
              h('div', { style: { fontSize: 11.5, color: '#cfe2f7', wordBreak: 'break-all' } }, c.path),
              h('div', { style: { fontSize: 10.5, color: '#6f8aa6', marginTop: 2 } },
                c.kind + ' · 心情 ' + c.value + ' · ' + c.dims + '/7 维'))),
          ] : h('div', { key: 'k', style: C.note }, '没扫到别的情绪文件（或者它们形状对不上）。可以直接填路径。'),

          h('div', { key: 'm', style: Object.assign({}, C.note, { marginTop: 10 }) },
            '字段映射（留空就按上面的形状自动认）：'),
          [['value', '心情值'], ['dims', '七维'], ['reason', '一句话']].map(([k, lbl]) =>
            h('div', { key: k, style: Object.assign({}, S.row, { marginTop: 4 }) },
              h('span', { style: { width: 64, color: '#8fb0cf', fontSize: 11 } }, lbl),
              h('input', {
                style: Object.assign({}, S.wide, { flex: '1 1 auto' }),
                value: map[k] || '', placeholder: '$.' + k,
                onChange: (e) => setMap(Object.assign({}, map, { [k]: e.target.value })),
              }))),
        ) : null,

        tab === 'http' ? h('div', { style: S.box },
          h('div', { style: S.lbl }, '接口地址'),
          h('div', { style: S.row },
            h('input', {
              style: Object.assign({}, S.wide, { flex: '1 1 auto' }),
              value: url, placeholder: 'http://127.0.0.1:1234/mood',
              onChange: (e) => setUrl(e.target.value),
            })),
          h('div', { style: S.row },
            h('span', { style: { color: '#8fb0cf', fontSize: 11 } }, '超时'),
            h('input', {
              style: Object.assign({}, C.input, { width: 70 }),
              value: tmo, onChange: (e) => setTmo(e.target.value),
            }),
            h('span', { style: { color: '#8fb0cf', fontSize: 11 } }, '毫秒')),
          h('div', { style: C.note },
            '对方返回 {"value": 71.2, "dims": {...}, "reason": "..."} 就行。超时没回就退回自带引擎，不会让面板卡住。'),
        ) : null,

        h('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
          h('button', { style: Object.assign({}, C.btn, { opacity: busy ? .6 : 1 }), disabled: busy, onClick: save }, '保存'),
          h('button', { style: Object.assign({}, C.btn2, { opacity: busy ? .6 : 1 }), disabled: busy, onClick: test }, '测试一下')),

        msg ? h('div', { style: S.msg }, msg) : null,
      )
    }

    function Icon() { return h('span', { style: { fontSize: 14 } }, '✧') }

    function apply(ctx) {
      const slots = ctx.get('slots')
      if (!slots) return
      const safe = (fn) => { try { fn() } catch (e) { console.warn('[nebula-mind] slot', e) } }
      safe(() => slots.inject('main', () => slots.register({ name: 'main', key: 'yu-mind' }, Config)))
      safe(() => slots.inject('sidebar.panellist', () =>
        slots.register({ name: 'sidebar.panellist', id: 'yu-mind', order: 20, label: () => '记忆星图' }, Icon)))
      safe(() => slots.inject('main', () => slots.register({ name: 'main', key: 'yu-mood' }, Mood)))
      safe(() => slots.inject('sidebar.panellist', () =>
        slots.register({ name: 'sidebar.panellist', id: 'yu-mood', order: 21, label: () => '心境' }, Icon)))
      safe(() => slots.inject('settings.section', () =>
        slots.register({ name: 'settings.section', id: 'yu-mood-src', order: 54, label: () => '心境来源' }, Section)))
    }

    return { apply, inject: ['slots'] }
  },
})
