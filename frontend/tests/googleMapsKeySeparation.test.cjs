const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

test('runtime settings never write or expose a Google Server Key', () => {
  const api = read('src/services/api.ts')
  const types = read('src/types/index.ts')

  assert.doesNotMatch(api, /setItem\([^\n]*google_maps/i)
  assert.doesNotMatch(api, /getRuntimeGoogleMapsApiKey|setRuntimeGoogleMapsApiKey/)
  assert.doesNotMatch(types, /google_maps_(?:server_)?api_key/)
  assert.match(api, /removeItem\(LEGACY_GOOGLE_MAPS_API_KEY_STORAGE_KEY\)/)
})

test('Result loads Google Maps only from the Vite Browser Key', () => {
  const result = read('src/views/Result.vue')

  assert.match(result, /import\.meta\.env\.VITE_GOOGLE_MAPS_BROWSER_KEY/)
  assert.match(result, /initGoogleMap\(GOOGLE_MAPS_BROWSER_KEY\)/)
  assert.doesNotMatch(result, /getBackendRuntimeSettings|getRuntimeGoogleMapsApiKey/)
  assert.match(result, /if \(GOOGLE_MAPS_BROWSER_KEY\)/)
  assert.match(result, /await initAMap\(\)/)
})

test('settings UI has no editable Google key field', () => {
  const navBar = read('src/components/NavBar.vue')

  assert.doesNotMatch(navBar, /v-model:value="settingsForm\.google_maps_api_key"/)
  assert.match(navBar, /Google Server Key 请在 backend\/\.env 中配置/)
})
