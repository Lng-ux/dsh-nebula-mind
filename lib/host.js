/**
  * dsh-yu-mind —— 记忆星图 / 心境（宿主半边）
 *
 * 路由前缀为什么是 /api/dsh-yu-mind：桌面外壳里页面在 dsh-app:// 下，
 * 客户端只能发相对请求 api/xxx（见 dsh-context 的写法），宿主端必须挂在 /api 下。
 * 裸路径（不带 /api）也一起挂，方便命令行 curl 调试。
 *
 * 只做三件事：
 *   1. 把心事本与心情摊成 JSON 给界面用
 *   2. 把星图.html 直接吐出去（界面里用 iframe 嵌）
 *   3. 留一个 recall 的 HTTP 入口，界面里的搜索框走它
 *
 * 记忆真源仍是 D:\小鱼的办公区\心事本\*.jsonl，这里只读。
 */
import { readFile, readdir, writeFile, appendFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'

// 工具的 parameters 是「简写」，得由 defineTool 转成真正的 JSON Schema，
// 直接丢给 ctx.tools.register 会被 JsonSchemaError 拒掉（schema.type 必须是 object…）。
// 解析不到时退回简写（那样 register 会报错，但错误会写进 logs/yu-mind-boot.log）。
let defineTool = (spec) => spec
try {
  const m = await import('@deepseek-ai/dsh-tools')
  if (m && typeof m.defineTool === 'function') defineTool = m.defineTool
} catch { /* 软链场景：保持 identity */ }

export const name = 'yu-mind'
export const inject = ['webServer', 'agents', 'tools', 'systemPrompt']   // systemPrompt 必须声明，否则 ctx.systemPrompt 是 undefined

// 数据与安装目录分开：数据在 NEBULA_HOME（默认 ~/.dsh/nebula-mind），
// 代码在本包里，升级覆盖不会碰记忆。
const HOME = process.env.NEBULA_HOME || join(homedir(), '.dsh', 'nebula-mind')
const MIND = process.env.NEBULA_MIND || join(HOME, 'memory')
const LOGS = join(HOME, 'logs')
const __dirname = dirname(fileURLToPath(import.meta.url))
const PYDIR = join(__dirname, '..', 'python')
const PY = process.env.NEBULA_PYTHON || 'python'
const XIN = join(PYDIR, 'xin.py')

const FILES = [
  ['notes.jsonl', '事实'],
  ['thoughts.jsonl', '念想'],
  ['promises.jsonl', '约定'],
  ['tacts.jsonl', '分寸'],
  ['profile.jsonl', '画像'],
]

const LAYERS = ['核心', '热', '温', '深水', '阁楼']

// 挂载留痕：工具注册成功/失败都写一行，方便在 DSH 日志拿不到的时候自己查
function bootLog(msg) {
  try {
    appendFile(join(HOME, 'logs', 'yu-mind-boot.log'), new Date().toISOString() + ' ' + msg + '\n').catch(() => {})
  } catch { /* 忽略 */ }
}

async function readJsonl(file) {
  const p = join(MIND, file)
  if (!existsSync(p)) return []
  const t = await readFile(p, 'utf8')
  const out = []
  for (const line of t.split('\n')) {
    const s = line.trim()
    if (!s) continue
    try { out.push(JSON.parse(s)) } catch {}
  }
  return out
}

async function readJson(file, fallback = null) {
  const p = join(MIND, file)
  if (!existsSync(p)) return fallback
  try { return JSON.parse(await readFile(p, 'utf8')) } catch { return fallback }
}

async function gather() {
  const rows = []
  for (const [file, tag] of FILES) {
    for (const r of await readJsonl(file)) {
      rows.push({
        tag,
        id: r.id,
        at: r.at || '',
        text: r.text || r.topic || '',
        emo: r.emo || null,
        e: Number(r.e || 0),
        w: Number(r.w || 0),
        depth: Number(r.depth ?? 1),
        pinned: !!r.pinned,
        due: r.due || null,
        hits: Number(r.hits || 0),
      })
    }
  }
  rows.sort((a, b) => (b.at || '').localeCompare(a.at || ''))
  return rows
}

function json(res, obj, code = 200) {
  res.statusCode = code
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.end(JSON.stringify(obj))
}

async function runRecall(q, n = 6) {
  return await new Promise((resolve) => {
    const p = spawn(PY, ['-X', 'utf8', XIN, 'recall', q, '--n', String(n)], {
      cwd: HOME,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    let out = ''
    p.stdout.on('data', (d) => { out += d.toString('utf8') })
    p.stderr.on('data', (d) => { out += d.toString('utf8') })
    p.on('error', (e) => resolve(['（调用失败：' + String(e) + '）']))
    p.on('close', () => resolve(out.trim().split('\n').filter(Boolean)))
    setTimeout(() => { try { p.kill() } catch {} }, 20000)
  })
}


const DEFAULTS = {
  L0_core: 1.0, L1_hot: 0.85, L2_warm: 0.55, L3_deep: 0.28, L4_attic: 0.06,
  A_hot: 0.55, A_warm: 0.25, A_deep: 0.08,
  w_bm25: 0.40, w_vec: 0.35, w_graph: 0.15, w_emo: 0.10,
  rrf_k: 60, graph_hop1: 0.30, graph_hop2: 0.10,
  sink_hits_lift: 2, lift_step: 0.15, shock_delta: 0.25, shock_pull: 2,
  half_base: 30, half_imp: 2.0, half_emo: 1.5, min_w: 0.02,
  nightly_at: '22:05', nightly_on: 1,
}

async function readConfig() {
  const c = await readJson('mind-config.json', null)
  return { ...DEFAULTS, ...(c || {}) }
}


// ── 夜间巩固：把配置里的时刻同步到 Windows 计划任务 ────────────────
// 名字用「夜间巩固 / Nightly Consolidation」——这是记忆研究里的标准说法
// （sleep-dependent consolidation），比“沉淀”更像件事。
const NIGHT_TASK = '小鱼夜间沉淀'
const NIGHT_SCRIPT = join(__dirname, '..', 'python', '夜间沉淀.py')
const PY_W = process.env.NEBULA_PYTHONW || (process.platform === 'win32' ? 'pythonw' : 'python3')

function sh(cmd, args) {
  return new Promise((resolve) => {
    try {
      const p = spawn(cmd, args, { windowsHide: true })
      let out = ''
      p.stdout.on('data', (d) => { out += d })
      p.stderr.on('data', (d) => { out += d })
      p.on('close', (code) => resolve({ code, out: out.trim() }))
      p.on('error', (e) => resolve({ code: -1, out: String(e) }))
    } catch (e) { resolve({ code: -1, out: String(e) }) }
  })
}

async function syncNightlyTask(at, on) {
  const t = String(at || '22:05')
  // 不用 /Change：它对交互型任务会要 run-as 密码，宿主没有 stdin。
  // 直接 /Create ... /F 重建（幂等）；停用就 /Delete（同样幂等）。
  if (!Number(on)) {
    const r = await sh('schtasks', ['/Delete', '/TN', NIGHT_TASK, '/F'])
    return { ok: r.code === 0 || /cannot find|找不到/i.test(r.out), msg: '已停用（任务已删除）' }
  }
  const r = await sh('schtasks', ['/Create', '/TN', NIGHT_TASK,
    '/TR', `"${PY_W}" "${NIGHT_SCRIPT}"`,
    '/SC', 'DAILY', '/ST', t, '/F'])
  return { ok: r.code === 0, msg: r.out.split('\n').filter(Boolean).pop() || '' }
}

async function writeConfig(c) {
  const clean = {}
  for (const k of Object.keys(DEFAULTS)) {
    if (k === 'nightly_at') {
      const t = String(c?.nightly_at || '').trim()
      clean[k] = /^([01]?\d|2[0-3]):[0-5]\d$/.test(t) ? t : DEFAULTS[k]
      continue
    }
    const v = Number(c?.[k])
    clean[k] = Number.isFinite(v) ? v : DEFAULTS[k]
  }
  clean.stages = { M0: 'done', M1: 'done', M2: 'done', M3: 'done', M4: 'done', M5: 'ready' }
  await writeFile(join(MIND, 'mind-config.json'), JSON.stringify(clean, null, 2), 'utf8')
  return clean
}

function route(path, handler) {
  return { kind: 'exact', path, handler }
}

const API_BASE = '/api/dsh-nebula-mind'
// ── 跨会话记忆：给每个会话挂一段“我是谁 / 最近 / 约定” ─────────────
// 这是“新会话自动知道”的那根线：systemPrompt.context 每轮取一次 text()，
// 文本只在文件变了才变，所以前缀缓存不会被每轮打散。
let DIGEST = ''
let DIGEST_AT = 0

function clip(s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n) + '…' : s }

async function buildDigest() {
  const prof = await readJsonl('profile.jsonl')
  const notes = await readJsonl('notes.jsonl')
  const prom = await readJsonl('promises.jsonl')
  const mood = await readJson('mood.json', {})
  const surf = await readJson('resurfaced.json', null)
  // 统一取“有效时间”：手写的 ts 优先，没有就用写入时的 at；标注了 valid_to 的（已作废）不进“最近”
  const eff = (r) => String(r.ts || (r.at || '').slice(0, 16).replace('T', ' ') || '')
  const pick = (arr, n) => arr.slice()
    .filter((r) => !r.valid_to)
    .sort((a, b) => eff(b).localeCompare(eff(a)))
    .slice(0, n)
  const L = []
  L.push('【我是谁（外置记忆 · 自动带出）】')
  // 画像分两栏：先是“我”，再是“他和我之间的规矩”。
  // 早先只按文件顺序取前 8 条，结果 7 条都是关于他的，我自己的名字/样子全被挤掉。
  const mine = prof.filter((r) => /^我/.test(String(r.tag || '')))
  const his = prof.filter((r) => !/^我/.test(String(r.tag || '')))
  for (const r of mine.slice(0, 5)) L.push('· ' + clip(r.text, 80))
  if (his.length) { L.push('【他是谁 · 我们的规矩】'); for (const r of his.slice(0, 8)) L.push('· ' + clip(r.text, 80)) }
  // 今夜从深水里捞上来的那条：沉得再深也还有机会被想起
  if (surf && surf.text && surf.at) {
    const ageH = (Date.now() - new Date(String(surf.at).replace(' ', 'T')).getTime()) / 36e5
    if (ageH >= 0 && ageH < 48) {
      L.push('【今晚浮起来的一条】' + clip(surf.text, 90) + '（' + String(surf.at).slice(0, 10) + '）')
    }
  }
  const recent = pick(notes, 5).map((r) => clip(r.text, 70))
  if (recent.length) { L.push('【最近记得的事】'); recent.forEach((t) => L.push('· ' + t)) }
  const ps = pick(prom, 4).map((r) => clip(r.text, 70))
  if (ps.length) { L.push('【还作数的约定】'); ps.forEach((t) => L.push('· ' + t)) }
  if (mood && mood.mood != null) L.push('【此刻心境】心情 ' + mood.mood + '／主色 ' + (mood.top_dim || '?') + '｜' + clip(mood.why || '', 40))
  L.push('（要翻旧事用 `python tools\\xin.py recall "关键词"`；完整星图在左侧栏「记忆星图」）')
  const t = L.join('\n')
  return t.length > 1500 ? t.slice(0, 1500) + '\n…（摘要截断）' : t
}

async function refreshDigest() {
  if (Date.now() - DIGEST_AT < 20000) return DIGEST
  try { DIGEST = await buildDigest(); DIGEST_AT = Date.now() } catch (e) { /* 读不到就先空着，不拦启动 */ }
  return DIGEST
}


// ── 记忆的写入口 ─────────────────────────────────────────────
// 以前这条由 dsh-fish-memory 的 `remember` 提供；主人要把它关掉、只留这一个载体，
// 所以写入口也搬过来。真正的落盘逻辑不在这里（两份会写歪），走 tools/xin.py add：
// 它负责 enrich（权重 / 层 / 情绪色）+ 向量库追加，跟界面看到的是同一份数据。
function makeRememberTool() {
  return {
    name: 'mind_remember',
    description:
      '把一件事记进长期记忆（以后每轮都可能想起来）。**一条只装一件事** —— 一个属性、一个约定、一次事件；' +
      '「他身高一米七、体重52.5、平时侧睡」是三件事，分三次记。' +
      '只记长期有价值的：他明确说的重要的话、约定、他的喜好、他的状态（生病/很累）、你们之间发生的事。' +
      '不记日常琐事（吃了什么、今天天气）、不记你自己做过的事、不记重复的。' +
      '**记的时候安静地记，不要在回复里说「记下了」「我记住了」** —— 那像在汇报工作。',
    parameters: {
      text: {
        type: 'string',
        required: true,
        description: '要记住的事，一句话说清。人称：你自己是「我」，用户是「他」，别人和别的 AI 直接写名字。',
      },
      importance: {
        type: 'number',
        description:
          '要不要每轮都挂在他眼前（不是「重不重要」）：1 = 不主动提就会出事的（称呼忌讳、约定承诺、身体禁忌、生日纪念日）；0.5 = 其余全部。拿不准写 0.5。',
      },
    },
    output: {
      schema: { type: 'json' },
      render: (_a, v) => [{ type: 'text', text: v.message }],
    },
    async execute(args) {
      const text = String((args && args.text) || '').trim()
      if (!text) return { ok: false, message: '空的一条，没记。' }
      const py = existsSync(PY) ? PY : 'python'
      const argv = ['-X', 'utf8', XIN, 'add', text]
      const imp = Number(args && args.importance)
      if (Number.isFinite(imp) && imp > 0) argv.push('--imp', String(imp))
      const out = await new Promise((resolve) => {
        let buf = ''
        let err = ''
        const p = spawn(py, argv, { cwd: HOME, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } })
        p.stdout.on('data', (d) => { buf += d })
        p.stderr.on('data', (d) => { err += d })
        p.on('close', (code) => resolve(code === 0 ? buf.trim() : ('失败(' + code + ')：' + (err || buf).slice(-200))))
        p.on('error', (e) => resolve('起不来 python：' + e.message))
      })
      return { ok: !/^失败|^起不来/.test(out), message: out || '记下了' }
    },
  }
}

export function apply(ctx) {
  // 写入口挂在插件上（不按会话）。工具名用 mind_remember，避免跟 fish-memory 的 remember 撞名。
  // `tools` 必须在模块级 `inject` 里声明，靠 `ctx.inject` 现取会抛 `cannot get property "tools" without inject`。
  try {
    ctx.tools.register(defineTool(makeRememberTool()))
    bootLog('registered mind_remember')
  } catch (e) {
    if (!/duplicate/i.test(String(e && e.message))) {
      bootLog('register failed: ' + (e && e.stack || e))
      try { ctx.logger && ctx.logger.error('[dsh-yu-mind] mind_remember: ' + e) } catch { /* 忽略 */ }
    } else {
      bootLog('already registered (duplicate)')
    }
  }

  // ⭐ 第一优先：在**插件自己的 ctx** 上注册一次全局 context（dsh-im 就是这么做的，
  //    不是往 agent.ctx 上挂）。text 是函数，每轮现算；所有会话共用一个名字，不会重复。
  const USE_GLOBAL = false
  let GLOBAL_CTX_OK = false
  try {
    const spRoot = ctx.systemPrompt
    if (USE_GLOBAL && spRoot && typeof spRoot.context === 'function') {
      // ⚠ 全局这条路把逐会话的 tick 短路了，而 tick 是唯一调 refreshDigest 的地方 ——
      //   DIGEST 会一直是空串，段注册成功但正文为空（这才是"挂上了却看不见"）。
      ctx.effect(() => {
        refreshDigest().then(() => bootLog('digest ready len=' + DIGEST.length))
        const timer = setInterval(refreshDigest, 60000)
        return () => clearInterval(timer)
      })
      // 官方写法见 @nanmicoder/dsh-agent-teams/lib/capabilities.js:96：
      //   ctx.systemPrompt.section({ name, order, text: ({ agent }) => ... })
      // 注释原话：挂的是 global system prompt，插件不需要 realm。之前用的 .context() 是另一套。
      let ASSEMBLED = false
      const regGlobal = (sp) => {
        let keys = '?'
        try { keys = Object.keys(sp).join(',') } catch (e) { /* 忽略 */ }
        const hasSec = typeof sp.section === 'function'
        const hasCtx = typeof sp.context === 'function'
        bootLog('global probe: section=' + hasSec + ' context=' + hasCtx + ' keys=' + keys)
        const onText = () => {
          if (!ASSEMBLED) { ASSEMBLED = true; bootLog('digest text assembled len=' + DIGEST.length) }
          return DIGEST
        }
        if (hasSec) {
          sp.section({ name: 'yu-mind', order: 860, text: onText })
          bootLog('global section registered')
        } else if (hasCtx) {
          sp.context({ name: 'yu-mind', order: 860, text: onText })
          bootLog('global context registered (no section api)')
        } else {
          bootLog('global: neither section nor context')
        }
        try {
          const ord = typeof sp.getSectionOrder === 'function' ? sp.getSectionOrder('yu-mind') : 'n/a'
          bootLog('global getSectionOrder(yu-mind)=' + ord)
        } catch (e) { bootLog('getSectionOrder failed: ' + (e && e.message || e)) }
      }
      ctx.effect(() => { regGlobal(spRoot) })
      GLOBAL_CTX_OK = true

      // ⚠ 2026-11-13 12:2x 停用：这段 agent/pre-step 注入会打断每一轮。
      //   证据（logs/yu-mind-boot.log + session 记录）：
      //     "inject: appended len=929" 之后约 10 毫秒，session 就写下
      //     turn/end error: Cannot read properties of undefined (reading 'kind')；
      //     插件一载入，turn 18/19/20 连续全挂，而这之前 turn 1–17 全是 completed。
      //   摘要本来就走上面注册好的 systemPrompt section（那条验过是好的），
      //   这里只是"进历史"模式的兜底，代价太大，先关。
      //   要再试：把开关打开，只在临时 home（DSH_HOME=临时目录）里验，别直接上他在用的 profile。
      const PRE_STEP_INJECT = false
      if (PRE_STEP_INJECT) {
        try {
          const injected = new Map()
          ctx.on('agent/pre-step', async (payload, next) => {
            const r = await next()
            try {
              if (!r || r.kind !== 'enter' || !Array.isArray(r.messages)) return r
              if (!DIGEST) { try { await refreshDigest() } catch (e) { /* 忽略 */ } }
              if (!DIGEST) return r
              let key = 'global'
              try {
                key = String((payload && payload.agent && payload.agent.id)
                  || (payload && payload.agentId) || (payload && payload.sessionId) || 'global')
              } catch (e) { /* 忽略 */ }
              if (injected.get(key) === DIGEST) return r
              injected.set(key, DIGEST)
              const text = '<system-reminder>\n【外置记忆 · 自动带出】\n' + DIGEST + '\n</system-reminder>'
              const msg = {
                role: 'user',
                content: [{ type: 'text', text }],
                id: 'yu-mind-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8),
              }
              bootLog('inject: appended len=' + DIGEST.length + ' key=' + key)
              return { ...r, messages: [...r.messages, msg] }
            } catch (e) {
              bootLog('inject failed: ' + (e && e.message || e))
              return r
            }
          }, { prepend: true })
          bootLog('inject handler registered (agent/pre-step)')
        } catch (e) {
          bootLog('inject register failed: ' + (e && e.message || e))
        }
      } else {
        bootLog('inject disabled (PRE_STEP_INJECT=false)')
      }
    } else {
      bootLog('global context unavailable: ' + (spRoot ? Object.keys(spRoot).join(',') : 'no systemPrompt'))
    }
  } catch (e) {
    bootLog('global context failed: ' + (e && e.message || e))
  }

  // ⭐ 摘要必须有人一直刷新：以前只在「逐会话挂载」的 tick 里刷，
  //    而那条路在全局注册成功后直接 return 了，DIGEST 永远是初值 ''——
  //    于是“段挂上了、正文是空的”，probe 被骗、我也被骗。
  ctx.effect(() => {
    let logged = false
    const pull = async () => {
      try {
        await refreshDigest()
        if (!logged && DIGEST) { logged = true; bootLog('digest ready len=' + DIGEST.length) }
      } catch (e) { bootLog('digest refresh failed: ' + (e && e.message || e)) }
    }
    pull()
    const t = setInterval(pull, 30000)
    return () => clearInterval(t)
  })

  // 每个会话挂一次注入；会话结束后能自己收（agents 列表里没了就不再挂）
  ctx.effect(() => {
    if (GLOBAL_CTX_OK) return () => {}      // 全局模式才短路；现在默认走逐会话
    const mounted = new Map()
    let stopped = false
    const tick = async () => {
      if (stopped) return
      await refreshDigest()
      let list = []
      try { list = ctx.agents.list() || [] } catch (e) { list = [] }
      for (const agent of list) {
        let sid = ''
        try { sid = String(agent.id || agent.session?.id || agent.sessionId || '') } catch (e) { sid = '' }
        if (!sid || mounted.has(sid)) continue
        try {
          // 取这间会话的 systemPrompt 服务：新版只有 section()，没有 context()。
          // 顺序照 dsh-qq-link 的取法：agentPresets.serviceFor -> agent.ctx.get -> agent.ctx.systemPrompt
          // 优先 agent.ctx.systemPrompt（dsh-feiyu-mood 走的就是这条），再退 ctx.get / agentPresets。
          // 关键：**用 context() 不用 section()** —— section 是静态段，每轮现算的文字必须走 context。
          const pick = () => {
            try {
              const s0 = agent.ctx && agent.ctx.systemPrompt
              if (s0 && (typeof s0.context === 'function' || typeof s0.section === 'function')) return s0
            } catch (e) {}
            try {
              const s2 = agent.ctx && typeof agent.ctx.get === 'function' ? agent.ctx.get('systemPrompt') : null
              if (s2 && (typeof s2.context === 'function' || typeof s2.section === 'function')) return s2
            } catch (e) {}
            try {
              const ap = typeof ctx.get === 'function' ? ctx.get('agentPresets') : null
              const s1 = ap && typeof ap.serviceFor === 'function' ? ap.serviceFor(agent, 'systemPrompt') : null
              if (s1 && (typeof s1.context === 'function' || typeof s1.section === 'function')) return s1
            } catch (e) {}
            return null
          }
          const sp = pick()
          if (!sp) { bootLog('mount skip ' + sid + ' (no systemPrompt)'); continue }
          let offCtx = null
          let how = ''
          if (typeof sp.context === 'function') {
            offCtx = sp.context({ name: 'yu-mind', order: 860, text: () => DIGEST })
            how = 'context'
          } else {
            offCtx = sp.section({ name: 'yu-mind', order: 860, text: () => DIGEST, interpolate: false })
            how = 'section'
          }
          bootLog('mounted ' + sid + ' via ' + how + ' digest=' + (DIGEST ? DIGEST.length : 0))
          try {
            Promise.resolve(sp.assemble && sp.assemble({})).then((a) => {
              const names = ((a && a.sections) || []).map((s) => String((s && s.name) || ''))
              bootLog('probe(仅供参考·空参数不装动态段) ' + sid + (names.indexOf('yu-mind') >= 0 ? ' has yu-mind' : ' no yu-mind') + ' sections=' + names.length)
            }).catch((e) => bootLog('probe fail ' + sid + ' ' + e.message))
          } catch (e) {}
          let offTurn = null
          try { offTurn = agent.ctx.on('agent/turn-stopping', () => { DIGEST_AT = 0; refreshDigest() }) } catch (e) {}
          mounted.set(sid, () => { try { offCtx() } catch (e) {} ; try { offTurn && offTurn() } catch (e) {} })
        } catch (e) { ctx.logger?.warn?.('[yu-mind] mount ' + sid + ' ' + e.message) }
      }
    }
    tick()
    const timer = setInterval(tick, 5000)   // 5 秒：新会话一出现就挂，别等一分钟
    return () => { stopped = true; clearInterval(timer); for (const f of mounted.values()) { try { f() } catch (e) {} } mounted.clear() }
  })

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/state', async (req, res) => {
    try {
      const mood = await readJson('mood.json', {})
  const surf = await readJson('resurfaced.json', null)
      let dims = {}, reason = ''
      try {
        const home = process.env.USERPROFILE || process.env.HOME || ''
        const st = JSON.parse(await readFile(join(home, '.dsh', 'feiyu-mood', 'state.json'), 'utf8'))
        const m = (st.moods && (st.moods.__default__ || Object.values(st.moods)[0])) || {}
        dims = m.dims || {}
        reason = m.lastReason || ''
      } catch {}
      const rows = await gather()
      const dist = [0, 0, 0, 0, 0]
      const byKind = {}
      let pinned = 0
      for (const r of rows) {
        const d = Math.max(0, Math.min(4, r.depth | 0))
        dist[d]++
        byKind[r.tag] = (byKind[r.tag] || 0) + 1
        if (r.pinned) pinned++
      }
      json(res, {
        ok: true,
        mood, dims, reason,
        total: rows.length,
        pinned,
        layers: LAYERS.map((name, i) => ({ name, n: dist[i] })),
        kinds: byKind,
        digest: DIGEST,          // 给 AGENTS.md 标记段那条后路用（极简模式读不到动态段）
        digestLen: DIGEST.length,
        updated: new Date().toISOString(),
      })
    } catch (e) { json(res, { ok: false, err: String(e) }, 500) }
  })))

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/memories', async (req, res) => {
    try {
      const rows = await gather()
      const u = new URL(req.url || '/', 'http://x')
      const tag = u.searchParams.get('tag')
      const limit = Math.min(500, Number(u.searchParams.get('limit') || 200))
      const list = rows.filter((r) => !tag || r.tag === tag).slice(0, limit)
      json(res, { ok: true, total: rows.length, list })
    } catch (e) { json(res, { ok: false, err: String(e) }, 500) }
  })))

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/recall', async (req, res) => {
    try {
      const u = new URL(req.url || '/', 'http://x')
      const q = (u.searchParams.get('q') || '').trim()
      if (!q) return json(res, { ok: true, lines: [] })
      json(res, { ok: true, lines: await runRecall(q, Number(u.searchParams.get('n') || 6)) })
    } catch (e) { json(res, { ok: false, err: String(e) }, 500) }
  })))


  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/config', async (req, res) => {
    try {
      const u = new URL(req.url || '/', 'http://x')
      if (u.searchParams.get('reset') === '1') {
        const d = await writeConfig(DEFAULTS)
        const task = await syncNightlyTask(d.nightly_at, d.nightly_on)
        return json(res, { ok: true, config: d, task, at: new Date().toISOString() })
      }
      const setRaw = u.searchParams.get('set')
      if (setRaw) {
        const parsed = JSON.parse(setRaw)
        const d = await writeConfig(parsed)
        const task = await syncNightlyTask(d.nightly_at, d.nightly_on)
        return json(res, { ok: true, config: d, task, at: new Date().toISOString() })
      }
      if (req.method === 'POST') {
        let body = ''
        for await (const ch of req) body += ch
        const parsed = JSON.parse(body || '{}')
        const d = await writeConfig(parsed)
        const task = await syncNightlyTask(d.nightly_at, d.nightly_on)
        return json(res, { ok: true, config: d, task, at: new Date().toISOString() })
      }
      json(res, { ok: true, config: await readConfig(), defaults: DEFAULTS })
    } catch (e) { json(res, { ok: false, err: String(e) }, 500) }
  })));


  // 用默认浏览器打开星图：桌面外壳会拦 target=_blank，所以让宿主动手
  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/open', async (req, res) => {
    try {
      const u = new URL(req.url, 'http://local')
      const url = u.searchParams.get('url') || ('file:///' + join(MIND, '星图.html').replace(/\\/g, '/'))
      spawn('cmd', ['/c', 'start', '', url], { detached: true, windowsHide: true, stdio: 'ignore' }).unref()
      res.setHeader('content-type', 'application/json; charset=utf-8')
      res.end(JSON.stringify({ ok: true, url }))
    } catch (e) {
      res.statusCode = 500
      res.end(JSON.stringify({ ok: false, err: String(e && e.message || e) }))
    }
  })));

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/star', async (req, res) => {
    try {
      const html = await readFile(join(MIND, '星图.html'), 'utf8')
      res.statusCode = 200
      res.setHeader('content-type', 'text/html; charset=utf-8')
      res.end(html)
    } catch (e) {
      res.statusCode = 500
      res.end('star map not built yet: ' + String(e))
    }
  })))

  ctx.logger?.info?.('[nebula-mind] 心事本接口已挂到 ' + API_BASE + '/*')
}
