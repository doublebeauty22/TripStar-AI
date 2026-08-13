const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const nav = fs.readFileSync(path.resolve(__dirname, '../src/components/NavBar.vue'), 'utf8')

test('public navigation exposes home, how it works, GitHub, and language', () => {
  assert.match(nav, /home\.nav\.home/)
  assert.match(nav, /home\.nav\.how/)
  assert.match(nav, /https:\/\/github\.com\/doublebeauty22\/TripStar-AI/)
  assert.match(nav, /landing-lang-item/)
})

test('runtime settings stay hidden in public demo mode', () => {
  assert.match(nav, /v-if="!publicDemoMode"/)
})
