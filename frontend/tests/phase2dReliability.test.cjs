const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const ts = require('typescript')
const Module = require('node:module')

const root = path.resolve(__dirname, '..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

const loadTsModule = (relativePath) => {
  const filename = path.join(root, relativePath)
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const loaded = new Module(filename, module)
  loaded.filename = filename
  loaded.paths = Module._nodeModulePaths(path.dirname(filename))
  loaded._compile(output, filename)
  return loaded.exports
}

test('frontend settings types and storage contain no server secrets', () => {
  const api = read('src/services/api.ts')
  const types = read('src/types/index.ts')
  const navbar = read('src/components/NavBar.vue')

  for (const source of [api, types]) {
    assert.doesNotMatch(source, /\bopenai_api_key\b/)
    assert.doesNotMatch(source, /\bxhs_cookie\b/)
    assert.doesNotMatch(source, /\bvite_amap_web_key\b/)
    assert.doesNotMatch(source, /\bamap_web_service_key\b/)
    assert.doesNotMatch(source, /\bgoogle_maps_proxy:\s*string\b/)
  }
  assert.doesNotMatch(navbar, /settingsForm\.(?:openai_api_key|xhs_cookie|vite_amap_web_key)/)
  assert.match(navbar, /Server secrets 请通过 backend\/\.env/)
})

test('verified map markers require match status, trusted source and coordinates', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /attraction\.poi_match_status === 'verified'/)
  assert.match(result, /source === 'google_places' \|\| source === 'amap'/)
  assert.match(result, /Number\.isFinite/)
  assert.match(result, /longitude >= -180 && longitude <= 180/)
  assert.match(result, /latitude >= -90 && latitude <= 90/)
  assert.match(result, /longitude === 0 && latitude === 0/)
})

test('weather UI labels source and unavailable state', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /高德天气 · 降级数据源/)
  assert.match(result, /Google Weather/)
  assert.match(result, /weatherUnavailableDescription/)
  assert.match(result, /selectedWeather\.precipitation_probability/)
  assert.match(result, /formatPrecipitationProbability/)
  assert.doesNotMatch(result, /getWeatherPrecipitation|getWeatherHumidity/)
  assert.doesNotMatch(result, /return '(?:85|65|55|30|10|88|78|72|62|42)%'/)
  assert.match(result, /return 'unknown'/)
  assert.doesNotMatch(result, /if \(!selectedWeather\.value\) return 'sunny'/)
})

test('weather unavailable UX distinguishes supported fallback outcomes', () => {
  const { getWeatherUnavailableDescription } = loadTsModule('src/utils/weatherAvailability.ts')
  const unsupported = [{
    provider: 'unavailable', request_success: true, data_available: false,
    degraded: true, reason: 'empty_forecast',
    primary_failure_reason: 'unsupported_location', days: [],
  }]
  const ordinaryFailure = [{
    provider: 'unavailable', request_success: false, data_available: false,
    degraded: true, reason: 'network_error', primary_failure_reason: 'timeout', days: [],
  }]

  assert.equal(getWeatherUnavailableDescription([], unsupported), '当前天气源暂不覆盖该地区')
  assert.equal(getWeatherUnavailableDescription([], ordinaryFailure), '天气数据暂不可用')
  assert.equal(getWeatherUnavailableDescription([], []), '天气数据暂不可用')
  for (const length of [1, 2, 3, 7]) {
    const days = Array.from({ length }, (_, index) => ({ date: `2026-08-${index + 1}` }))
    assert.equal(getWeatherUnavailableDescription(days, unsupported), '天气数据暂不可用')
  }
})

test('straight-line route fallback is explicitly disclosed', () => {
  const result = read('src/views/Result.vue')
  assert.match(result, /真实路线数据不可用，仅连接景点位置/)
})

test('photo cards label Google, XHS and placeholder sources', () => {
  const result = read('src/views/Result.vue')
  const card = read('src/components/OverviewAttractionCard.vue')
  assert.match(result, /attractionPhotoSources/)
  assert.match(card, /小红书图片/)
  assert.match(card, /Google Places 图片/)
  assert.match(card, /占位图/)
  assert.match(result, /addressParam/)
  assert.match(result, /categoryParam/)
  assert.match(result, /planIdParam/)
  assert.match(result, /plan_id=/)
  assert.match(result, /browser_load_error/)
  assert.match(result, /attractionPhotoReasons/)
})
