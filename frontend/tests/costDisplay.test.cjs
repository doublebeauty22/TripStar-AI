const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const ts = require('typescript')

// This suite exercises pure display/storage code; any accidental fetch fails closed.
global.fetch = () => { throw new Error('Cost display tests must stay offline') }
const root = path.resolve(__dirname, '..')
const sourcePath = path.join(root, 'src/utils/costDisplay.ts')
const load = () => {
  assert.ok(fs.existsSync(sourcePath), 'Result needs the shared cost display implementation')
  const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', compiled)(require, mod, mod.exports)
  return mod.exports
}
const translator = lang => {
  const messages = JSON.parse(fs.readFileSync(path.join(root, `src/i18n/locales/${lang}.json`)))
  return (key, args = {}) => {
    const value = key.split('.').reduce((v, k) => v?.[k], messages)
    assert.equal(typeof value, 'string', `Missing ${lang} key: ${key}`)
    return value.replace(/\{(\w+)\}/g, (_, k) => String(args[k]))
  }
}
const storage = () => {
  const data = new Map()
  return { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) }
}

test('5000 cap and 1900 reference total stay distinct, with uncertainty and over-limit risk', () => {
  const { budgetOverview } = load()
  const t = translator('zh')
  const display = budgetOverview({ budget_cny: 5000 }, 1900, t)
  assert.match(display.cap, /你的预算上限.*人民币 ¥5000/)
  assert.match(display.total, /计划参考总额.*人民币 ¥1900/)
  assert.match(display.comparison, /尚不能确认最终支出/)
  assert.doesNotMatch(JSON.stringify(display), /已省|节省|确定不超|3100/)
  assert.match(budgetOverview({ budget_cny: 5000 }, 6000, t).comparison, /超过.*上限/)
  assert.match(budgetOverview({ budget_cny: 5000 }, 5000, t).comparison, /仍可能超限/)
})

test('hotel estimates remain unverified after edits; unknown amounts never imply free', () => {
  const { referenceCost } = load()
  const t = translator('zh')
  const plan = { hotel: { estimated_cost: 450, price_range: '450', place_id: 'some-poi', verified_price: 450 } }
  const before = JSON.stringify(plan)
  assert.equal(referenceCost(plan.hotel.estimated_cost, t, true), '计划内参考估算：人民币 ¥450/晚，未核实报价')
  assert.equal(JSON.stringify(plan), before)
  for (const amount of [undefined, null, 0, -1, NaN, Infinity, '', '450']) {
    assert.equal(referenceCost(amount, t, true), '费用未确认')
  }
  plan.hotel.estimated_cost = 700
  assert.match(referenceCost(plan.hotel.estimated_cost, t, true), /700.*未核实报价/)
})

test('task-bound cache refuses stale budgets, legacy results, and malformed metadata', () => {
  const { cacheTaskCostDisplay, readTaskCostDisplay, responseTaskCostDisplay, budgetOverview } = load()
  const s = storage()
  const a = { plan_id: 'A', cost_display: { budget_cny: 5000 }, data: { budget: { total: 1900 } } }
  cacheTaskCostDisplay(s, a)
  assert.deepEqual(readTaskCostDisplay(s, 'A'), { budget_cny: 5000 })
  assert.equal(readTaskCostDisplay(s, 'B'), null)
  assert.equal(responseTaskCostDisplay('B', a), null)
  const b = { plan_id: 'B', cost_display: { budget_cny: 8000 } }
  cacheTaskCostDisplay(s, b)
  assert.deepEqual(readTaskCostDisplay(s, 'B'), { budget_cny: 8000 })
  assert.equal(readTaskCostDisplay(s, 'A'), null)
  assert.equal(responseTaskCostDisplay('B', { plan_id: 'B', data: a.data }), null)
  for (const value of [undefined, 0, -1, '5000', true, NaN, Infinity]) {
    assert.equal(responseTaskCostDisplay('A', { plan_id: 'A', cost_display: { budget_cny: value } }), null)
  }
  const t = translator('zh')
  assert.equal(budgetOverview(null, 1900, t).cap, '原始预算上限未能恢复')
  assert.equal(budgetOverview({ budget_cny: null }, 1900, t).cap, '未设置预算上限')
  assert.deepEqual(a.data, { budget: { total: 1900 } })
})

test('all supported languages provide complete labels, CNY units, and uncertainty', () => {
  const { budgetOverview, referenceCost } = load()
  for (const lang of ['zh', 'en', 'ja']) {
    const t = translator(lang)
    for (const key of ['totalDisclaimer', 'categoryNote', 'hotelPending', 'unknown']) t(`result.cost.${key}`)
    assert.match(budgetOverview({ budget_cny: 5000 }, 1900, t).cap, /5000/)
    assert.match(referenceCost(450, t, true), /450/)
    assert.match(referenceCost(20, t), /20/)
    budgetOverview({ budget_cny: 5000 }, 6000, t)
    budgetOverview(null, 1900, t)
    budgetOverview({ budget_cny: null }, 1900, t)
  }
})

test('Result uses the tested display code for cards, rows, exports and task recovery', () => {
  const result = fs.readFileSync(path.join(root, 'src/views/Result.vue'), 'utf8')
  assert.match(result, /budgetOverview\(taskCostDisplay\.value/)
  assert.match(result, /referenceCost\(day\.hotel\.estimated_cost, t, true\)/)
  assert.match(result, /referenceCost\(item\.amount, t, item\.type === 'hotel'\)/)
  assert.match(result, /responseTaskCostDisplay\(planId\.value, response\)/)
  assert.match(result, /readTaskCostDisplay\(sessionStorage, planId\.value\)/)
  assert.match(result, /watch\(\(\) => route\.query\.plan_id/)
  assert.doesNotMatch(result, /\{\{ day\.hotel\.price_range \}\}/)
  assert.doesNotMatch(result, /if \(amount <= 0\) return/)
  assert.match(result, /\$\{costOverview\.value\.cap\}/)
  // Existing explicit fee explanations (including evidenced free entry) survive.
  assert.match(result, /\{\{ item\.description \}\}/)
  assert.match(result, /\$\{a\.description \|\| ''\}/)
})

// Run the actual Result restore/Patch functions with fake I/O, rather than a
// second implementation of their task association rules.
const resultHarness = () => {
  const result = fs.readFileSync(path.join(root, 'src/views/Result.vue'), 'utf8')
  const slice = (start, end) => result.slice(result.indexOf(start), result.indexOf(end))
  const state = {
    sessionStorage: storage(), route: { query: { plan_id: 'A' } },
    tripPlan: { value: null }, planId: { value: '' }, taskCostDisplay: { value: null },
    editMode: { value: false }, originalPlan: { value: null },
    pendingBudgetItems: { value: [] }, graphData: { value: null }, graphCategories: { value: [] },
    isExample: { value: false }, exampleMeta: { value: null }, activeSection: { value: 'budget' },
    attractionPhotos: {}, attractionPhotoAttributions: {}, attractionPhotoSources: {},
    attractionPhotoRequestIds: {}, attractionPhotoReasons: {},
    loadAttractionPhotos: async () => {}, ensureMapReady: async () => {}, ensureGraphReady: async () => {},
    initOverviewSwiper: async () => {}, message: { error: () => {} }, t: translator('zh'),
    responses: {},
  }
  state.pollTaskStatus = async id => state.responses[id]
  const body = `const { ${Object.keys(state).join(', ')} } = state;
    const { cacheTaskCostDisplay, readTaskCostDisplay, responseTaskCostDisplay } = utils;
    ${slice('const applyTripPlanPayload =', 'const destroyCurrentMap =')}
    ${slice('let resultLoadVersion =', 'onMounted(async () =>')}
    return { loadResultPlan, handlePatchedPlan };`
  const compiled = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText
  return { state, ...new Function('state', 'utils', 'console', compiled)(state, load(), { error: () => {} }) }
}

test('actual Result restore, failed task switch, refresh and Patch keep cap/task association', async () => {
  const { state: s, loadResultPlan, handlePatchedPlan } = resultHarness()
  const original = { city: 'Synthetic', days: [], budget: { total: 1900 } }
  const completed = (id, budget) => ({ status: 'completed', result: {
    success: true, plan_id: id, data: structuredClone(original), cost_display: { budget_cny: budget },
  } })
  s.responses.A = completed('A', 5000)
  s.responses.B = { status: 'processing' }
  await loadResultPlan()
  assert.deepEqual(s.taskCostDisplay.value, { budget_cny: 5000 })
  const stored = s.sessionStorage.getItem('tripPlan')
  const { budgetOverview } = load()
  budgetOverview(s.taskCostDisplay.value, s.tripPlan.value.budget.total, s.t)
  assert.equal(s.sessionStorage.getItem('tripPlan'), stored, 'render must not recalculate the plan')
  s.editMode.value = true
  s.originalPlan.value = structuredClone(original)
  s.route.query.plan_id = 'B'
  await loadResultPlan()
  assert.equal(s.tripPlan.value, null, 'do not show task A data as task B')
  assert.equal(s.taskCostDisplay.value, null)
  assert.equal(s.editMode.value, false, 'switching tasks must discard the old edit session')
  assert.equal(s.originalPlan.value, null)
  assert.equal(s.sessionStorage.getItem('planId'), 'A', 'cache identity must stay with its data')
  await loadResultPlan() // refresh while B is still unavailable
  assert.equal(s.tripPlan.value, null)
  s.responses.B = completed('B', 8000)
  await loadResultPlan()
  assert.deepEqual(s.taskCostDisplay.value, { budget_cny: 8000 })
  await handlePatchedPlan({ ...original, budget: { total: 999 } }, null, 'A')
  assert.equal(s.tripPlan.value.budget.total, 1900, 'late Patch A must not replace task B')
  await handlePatchedPlan({ ...original, budget: { total: 2100 } }, null, 'B')
  assert.deepEqual(s.taskCostDisplay.value, { budget_cny: 8000 })
  assert.equal(s.tripPlan.value.budget.total, 2100)
  s.responses.B = { status: 'processing' }
  await loadResultPlan() // offline-compatible fallback to the same task's cache
  assert.deepEqual(s.taskCostDisplay.value, { budget_cny: 8000 })
  assert.equal(s.tripPlan.value.budget.total, 2100)
  s.route.query.plan_id = 'A'
  delete s.responses.A.result.cost_display
  await loadResultPlan()
  assert.equal(s.taskCostDisplay.value, null, 'legacy response must not borrow task B or last form budget')
  assert.deepEqual(s.tripPlan.value, original)
})

test('actual edit dialog keeps an unknown amount unknown instead of presenting default zero', () => {
  const result = fs.readFileSync(path.join(root, 'src/views/Result.vue'), 'utf8')
  const start = result.indexOf('const editBudgetItemAmount =')
  const end = result.indexOf('const deleteBudgetItem =', start)
  const compiled = ts.transpileModule(`${result.slice(start, end)}; return editBudgetItemAmount`, {
    compilerOptions: { target: ts.ScriptTarget.ES2020 },
  }).outputText
  const calls = []
  const edit = new Function('tripPlan', 'window', 't', 'formatBudgetAmount', 'referenceCost', compiled)(
    { value: { days: [{}] } }, { prompt: (...args) => { calls.push(args); return null } },
    translator('zh'), String, load().referenceCost,
  )
  edit({ type: 'hotel', name: 'Synthetic', dayIndex: 0, amount: 0 })
  assert.match(calls[0][0], /费用未确认/)
  assert.doesNotMatch(calls[0][0], /¥0/)
  assert.equal(calls[0][1], '')
})

test('late rejected task load cannot restore a stale cache over the active task', async () => {
  const { state: s, loadResultPlan } = resultHarness()
  const completed = total => ({ status: 'completed', result: {
    success: true, plan_id: 'A', cost_display: { budget_cny: 5000 }, data: { days: [], budget: { total } },
  } })
  s.responses.A = completed(1900)
  await loadResultPlan()
  let rejectB
  s.responses.B = new Promise((_, reject) => { rejectB = reject })
  s.route.query.plan_id = 'B'
  const loadingB = loadResultPlan()
  s.route.query.plan_id = 'A'
  s.responses.A = completed(2100)
  await loadResultPlan()
  rejectB(new Error('synthetic stale request failure'))
  await loadingB
  assert.equal(s.tripPlan.value.budget.total, 2100)
  assert.equal(JSON.parse(s.sessionStorage.getItem('tripPlan')).budget.total, 2100)
  assert.deepEqual(s.taskCostDisplay.value, { budget_cny: 5000 })
})

test('actual export retains cost labels, descriptions and cap even without generated budget', () => {
  const result = fs.readFileSync(path.join(root, 'src/views/Result.vue'), 'utf8')
  const start = result.indexOf('const buildExportHTML =')
  const end = result.indexOf('// ========== 捕获地图截图', start)
  const compiled = ts.transpileModule(`${result.slice(start, end)}; return buildExportHTML`, {
    compilerOptions: { target: ts.ScriptTarget.ES2020 },
  }).outputText
  const t = translator('zh')
  const { budgetOverview, referenceCost } = load()
  const plan = { city: 'Synthetic', days: [{ attractions: [{ name: 'Synthetic public site', ticket_price: 0,
    description: '已有免费说明文本（合成），保持原文；不据此升级为已核验费用。' }], meals: [],
    hotel: { name: 'Synthetic hotel', estimated_cost: 450 } }], budget: { total: 1900 } }
  const planRef = { value: plan }
  const overview = { value: budgetOverview({ budget_cny: 5000 }, 1900, t) }
  const render = new Function('tripPlan', 'attractionPhotos', 'costOverview', 'referenceCost', 't', compiled)(
    planRef, { value: {} }, overview, referenceCost, t,
  )
  const before = JSON.stringify(plan)
  const html = render()
  assert.match(html, /你的预算上限.*5000/)
  assert.match(html, /计划参考总额.*1900/)
  assert.match(html, /对应入住日期的房价待查询/)
  assert.match(html, /450\/晚，未核实报价/)
  assert.ok(html.includes(plan.days[0].attractions[0].description), 'keep existing explicit fee explanations')
  assert.equal(JSON.stringify(plan), before)
  planRef.value = { city: 'Synthetic', days: [] }
  overview.value = budgetOverview({ budget_cny: 5000 }, undefined, t)
  assert.match(render(), /你的预算上限.*5000/)
  assert.match(render(), /计划参考总额.*费用未确认/)
})
