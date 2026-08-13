const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const read = relative => fs.readFileSync(path.resolve(__dirname, '..', relative), 'utf8')

test('example trip uses the read-only demo endpoint and never creates a task', () => {
  const api = read('src/services/api.ts')
  const landing = read('src/views/Landing.vue')
  assert.match(api, /getExampleTrip/)
  assert.match(api, /\/api\/demo\/example-trip/)
  assert.match(landing, /await getExampleTrip\(\)/)
  assert.match(landing, /query: \{ example: '1' \}/)
  assert.doesNotMatch(landing, /openExampleTrip[\s\S]{0,1000}submitTripPlan/)
})

test('example mode survives refresh and remains visibly labeled', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /route\.query\.example === '1'/)
  assert.match(result, /tripstar\.exampleMeta/)
  assert.match(result, /result\.example\.badge/)
  assert.match(result, /v-if="!isExample"/)
  assert.match(result, /if \(!isExample\.value\)[\s\S]{0,120}loadAttractionPhotos/)
})

test('public landing contains dual CTA, portfolio evidence, and no remote hero asset', () => {
  const landing = read('src/views/Landing.vue')
  assert.match(landing, /home\.planCta/)
  assert.match(landing, /home\.exampleCta/)
  assert.match(landing, /home\.evidence\.disclaimer/)
  assert.doesNotMatch(landing, /demos\.creative-tim\.com/)
})

test('result presents user-facing summary without plan identifiers', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /trip-summary-card/)
  assert.match(result, /confidence-strip/)
  assert.doesNotMatch(result, /Plan ID:/)
})
