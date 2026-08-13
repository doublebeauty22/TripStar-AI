const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const read = relativePath => fs.readFileSync(path.join(root, relativePath), 'utf8')

test('Result renders Phase 2A validation status and risks', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /tripPlan\.validation_status/)
  assert.match(result, /v-for="risk in tripPlan\.risks"/)
  assert.match(result, /result\.risks\.disclaimer/)
  assert.match(result, /day\.start_time/)
})

test('frontend types preserve Phase 2A fields and add minimal Phase 2B state', () => {
  const types = read('src/types/index.ts')
  assert.match(types, /validation_status\?: 'passed' \| 'issues_found' \| 'degraded'/)
  assert.match(types, /export interface RiskItem/)
  assert.match(types, /\| 'validating'/)
  assert.match(types, /revision_count\?: 0 \| 1/)
  assert.match(types, /\| 'critic'/)
  assert.match(types, /\| 'revising'/)
  assert.match(types, /\| 'revalidating'/)
  assert.doesNotMatch(types, /CriticResult/)
})

test('Result shows the adjustment notice only for revision_count 1', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /tripPlan\.revision_count === 1/)
  assert.match(result, /result\.risks\.autoRevised/)
})
