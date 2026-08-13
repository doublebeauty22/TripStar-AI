const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

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
  assert.match(result, /天气数据暂不可用/)
  assert.match(result, /selectedWeather\.precipitation_probability/)
  assert.match(result, /formatPrecipitationProbability/)
  assert.doesNotMatch(result, /getWeatherPrecipitation|getWeatherHumidity/)
  assert.doesNotMatch(result, /return '(?:85|65|55|30|10|88|78|72|62|42)%'/)
  assert.match(result, /return 'unknown'/)
  assert.doesNotMatch(result, /if \(!selectedWeather\.value\) return 'sunny'/)
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
})
