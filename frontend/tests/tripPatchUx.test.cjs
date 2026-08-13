const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const read = relativePath => fs.readFileSync(path.join(root, relativePath), 'utf8')

test('AIChat renders two explicit and visible modes', () => {
  const chat = read('src/components/AIChat.vue')
  assert.match(chat, /class="chat-mode-switch"/)
  assert.match(chat, /result\.chat\.qaMode/)
  assert.match(chat, /result\.chat\.patchMode/)
  assert.match(chat, /class="chat-mode-tab"/)
  assert.match(chat, /font-size: 38px/)
})

test('Q&A stays default and quick questions hide in patch mode', () => {
  const chat = read('src/components/AIChat.vue')
  assert.match(chat, /const patchMode = ref\(false\)/)
  assert.match(chat, /v-if="!patchMode" class="chat-suggestions"/)
  assert.match(chat, /patchMode \? t\('result\.chat\.editWelcome'\) : t\('result\.chat\.welcome'\)/)
})

test('patch mode uses patch API and returns before ordinary chat API', () => {
  const chat = read('src/components/AIChat.vue')
  const patchBranch = chat.indexOf('if (patchMode.value)')
  const patchCall = chat.indexOf('await patchTripPlan(', patchBranch)
  const branchReturn = chat.indexOf('\n      return', patchCall)
  const chatAsk = chat.indexOf('/api/chat/ask', patchCall)
  assert.ok(patchBranch >= 0 && patchCall > patchBranch)
  assert.ok(branchReturn > patchCall && chatAsk > branchReturn)
  assert.doesNotMatch(chat, /plan_version \|\| 1/)
})

test('missing task or version keeps edit entry visible but disabled with reason', () => {
  const chat = read('src/components/AIChat.vue')
  assert.match(chat, /:disabled="chatLoading \|\| !canPatch"/)
  assert.match(chat, /v-if="patchUnavailableReason"/)
  assert.match(chat, /result\.chat\.missingTaskInfo/)
  assert.match(chat, /result\.chat\.missingVersionInfo/)
  assert.match(chat, /result\.chat\.currentVersion/)
})

test('successful patch emits updated plan, graph and deterministic summary', () => {
  const chat = read('src/components/AIChat.vue')
  assert.match(chat, /result\.change_summary/)
  assert.match(chat, /emit\('plan-updated', result\.updated_plan, result\.graph_data\)/)
  assert.match(chat, /requires_regeneration/)
})

test('Result supplies task identity and consumes patched plan', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /:plan-id="planId"/)
  assert.match(result, /@plan-updated="handlePatchedPlan"/)
})

test('Result refreshes canonical backend plan before legacy sessionStorage', () => {
  const result = read('src/views/Result.vue')
  const canonicalComment = result.indexOf('A task ID makes the backend result canonical')
  const backendFetch = result.indexOf('await pollTaskStatus(planId.value)', canonicalComment)
  const cacheApply = result.indexOf('if (data && canUseCachedData)', backendFetch)
  assert.ok(canonicalComment >= 0)
  assert.ok(backendFetch > canonicalComment)
  assert.ok(cacheApply > backendFetch)
})

test('canonical legacy version 1 enables patch mode without fallback guessing', () => {
  const chat = read('src/components/AIChat.vue')
  assert.match(chat, /Number\.isInteger\(props\.tripPlan\.plan_version\)/)
  assert.match(chat, /Number\(props\.tripPlan\.plan_version\) >= 1/)
  assert.match(chat, /:disabled="chatLoading \|\| !canPatch"/)
  assert.doesNotMatch(chat, /plan_version \|\| 1/)
})

test('frontend patch types include version, diff and regeneration boundary', () => {
  const types = read('src/types/index.ts')
  assert.match(types, /plan_version\?: number/)
  assert.match(types, /export interface TripChangeDiff/)
  assert.match(types, /requires_regeneration: boolean/)
})

for (const locale of ['zh', 'en', 'ja']) {
  test(`${locale} locale includes Phase 2C patch UX messages`, () => {
    const messages = read(`src/i18n/locales/${locale}.json`)
    for (const key of ['qaMode', 'patchMode', 'editPlaceholder', 'missingTaskInfo', 'missingVersionInfo', 'currentVersion', 'understandingEdit', 'revalidatingEdit', 'patchFailed', 'versionConflict']) {
      assert.match(messages, new RegExp(`"${key}"`))
    }
  })
}
