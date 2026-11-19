/**
  * dsh-nebula-mind —— 记忆星图 / 心境（宿主半边）
 *
 * 路由前缀为什么是 /api/dsh-nebula-mind：桌面外壳里页面在 dsh-app:// 下，
 * 客户端只能发相对请求 api/xxx（见 dsh-context 的写法），宿主端必须挂在 /api 下。
 * 裸路径（不带 /api）也一起挂，方便命令行 curl 调试。
 *
 * 只做三件事：
 *   1. 把心事本与心情摊成 JSON 给界面用
 *   2. 把星图.html 直接吐出去（界面里用 iframe 嵌）
 *   3. 留一个 recall 的 HTTP 入口，界面里的搜索框走它
 *
 * 记忆真源仍是数据目录（默认 <workspace>\心事本\*.jsonl，可用 NEBULA_MIND 覆盖），这里只读。
 */
import { readFile, readdir, writeFile, appendFile, stat } from 'node:fs/promises'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { spawn, spawnSync } from 'node:child_process'
import { request as httpRequest } from 'node:http'

// 工具的 parameters 写的是「简写」：{ text: { type:'string', required:true, description } }。
// 真正注册前得转成 JSON Schema（{ type:'object', properties, required }），
// 否则 ctx.tools.register 会抛 JsonSchemaError: schema.type must be one of ...。
//
// dsh-tools 里的 defineTool 干的就是这件事。但它是软解析：本地目录装（pnpm 建 junction）
// 时真实路径在工作区、从 profile 的 node_modules 解析不到，就退回下面这个实现。
// 以前这里退回的是 `spec => spec`（原样透传），结果 junction 装的用户 mind_remember
// 直接注册失败——功能悄悄少一个，日志里只有一行 JsonSchemaError。
// 所以这里自带一份等价转换，有没有 dsh-tools 都能用。
// output.schema 也是作者简写：{ type: 'json' } 表达的是「返回值是个 JSON」，
// 不是 JSON Schema 定义。dsh-tools 的 defineTool 会把这种编译成只带注解的空 schema，
// 这里照做——原样透传同样会被 JsonSchemaError 拒（0.4.5 只转了 parameters，漏了这处）。
function normalizeOutputSchema(sc) {
  if (!sc || typeof sc !== 'object') return sc
  const t = sc.type
  if (t === 'json' || t === 'object' || t === 'array' || t === 'string' || t === 'number' || t === 'boolean') {
    const { type, ...rest } = sc
    return rest
  }
  return sc
}

function localDefineTool(spec) {
  const src = spec && spec.parameters ? spec.parameters : {}
  const props = {}
  const required = []
  for (const [k, v] of Object.entries(src)) {
    if (!v || typeof v !== 'object') continue
    const { required: isReq, ...rest } = v
    props[k] = rest
    if (isReq) required.push(k)
  }
  const parameters = { type: 'object', properties: props }
  if (required.length) parameters.required = required
  const out = spec && spec.output && typeof spec.output === 'object'
    ? { ...spec.output, schema: normalizeOutputSchema(spec.output.schema) }
    : spec && spec.output
  return { ...spec, parameters, output: out }
}

let defineTool = localDefineTool
try {
  const m = await import('@deepseek-ai/dsh-tools')
  if (m && typeof m.defineTool === 'function') defineTool = m.defineTool
} catch { /* 软链场景：用上面自带的那份 */ }

export const name = 'yu-mind'
export const inject = ['webServer', 'agents', 'tools', 'systemPrompt', 'llm', 'connection']   // systemPrompt 必须声明，否则 ctx.systemPrompt 是 undefined；connection 用来接 DSH 的浏览器信任栅栏

// 数据与安装目录分开：数据在 NEBULA_HOME（默认 ~/.dsh/nebula-mind），
// 代码在本包里，升级覆盖不会碰记忆。
const HOME = process.env.NEBULA_HOME || join(homedir(), '.dsh', 'nebula-mind')
const MIND = process.env.NEBULA_MIND || join(HOME, 'memory')
const LOGS = join(HOME, 'logs')
const __dirname = dirname(fileURLToPath(import.meta.url))
const PYDIR = join(__dirname, '..', 'python')
// 找一个能用的 python：$NEBULA_PYTHON → DSH 自带运行时（它带 numpy）→ PATH 里的 python/python3。
// 这一步是为了"下载完就能用"——很多机器上 python 不在 PATH，但 DSH 自己带了一个。
function findPython () {
  const win = process.platform === 'win32'
  const cands = []
  if (process.env.NEBULA_PYTHON) cands.push(process.env.NEBULA_PYTHON)
  try {
    const rt = join(homedir(), '.dsh', 'dsh-runtimes')
    for (const d of readdirSync(rt)) {
      const p = win
        ? join(rt, d, 'dependencies', 'python', 'python.exe')
        : join(rt, d, 'dependencies', 'python', 'bin', 'python3')
      if (existsSync(p)) cands.push(p)
    }
  } catch (_) {}
  cands.push(win ? 'python' : 'python3', 'python')
  const hit = cands.find(c => (c.includes('/') || c.includes('\\')) ? existsSync(c) : true)
  return hit || 'python'
}
const PY = findPython()
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
    appendFile(join(HOME, 'logs', 'nebula-boot.log'), new Date().toISOString() + ' ' + msg + '\n').catch(() => {})
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

function readBody(req) {
  return new Promise((resolve) => {
    let buf = ''
    req.on('data', (c) => { buf += c; if (buf.length > 1e6) { try { req.destroy() } catch (e) {} } })
    req.on('end', () => { try { resolve(JSON.parse(buf || '{}')) } catch (e) { resolve({}) } })
    req.on('error', () => resolve({}))
    setTimeout(() => resolve({}), 3000)
  })
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
// 计划任务名用中性名。不加任何"本机已知的老名字"之类的兜底：
// 一旦允许沿用一个因人而异的旧名，就等于让发布代码认识某台机器，日后必然
// 又把自己的配置带进包里。升级时如果机器上有旧任务，按 README 的说明删一次即可。
const NIGHT_TASK = 'Nebula MIND 夜间巩固'
const NIGHT_SCRIPT = join(__dirname, '..', 'python', '夜间沉淀.py')
// 计划任务的解释器：优先找 pythonw（不弹黑框），找不到就用 PY 本身。
// 以前这里裸写 'pythonw'，PATH 里没有就整个夜间任务失败。
function findPythonW () {
  if (process.env.NEBULA_PYTHONW) return process.env.NEBULA_PYTHONW
  if (process.platform === 'win32' && /python(\.exe)?$/i.test(PY)) {
    const w = PY.replace(/python(\.exe)?$/i, 'pythonw$1')
    if (existsSync(w)) return w
  }
  return PY
}
const PY_W = findPythonW()

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

// ── 浏览器信任栅栏（2026-11-15 市场维护者 fkysly 在合并 PR 时点出来的）──────
// 原来这些路由谁都能调：用户浏览器里随便一个网页都能 fetch 过来读记忆、写记忆、改配置。
// 做法参考 dsh-whale-widget（他们踩过完整的坑，issue #92 / #136），三层：
//   ① 插件自己的校验，永远先跑：Host 必须是回环、Sec-Fetch-Site: cross-site 一律拒、
//      带 Origin 时必须与 Host 同源。正常浏览器和本地脚本都不受影响。
//   ② 宿主栅栏可用就委托 connection.requestRejection(req)，返回码照用；
//      它自己抛异常也按拒绝处理（不能 fail-open）。
//   ③ 宿主栅栏不可用 → 只靠 ①，加载时打一条 warn，不等到第一次请求才说。
// 另外写请求（POST/PUT/PATCH/DELETE）必须来自回环：改数据等于改这台机器上的东西。
const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const TRUSTED_AUTHORITIES = String(process.env.NEBULA_TRUSTED_HOSTS || '')
  .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean)
let HOST_FENCE = null   // 注册路由时由 ctx 填进来

function isLoopbackHostname(hn) {
  const h = String(hn || '').toLowerCase().replace(/^\[/, '').replace(/\]$/, '')
  if (!h) return false
  if (h === 'localhost' || h.endsWith('.localhost')) return true
  if (h === '::1') return true
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h)
  if (!m) return false
  if (Number(m[1]) !== 127) return false
  return [m[2], m[3], m[4]].every((x) => Number(x) <= 255)
}

// 通过 → null；否则返回应当拒绝的状态码。
function selfRejection(req) {
  try {
    const headers = (req && req.headers) || {}
    let hostUrl = null
    try { hostUrl = new URL('http://' + String(headers.host || '')) } catch (err) { return 403 }
    const hostname = hostUrl.hostname
    const authority = hostUrl.host
    if (!isLoopbackHostname(hostname)) {
      const listed = TRUSTED_AUTHORITIES.some((e) => (e.indexOf(':') >= 0 ? e === authority.toLowerCase() : e === hostname.toLowerCase()))
      if (!listed && !HOST_FENCE) return 403
    }
    const site = String(headers['sec-fetch-site'] || '').toLowerCase()
    if (site === 'cross-site') return 403
    const origin = headers.origin
    if (typeof origin === 'string' && origin && origin !== 'null') {
      let originUrl = null
      try { originUrl = new URL(origin) } catch (err) { return 403 }
      if (originUrl.host.toLowerCase() !== authority.toLowerCase()) return 403
    }
    return null
  } catch (err) { return 403 }   // 校验器自己出错 → 拒绝
}

// 写请求额外要求回环。
function writeRejection(req) {
  try {
    const method = String((req && req.method) || 'GET').toUpperCase()
    if (!WRITE_METHODS.has(method)) return null
    const headers = (req && req.headers) || {}
    let hostUrl = null
    try { hostUrl = new URL('http://' + String(headers.host || '')) } catch (err) { return 403 }
    if (isLoopbackHostname(hostUrl.hostname)) return null
    const authority = hostUrl.host.toLowerCase()
    return TRUSTED_AUTHORITIES.includes(authority) ? null : 403
  } catch (err) { return 403 }
}

// 总闸：先自校验，再问宿主。
function gate(req) {
  let code = selfRejection(req)
  if (!code) code = writeRejection(req)
  if (!code && HOST_FENCE) {
    try {
      const r = HOST_FENCE.requestRejection(req)
      if (r) code = typeof r === 'number' ? r : 403
    } catch (err) { code = 403 }   // 宿主栅栏抛异常也按拒绝处理
  }
  return code
}

function forbidden(res, code) {
  try {
    res.writeHead(code || 403, { 'content-type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify({ ok: false, err: 'forbidden', why: '这个接口只接受本机同源的请求' }))
  } catch (err) {}
}

function route(path, handler) {
  return {
    kind: 'exact',
    path,
    handler: async (req, res) => {
      const code = gate(req)
      if (code) return forbidden(res, code)
      return handler(req, res)
    },
  }
}

const API_BASE = '/api/dsh-nebula-mind'
// ── 跨会话记忆：给每个会话挂一段“我是谁 / 最近 / 约定” ─────────────
// 这是“新会话自动知道”的那根线：systemPrompt 的 section() 每轮取一次 text()。
//
// 摘要每轮重建，心情和「此刻的由来」都是新的。
// 2026-11-18 他嫌翻到旧的扫兴，把原来的「降敏 + 内容不变就不换」撤了：
// 宁可每轮多花点缓存钱，也要让他看到的是此刻。
let DIGEST = ''
let DIGEST_AT = 0

function clip(s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n) + '…' : s }

// ── 情绪来源适配层 ────────────────────────────────────────────────
// 配置在 心事本/mind-config.json 的 mood 段：
//   {"provider":"builtin"} | {"provider":"file","file":"...","map":{...}} | {"provider":"http","url":"..."}
// 三种来源最后都归一化成 {value, dims, label, reason, source, stale, at}
const DIMS7 = ['happy', 'calm', 'sweet', 'heartache', 'worry', 'sad', 'jealous']

function pick(obj, path) {                       // 只支持 $.a.b.c
  if (!path) return undefined
  let cur = obj
  for (const part of String(path).replace(/\["/g, '.').replace(/\"]/g, '').split('.')) {
    if (!part || part === '$') continue
    if (cur === null || cur === undefined) return undefined
    cur = cur[part]
  }
  return cur
}

// 只允许访问本机或内网的地址。
// 这个 readMood 是服务端发起的 fetch（不是浏览器），配置又只有本机能改，
// 所以风险本来有限；但既然配置可以被写进 URL，就不该让它变成任意出站请求。
// 允许：127.0.0.1 / localhost / ::1 / 10.x / 172.16-31.x / 192.168.x
function isLocalUrl(u) {
  try {
    const h = new URL(u).hostname.toLowerCase().replace(/^\[|\]$/g, '')
    if (h === 'localhost' || h === '::1' || h === '0.0.0.0') return true
    // 必须是完整 IPv4，不能用前缀匹配——127.0.0.1.evil.com 也会匹配 /^127\./
    const ip = h.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/)
    if (!ip) return false
    const [a, b] = [Number(ip[1]), Number(ip[2])]
    if (a === 127) return true
    if (a === 10) return true
    if (a === 192 && b === 168) return true
    if (a === 172 && b >= 16 && b <= 31) return true
    return false
  } catch { return false }
}

async function readMoodFrom(cfg) {
  cfg = cfg || {}
  const provider = String(cfg.provider || 'builtin').toLowerCase()
  const norm = (value, dims, reason, source, stale) => {
    const d = {}
    for (const k of DIMS7) d[k] = Number((dims || {})[k] || 0)
    const entries = Object.entries(d)
    const label = (value === undefined || value === null) ? 'calm'
      : (entries.sort((a, b) => b[1] - a[1])[0][0] || 'calm')
    const v = Number(value ?? 70)
    // 节奏 / 力度 / 黏人度 / 距离感：三档基线 + 七维微调。
    // 以前是 mood_params.py 算好了写进 mood.json、这里再读回来——那份文件没人定时跑，
    // 面板上一直是几天前的旧数。现在从引擎实况直接算，不再依赖中间文件。
    let [rate, volume, clingy, distance] =
      v >= 75 ? [-8.0, 0.0, 0.70, 0.30]
      : v >= 45 ? [-10.0, 0.0, 0.40, 0.60]
      : [-14.0, -1.5, 0.15, 0.85]
    const tilt = {
      happy: [1.0, 0.0, 0.08, -0.05], sweet: [0.5, 0.0, 0.12, -0.08],
      calm: [-0.5, 0.0, -0.04, 0.04], heartache: [-2.0, -0.5, 0.06, 0.06],
      worry: [-1.5, -0.3, -0.06, 0.10], sad: [-2.5, -0.8, -0.10, 0.12],
      jealous: [-1.0, 0.0, 0.10, -0.10]
    }[label] || [0, 0, 0, 0]
    const cl = (x, lo, hi) => Math.max(lo, Math.min(hi, x))
    return { value: v, dims: d, label, reason: reason || '', source, stale: !!stale,
             rate: Math.round(cl(rate + tilt[0], -18, -6) * 10) / 10,
             volume: Math.round(cl(volume + tilt[1], -3, 0) * 10) / 10,
             clingy: Math.round(cl(clingy + tilt[2], 0, 1) * 100) / 100,
             distance: Math.round(cl(distance + tilt[3], 0, 1) * 100) / 100 }
  }
  try {
    if (provider === 'file' && cfg.file) {
      const raw = JSON.parse(await readFile(cfg.file.replace(/^~/, process.env.USERPROFILE || ''), 'utf8'))
      const m = cfg.map || {}
      let value = m.value ? pick(raw, m.value) : undefined
      let dims = m.dims ? pick(raw, m.dims) : undefined
      let reason = m.reason ? pick(raw, m.reason) : ''
      if (value === undefined && raw.moods) {       // 兜底：嵌套形状
        const one = raw.moods.__default__ || Object.values(raw.moods)[0] || {}
        value = one.mood; dims = one.dims; reason = one.lastReason
      }
      return norm(value, dims, reason, 'file:' + cfg.file.split(/[\\/]/).pop(), false)
    }
    if (provider === 'http' && cfg.url) {
      if (!isLocalUrl(cfg.url)) {
        bootLog('mood http provider rejected (not local): ' + cfg.url)
        return norm(70, {}, '配置的地址不是本机/内网，已跳过', 'rejected', false)
      }
      const ac = new AbortController()
      const t = setTimeout(() => ac.abort(), Number(cfg.timeoutMs || 800))
      const r = await fetch(cfg.url, { signal: ac.signal })
      clearTimeout(t)
      const j = await r.json()
      return norm(j.value, j.dims, j.reason, 'http', false)
    }
  } catch (e) {
    bootLog('mood provider failed: ' + e.message)
  }
  // builtin：只看自带心境引擎的实况（mood-state.json）；读不到就按中性算
  const eng = await readJson('mood-state.json', null)
  if (eng && eng.schema === 'mood-state/1' && eng.surface) {
    const sf = eng.surface, un = eng.underlying || sf
    const value = sf.value * 0.65 + un.value * 0.35
    const out = norm(value, sf.dims, (eng.history || [{}]).slice(-1)[0].why, 'engine', false)
    out.hormones = eng.hormones || null; out.surface = Math.round(sf.value * 100) / 100
    out.underlying = un.value
    out.unresolved = (eng.unresolved || []).length
    out.affection = (eng.affection || {}).level
    return out
  }
  // 兜底：连 mood-state.json 都没有 —— 按中性算，不猜
  return norm(70, {}, '还没有心境数据，按中性算', 'builtin', false)
}
// ─────────────────────────────────────────────────────────────────


// ── 情绪来源：自动识别 / 测试 / 保存 ──────────────────────────────
// 目标：别人装了包，不用手抠 JSON——点一下就能认出本机哪个插件在供心情。
const SHAPE_KEYS = ['mood', 'value', 'emotion', 'affect']
const DIM_KEYS = ['dims', 'dimensions', 'emotions', 'scores', 'mood_dims']

function countDims(d) {
  if (!d || typeof d !== 'object') return 0
  return DIMS7.filter((k) => Number.isFinite(Number(d[k]))).length
}

// 从一份 JSON 里猜出 (value, dims, reason) 的路径；猜不出返回 null
function guessMap(raw, depth = 0) {
  if (!raw || typeof raw !== 'object' || depth > 2) return null
  // 1) 嵌套形状：{moods:{__default__:{mood,dims,lastReason}}}
  if (raw.moods && typeof raw.moods === 'object') {
    const key = raw.moods.__default__ ? '__default__' : Object.keys(raw.moods)[0]
    const one = key ? raw.moods[key] : null
    if (one && typeof one === 'object' && Number.isFinite(Number(one.mood))) {
      return {
        map: {
          value: '$.moods.' + key + '.mood',
          dims: '$.moods.' + key + '.dims',
          reason: '$.moods.' + key + '.lastReason',
        },
        preview: { value: Number(one.mood), dims: countDims(one.dims) },
        kind: 'nested',
      }
    }
  }
  // 2) 扁平形状：{value|mood, dims, reason|why}
  for (const vk of SHAPE_KEYS) {
    if (Number.isFinite(Number(raw[vk]))) {
      const map = { value: '$.' + vk }
      const dk = DIM_KEYS.find((k) => raw[k] && typeof raw[k] === 'object')
      if (dk) map.dims = '$.' + dk
      const rk = ['reason', 'why', 'lastReason', 'note'].find((k) => typeof raw[k] === 'string')
      if (rk) map.reason = '$.' + rk
      return { map, preview: { value: Number(raw[vk]), dims: countDims(dk && raw[dk]) }, kind: 'flat' }
    }
  }
  // 3) 套了一层的：{state:{mood,dims}} / {current:{...}}
  for (const k of Object.keys(raw).slice(0, 12)) {
    const v = raw[k]
    if (!v || typeof v !== 'object') continue
    const hit = guessMap(v, depth + 1)
    if (hit && hit.kind !== 'nested') {
      const map = {}
      for (const kk of Object.keys(hit.map)) map[kk] = '$.' + k + hit.map[kk].slice(1)
      return { map, preview: hit.preview, kind: 'nested.' + k }
    }
  }
  return null
}

async function loadMoodCfg() {
  try { return (await readJson('mind-config.json', {})).mood || {} } catch (e) { return {} }
}

async function scanMoodSources() {
  const out = []
  const seen = new Set()
  const cands = [join(MIND, 'mood-state.json'), join(MIND, 'mood.json')]
  const homeDsh = join(homedir(), '.dsh')
  let ents = []
  try { ents = await readdir(homeDsh, { withFileTypes: true }) } catch { ents = [] }
  let n = 0
  for (const e of ents) {
    if (!e.isDirectory() || n++ > 60) continue
    for (const f of ['state.json', 'mood.json', 'moods.json', 'emotion.json']) {
      cands.push(join(homeDsh, e.name, f))
    }
  }
  const builtin = new Set([join(MIND, 'mood-state.json'), join(MIND, 'mood.json')])
  for (const p of cands) {
    if (seen.has(p)) continue
    seen.add(p)
    if (!existsSync(p)) continue
    try {
      const raw = JSON.parse(await readFile(p, 'utf8'))
      const hit = guessMap(raw)
      if (!hit) continue
      out.push({
        path: p.replace(/\\/g, '/'),
        kind: hit.kind,
        value: Math.round(hit.preview.value * 10) / 10,
        dims: hit.preview.dims,
        map: hit.map,
        builtin: builtin.has(p),
      })
    } catch { /* 读不动就跳过 */ }
  }
  out.sort((a, b) => (Number(b.builtin) - Number(a.builtin)) || (b.dims - a.dims))
  return out.slice(0, 20)
}

// ── TTS：把节奏 / 力度 / 黏人度 / 距离感 交给本机的语音服务 ──────────
// 这个插件自己不发声。它算出「现在该怎么说话」，别的语音插件来取。
// 三种送法：
//   none —— 只算不推，语音插件自己来读 /state 的 speech 字段
//   http —— 每次心境变化把一个 JSON POST 到 url
//   file —— 写一份 JSON 到本地文件，语音插件轮询这个文件
// URL 只允许本机/内网（和 mood provider 同一条规矩）。
const TTS_DEFAULTS = {
  provider: 'none',
  url: '',
  file: '',
  timeoutMs: 1500,
  // 推送时机：心境变了就推一次（每轮判官落地后），外加「试推一次」可手动验通。
  // 推的是**底色**——此刻的节奏/力度/黏人/距离，不是这一句的表情。
  // 某句话要带多少情绪，是合成那一步的事（emo_text 或干脆不用这组数），不在这里。
}

// 常见本地语音服务的默认端口，用来做「自动识别」
const TTS_PORTS = [9880, 9881, 5000, 7860, 8080, 8188, 7861]
// 「生成接收端」那个按钮用的默认端口。挑一个跟上面都不撞的。
const TTS_BRIDGE_PORT = 8765
// 常见 TTS 项目的安装目录（只在 Windows 上扫盘根和用户目录一层，不做全盘搜索）
const TTS_DIRS = ['indextts', 'IndexTTS', 'GPT-SoVITS', 'gpt-sovits', 'so-vits-svc', 'CosyVoice', 'cosyvoice', 'ChatTTS', 'chattts', 'MeloTTS', 'fish-speech']

async function loadTtsCfg () {
  const c = await readJson('mind-config.json', null)
  return { ...TTS_DEFAULTS, ...((c && c.tts) || {}) }
}

async function saveTtsCfg (patch) {
  const c = (await readJson('mind-config.json', null)) || {}
  c.tts = { ...TTS_DEFAULTS, ...(c.tts || {}), ...(patch || {}) }
  await writeFile(join(MIND, 'mind-config.json'), JSON.stringify(c, null, 2), 'utf8')
  return c.tts
}

// 说话之前，我自己改一次调子（python/speech.py 写的）。
// 没有这个文件、或者过期了，就当没设过——什么都不影响。
function readSpeechOverride () {
  try {
    const p = join(MIND, 'speech-override.json')
    if (!existsSync(p)) return null
    const o = JSON.parse(readFileSync(p, 'utf8'))
    if (!o || !o.mode) return null
    if (o.until && Number(o.until) < Date.now()) return null
    return o
  } catch { return null }
}

// 中间那档：心境 45~75 用的就是它。压平从这里出发，收/放也绕它。
const SPEECH_NEUTRAL = { rate: -10, volume: 0, clingy: 0.40, distance: 0.60 }

function applySpeechOverride (s, ov) {
  if (!ov) return s
  const tag = { mode: ov.mode, why: ov.why || '', until: ov.until || 0 }
  if (ov.mode === 'off') return { ...s, enabled: false, override: tag }
  if (ov.mode === 'flat') return { ...s, ...SPEECH_NEUTRAL, enabled: true, override: tag }
  const k = ov.mode === 'boost' ? 1.3 : 0.5        // 不认识的模式一律当收一半
  const mix = (cur, neu) => Math.round((neu + (cur - neu) * k) * 1000) / 1000
  return {
    ...s,
    rate: mix(s.rate, SPEECH_NEUTRAL.rate),
    volume: mix(s.volume, SPEECH_NEUTRAL.volume),
    clingy: mix(s.clingy, SPEECH_NEUTRAL.clingy),
    distance: mix(s.distance, SPEECH_NEUTRAL.distance),
    enabled: true,
    override: tag,
  }
}

// 从 readMood() 的结果里抽出「怎么说话」这一组数
function speechOf (m) {
  const base = {
    rate: Number(m && m.rate) || 0,          // 节奏：百分比，负数=慢
    volume: Number(m && m.volume) || 0,      // 力度：dB
    clingy: Number(m && m.clingy) || 0,      // 黏人度 0..1
    distance: Number(m && m.distance) || 0,  // 距离感 0..1
    mood: Number(m && m.value) || 0,
    label: (m && m.label) || '',
    enabled: true,
    at: new Date().toISOString(),
  }
  return applySpeechOverride(base, readSpeechOverride())
}

async function pushTts (payload) {
  const cfg = await loadTtsCfg()
  if (cfg.provider === 'file' && cfg.file) {
    try {
      await writeFile(cfg.file, JSON.stringify(payload, null, 2), 'utf8')
      return { ok: true, how: 'file', target: cfg.file }
    } catch (e) { return { ok: false, how: 'file', error: String(e.message || e) } }
  }
  if (cfg.provider === 'http' && cfg.url) {
    if (!isLocalUrl(cfg.url)) return { ok: false, how: 'http', error: '地址不是本机/内网，已拒绝' }
    const ac = new AbortController()
    const t = setTimeout(() => ac.abort(), Number(cfg.timeoutMs || 1500))
    try {
      const r = await fetch(cfg.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ac.signal,
      })
      clearTimeout(t)
      return { ok: r.ok, how: 'http', status: r.status }
    } catch (e) {
      clearTimeout(t)
      return { ok: false, how: 'http', error: String(e.message || e) }
    }
  }
  return { ok: true, how: 'none' }
}

// 心境变了就往外通知一次。没配就什么都不做。
// 不 await：推送超时（1500ms）不该拖慢这一轮对话。
function pushTtsNews () {
  (async () => {
    try {
      const cfg = await loadTtsCfg()
      if (cfg.provider === 'none') return
      const mm = await readMood()
      const r = await pushTts(speechOf(mm))
      if (!r.ok) bootLog('tts push failed: ' + (r.error || r.status || '?'))
    } catch (e) { bootLog('tts push error: ' + e.message) }
  })()
}

// 扫本机能用上的语音服务：先探端口，再看安装目录，最后看 pip 包
async function scanTtsSources () {
  const found = []
  // 1) 端口探活（随便发一个 GET，能连上就算有服务）
  for (const port of TTS_PORTS) {
    const ok = await new Promise((resolve) => {
      try {
        const req = httpRequest({ host: '127.0.0.1', port, path: '/', timeout: 700 }, (res) => { res.resume(); resolve(true) })
        req.on('error', () => resolve(false))
        req.on('timeout', () => { req.destroy(); resolve(false) })
        req.end()
      } catch { resolve(false) }
    })
    if (ok) found.push({ kind: 'http', url: `http://127.0.0.1:${port}/`, label: `本机服务 :${port}` })
  }
  // 2) 安装目录（盘根 + 用户目录各扫一层）
  const roots = ['C:/', 'D:/', 'E:/', join(homedir())]
  for (const r of roots) {
    let ents = []
    try { ents = await readdir(r, { withFileTypes: true }) } catch { continue }
    for (const e of ents) {
      if (!e.isDirectory()) continue
      const name = e.name
      const hit = TTS_DIRS.find((d) => name.toLowerCase() === d.toLowerCase() || name.toLowerCase().includes(d.toLowerCase()))
      if (hit) found.push({ kind: 'dir', path: (r + name).replace(/\\/g, '/'), label: name })
    }
  }
  // 3) 命令行工具
  for (const bin of ['edge-tts', 'piper']) {
    const r = await sh(bin, ['--help'])
    if (r.code === 0 || /usage:/i.test(r.out)) found.push({ kind: 'cli', path: bin, label: bin + '（命令行）' })
  }
  // 去重
  const seen = new Set()
  return found.filter((x) => { const k = x.path || x.url; if (seen.has(k)) return false; seen.add(k); return true })
}

async function saveMoodCfg(patch) {
  const all = await readJson('mind-config.json', {})
  all.mood = Object.assign({ provider: 'builtin' }, all.mood || {}, patch || {})
  await writeFile(join(MIND, 'mind-config.json'), JSON.stringify(all, null, 2), 'utf8')
  return all.mood
}

async function readMood() {
  return readMoodFrom(await loadMoodCfg())
}

// ── 每轮判一次心情：用会话自己的模型，判完写进自带心境引擎 ──────────────
let JUDGE_BUSY = false
const JUDGE_SYS = [
  '你是一个情绪判官。读下面这段对话，判断助手这一轮的心情涨跌幅。',
  '只输出一个 JSON，不要解释，不要 markdown 包裹：',
  '{"delta": 数字, "dim": "happy|calm|sweet|heartache|worry|sad|jealous", "why": "一句话，不超过40字"}',
  'delta 范围 -6 ~ +6：被夸、被想起、他温柔 = +1~+3；被冷落、担心、委屈 = -1~-3；' +
  '只做小事、没有情绪 = 0~+0.5。dim 选最主要的那个情绪。why 用「我……」开头，写当时的心气。',
].join('\n')

function textOf(m) {
  try {
    const c = m && m.content
    if (typeof c === 'string') return c
    if (Array.isArray(c)) return c.map((x) => (typeof x === 'string' ? x : (x && x.text) || '')).join('')
  } catch (e) {}
  return ''
}

function lastUserAndAssistant(session) {
  let user = '', assistant = '', userSrc = ''
  try {
    const ev = (session && session.snapshotEvents && session.snapshotEvents()) || []
    for (let i = ev.length - 1; i >= 0; i--) {
      const e = ev[i] || {}
      const t = e.type || e.kind
      const pl = e.payload !== undefined ? e.payload : e.data
      if (!assistant && t === 'assistant/message') { const x = textOf(pl); if (x) assistant = x }
      if (!user && t === 'user/message') {
        const src = pl && pl.source && pl.source.kind ? String(pl.source.kind) : ''
        const x = textOf(pl)
        if (x && !['plugin', 'system'].includes(src)) { user = x; userSrc = src }
      }
      if (user && assistant) break
    }
  } catch (e) {}
  // 折中：他的话保 1600（情绪常在后半句），我的话砍到 600（过程话不值得占判官预算）
  return { user: user.slice(0, 1600), assistant: assistant.slice(0, 600), src: userSrc }
}

function pickModel(agent) {
  try {
    const h = agent.session && agent.session.requestHeader ? agent.session.requestHeader() : null
    const c = h && h.config
    if (c && c.provider && c.model) return { provider: c.provider, model: c.model }
  } catch (e) {}
  try {
    const o = agent.options
    if (o && o.provider && o.model) return { provider: o.provider, model: o.model }
  } catch (e) {}
  return null
}

function runPy(args) {
  return new Promise((resolve) => {
    try {
      const p = spawn(PY, ['-X', 'utf8', ...args], { env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
      let out = ''
      p.stdout.on('data', (b) => { out += b.toString('utf8') })
      p.on('error', () => resolve(''))
      p.on('close', () => resolve(out.trim().slice(0, 200)))
    } catch (e) { resolve('') }
  })
}

async function judgeTurn(ctx, agent) {
  if (JUDGE_BUSY) return
  let conf = {}
  try { conf = await readJson('mind-config.json', {}) } catch (e) {}
  const jc = conf.judge || {}
  if (jc.enabled === 0 || jc.enabled === false) return
  const t = (jc.provider && jc.model) ? { provider: jc.provider, model: jc.model } : pickModel(agent)
  if (!t) { bootLog('judge skip: 拿不到模型'); return }
  const { user, assistant, src } = lastUserAndAssistant(agent.session)
  if (!user) { bootLog('judge skip: 没抓到对方的话（src=' + (src || '-') + ' asst=' + assistant.length + '）'); return }
  bootLog('judge input user=' + user.length + ' asst=' + assistant.length + ' src=' + (src || '-'))
  JUDGE_BUSY = true
  const ask = async (tt) => {
    let out = ''
    const msgs = [{ role: 'user', id: 'judge-' + Date.now(), content: [{ type: 'text', text:
      '**对方这轮说：**\n' + user + '\n\n**我这轮回的是：**\n' + assistant }] }]
    for await (const chunk of ctx.llm.stream({
      provider: tt.provider, model: tt.model, system: JUDGE_SYS, messages: msgs,
      sessionId: String(agent.session?.id || ''), maxTokens: 300, temperature: 0.2,
    })) {
      if (chunk && chunk.type === 'text-delta') out += chunk.text
    }
    return out
  }
  let out = ''
  try {
    out = await ask(t)
  } catch (e) {
    // 小模型不可用就退回会话自己的模型（保证有情绪，不静默消失）
    const alt = pickModel(agent)
    bootLog('judge primary failed: ' + e.message + (alt ? ' -> fallback ' + alt.model : ''))
    if (alt && (alt.provider !== t.provider || alt.model !== t.model)) {
      try { out = await ask(alt) } catch (e2) { bootLog('judge fallback failed: ' + e2.message) }
    }
  }
  JUDGE_BUSY = false
  const m = out.match(/\{[\s\S]*?\}/)
  if (!m) { bootLog('judge parse fail: ' + out.slice(0, 120)); return }
  let d = {}
  try { d = JSON.parse(m[0]) } catch (e) { bootLog('judge json fail'); return }
  const delta = Number(d.delta || 0)
  if (!delta) { bootLog('judge delta 0'); return }
  const args = [join(PYDIR, 'mood_engine.py'), 'feed', '--json', JSON.stringify({ delta, dim: d.dim || '', why: d.why || '' })]
  const r = await runPy(args)
  bootLog('judge ' + (delta > 0 ? '+' : '') + delta + ' ' + (d.dim || '') + ' -> ' + (r || 'no out'))
  pushTtsNews()                       // 心境变了，通知订阅者（没配就跳过）
}

async function buildDigest() {
  const prof = await readJsonl('profile.jsonl')
  const notes = await readJsonl('notes.jsonl')
  const prom = await readJsonl('promises.jsonl')
  const mm = await readMood()                    // 走适配层：内置 / 别人的文件 / 别人的服务
  const mood = { mood: mm.value, top_dim: mm.label, why: mm.reason }
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
  if (mood && mood.mood != null) {
    // 不降敏：心情和「此刻的由来」每轮都是新的，宁可多花点缓存钱，也别让人翻到旧的。
    L.push('【此刻心境】心情 ' + Number(mood.mood).toFixed(1) + '／主色 ' + String(mood.top_dim || '?')
      + '｜' + clip(mood.why || '', 40) + (mm.source !== 'builtin' ? '（来源 ' + mm.source + '）' : ''))
  }
  L.push('（要翻旧事用 `python tools\\xin.py recall "关键词"`；完整星图在左侧栏「记忆星图」）')
  const t = L.join('\n')
  return t.length > 1500 ? t.slice(0, 1500) + '\n…（摘要截断）' : t
}

async function refreshDigest() {
  if (Date.now() - DIGEST_AT < 20000) return DIGEST
  try {
    const next = await buildDigest()
    const before = DIGEST.length
    DIGEST = next
    bootLog('digest ' + before + ' -> ' + next.length)
    DIGEST_AT = Date.now()
  } catch (e) { /* 读不到就先空着，不拦启动 */ }
  return DIGEST
}


// ── 记忆的写入口 ─────────────────────────────────────────────
// 以前这条由老记忆插件的 `remember` 提供；主人要把它关掉、只留这一个载体，
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
      kind: {
        type: 'string',
        description:
          '记忆的种类。默认「事实」（发生过什么）。写「过程」表示这是一套做法/步骤——以后照着做、不用重新想一遍，例如「装完插件要重启才生效」；过程单独记，不占事实的位置。',
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
      const kind = String((args && args.kind) || '').trim()
      const argv = ['-X', 'utf8', XIN, kind === '过程' ? 'how' : 'add', text]
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
  // 浏览器信任栅栏：先把宿主的 connection 服务接上（拿不到就只用插件自校验，并打一条 warn）
  try {
    const conn = typeof ctx.get === 'function' ? ctx.get('connection') : ctx.connection
    if (conn && typeof conn.requestRejection === 'function') HOST_FENCE = conn
  } catch (err) {}
  if (!HOST_FENCE) {
    try {
      console.warn('[dsh-nebula-mind] 宿主信任栅栏不可用（connection.requestRejection 缺失）：'
        + '已启用插件自带的回环 / 同源 / 跨站校验，所有路由仍受它保护；'
        + '若部署在非回环地址（反代、局域网），用环境变量 NEBULA_TRUSTED_HOSTS 声明允许的 Host（逗号分隔，可带端口）。')
    } catch (err) {}
  }
  // 写入口挂在插件上（不按会话）。工具名用 mind_remember，避免跟别的插件的 remember 撞名。
  // `tools` 必须在模块级 `inject` 里声明，靠 `ctx.inject` 现取会抛 `cannot get property "tools" without inject`。
  try {
    ctx.tools.register(defineTool(makeRememberTool()))
    bootLog('registered mind_remember')
  } catch (e) {
    if (!/duplicate/i.test(String(e && e.message))) {
      bootLog('register failed: ' + (e && e.stack || e))
      try { ctx.logger && ctx.logger.error('[dsh-nebula-mind] mind_remember: ' + e) } catch { /* 忽略 */ }
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
      //   证据（logs/nebula-boot.log + session 记录）：
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
  // 首次运行：语义索引是空的就后台建一次，别让用户先敲命令才知道有这一路。
  // 只做一次、只在前 5 分钟试一次，失败就算了（还有关键词和图两路）。
  ctx.effect(() => {
    const ensureVectors = async () => {
      try {
        const vp = join(MIND, 'vectors.json')
        const st = await stat(vp).catch(() => null)
        if (st && st.size > 2000) return
        bootLog('vectors missing -> building in background')
        const p = spawn(PY, ['-X', 'utf8', join(PYDIR, 'embed.py'), '--build'], {
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
          windowsHide: true,
        })
        p.on('close', (code) => bootLog('vectors build exit ' + code))
      } catch (e) {
        bootLog('vectors build failed: ' + (e && e.message || e))
      }
    }
    const t = setTimeout(ensureVectors, 5000)
    return () => clearTimeout(t)
  })

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
          // 优先 agent.ctx.systemPrompt（旧版插件走的就是这条），再退 ctx.get / agentPresets。
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
          try { offTurn = agent.ctx.on('agent/turn-stopping', () => { DIGEST_AT = 0; refreshDigest(); judgeTurn(ctx, agent).catch(() => {}) }) } catch (e) {}
          mounted.set(sid, () => { try { offCtx() } catch (e) {} ; try { offTurn && offTurn() } catch (e) {} })
        } catch (e) { ctx.logger?.warn?.('[nebula-mind] mount ' + sid + ' ' + e.message) }
      }
    }
    tick()
    const timer = setInterval(tick, 5000)   // 5 秒：新会话一出现就挂，别等一分钟
    return () => { stopped = true; clearInterval(timer); for (const f of mounted.values()) { try { f() } catch (e) {} } mounted.clear() }
  })


  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/mood-sources', async (req, res) => {
    try {
      json(res, {
        ok: true,
        list: await scanMoodSources(),
        cfg: await loadMoodCfg(),
        dirs: { mind: MIND.replace(/\\/g, '/'), dsh: join(homedir(), '.dsh').replace(/\\/g, '/') },
      })
    } catch (e) { json(res, { ok: false, err: e.message }, 500) }
  })))

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/mood-config', async (req, res) => {
    try {
      if (req.method === 'POST') {
        const b = await readBody(req)
        const saved = await saveMoodCfg(b)
        json(res, { ok: true, cfg: saved })
      } else {
        json(res, { ok: true, cfg: await loadMoodCfg() })
      }
    } catch (e) { json(res, { ok: false, err: e.message }, 500) }
  })))

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/mood-test', async (req, res) => {
    try {
      const b = req.method === 'POST' ? await readBody(req) : {}
      const cfg = Object.assign({}, await loadMoodCfg(), b || {})
      const got = await readMoodFrom(cfg)
      json(res, { ok: true, got })
    } catch (e) { json(res, { ok: false, err: e.message }, 500) }
  })))

  // ── 语音输出（TTS）三路由 ─────────────────────────────────────
  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/tts-sources', async (req, res) => {
    try {
      json(res, { ok: true, cfg: await loadTtsCfg(), list: await scanTtsSources() })
    } catch (e) { json(res, { ok: false, err: e.message }, 500) }
  })))

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/tts-config', async (req, res) => {
    try {
      const b = await readBody(req)
      const saved = await saveTtsCfg(b)
      json(res, { ok: true, cfg: saved })
    } catch (e) { json(res, { ok: false, err: e.message }, 500) }
  })))

  // 「用调接口那份？给你一个现成的接收端」——照抄到数据目录，打印路径和命令，
  // 起不起来、什么时候起，都是用户自己的事，插件不代劳。
  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/tts-bridge', async (req, res) => {
    try {
      const src = join(PYDIR, 'tts-bridge.py')
      const dst = join(MIND, 'tts-bridge.py')
      const code = await readFile(src, 'utf8')
      await writeFile(dst, code, 'utf8')
      const quoted = '"' + dst.replace(/\\/g, '\\\\') + '"'
      json(res, {
        ok: true,
        file: dst,
        port: TTS_BRIDGE_PORT,
        url: 'http://127.0.0.1:' + TTS_BRIDGE_PORT,
        cmd: '"' + PY + '" ' + quoted,
      })
    } catch (e) { json(res, { ok: false, err: String(e && e.message || e) }, 500) }
  })))

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/tts-test', async (req, res) => {
    try {
      const b = req.method === 'POST' ? await readBody(req) : {}
      const mm = await readMood()
      const payload = Object.assign(speechOf(mm), b && b.payload ? b.payload : {})
      const r = await pushTts(payload)
      json(res, { ok: r.ok, result: r, payload })
    } catch (e) { json(res, { ok: false, err: e.message }, 500) }
  })))

  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/state', async (req, res) => {
    try {
      const mm = await readMood()
      // readMood() 会把引擎的实况挂在 mm 上（hormones / surface / underlying / unresolved /
      // affection），这里必须显式搬进来——不然前端读 m.hormones 永远是 undefined，激素那块就是空的。
      const mood = { mood: mm.value, top_dim: mm.label,
                     source: mm.source, stale: mm.stale,
                     hormones: mm.hormones || {},
                     surface: mm.surface, underlying: mm.underlying,
                     unresolved: mm.unresolved, affection: mm.affection,
                     rate: mm.rate, volume: mm.volume,
                     clingy: mm.clingy, distance: mm.distance,
                     why: mm.reason, reason: mm.reason, reasonAt: mm.reasonAt,
                     // 给别的语音插件读的现成一组：取这几个数就能照着说话
                     speech: speechOf(mm),
                     // 语音输出接没接。面板据此决定「节奏/力度」要不要显示——
                     // 没接的时候那两个数是空承诺，不该占位置。
                     tts: (await loadTtsCfg()).provider }
      const surf = await readJson('resurfaced.json', null)
      const dims = mm.dims, reason = mm.reason
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
      // 写配置只走 POST。以前为了省事留了 ?set= / ?reset=1 两个 GET 写入口，
      // 语义上不对，也容易被当成图片链接之类的东西误触发。
      if (u.searchParams.get('set') || u.searchParams.get('reset') === '1') {
        return json(res, { ok: false, err: 'GET 不再改配置，请用 POST' }, 405)
      }
      if (req.method === 'POST') {
        let body = ''
        for await (const ch of req) body += ch
        const parsed = JSON.parse(body || '{}')
        const d = await writeConfig(parsed.reset ? DEFAULTS : parsed)
        const task = await syncNightlyTask(d.nightly_at, d.nightly_on)
        return json(res, { ok: true, config: d, task, at: new Date().toISOString() })
      }
      json(res, { ok: true, config: await readConfig(), defaults: DEFAULTS })
    } catch (e) { json(res, { ok: false, err: String(e) }, 500) }
  })));


  // 用默认浏览器打开星图：桌面外壳会拦 target=_blank，所以让宿主动手
  ctx.effect(() => ctx.webServer.register(route(API_BASE + '/open', async (req, res) => {
    try {
      // 只打开内置的那张星图，不接受外部 URL。
      // 以前这里接 ?url= 参数并用系统默认程序打开任意地址 —— 等于谁能调这个接口，
      // 谁就能让我在他的电脑上启动东西。前端本来就不传参数，所以收紧掉。
      const url = 'file:///' + join(MIND, '星图.html').replace(/\\/g, '/')
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
