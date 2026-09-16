const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const ts = require('typescript')

const loadTypeScriptModule = (relativePath) => {
  const filename = path.join(__dirname, '..', relativePath)
  const source = fs.readFileSync(filename, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
    fileName: filename,
    reportDiagnostics: true,
  })
  assert.deepEqual(compiled.diagnostics || [], [])
  const loaded = { exports: {} }
  Function('require', 'module', 'exports', compiled.outputText)(
    require,
    loaded,
    loaded.exports,
  )
  return loaded.exports
}

const {
  clearFailedPhotoState,
  reportPhotoClientEvent,
} = loadTypeScriptModule('src/services/photoTelemetry.ts')

test('failed browser image clears the stale URL and attribution', () => {
  const photos = { Temple: 'https://private.example/broken' }
  const attributions = { Temple: [{ displayName: 'Private' }] }
  const sources = { Temple: 'xhs' }
  const reasons = {}

  clearFailedPhotoState('Temple', photos, attributions, sources, reasons)

  assert.deepEqual(photos, {})
  assert.deepEqual(attributions, {})
  assert.equal(sources.Temple, 'placeholder')
  assert.equal(reasons.Temple, 'browser_load_error')
})

test('client event sends only fixed fields and deduplicates each surface', async () => {
  const calls = []
  const fakeFetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: true }
  }
  const reported = new Set()
  const event = {
    request_id: 'abcdef123456',
    source: 'xhs',
    outcome: 'load_success',
    surface: 'overview',
  }

  assert.equal(await reportPhotoClientEvent('', event, reported, fakeFetch), true)
  assert.equal(await reportPhotoClientEvent(
    '', { ...event, outcome: 'load_error' }, reported, fakeFetch,
  ), false)
  assert.equal(await reportPhotoClientEvent(
    '', { ...event, surface: 'day' }, reported, fakeFetch,
  ), true)

  assert.equal(calls.length, 2)
  assert.equal(calls[0].url, '/api/poi/photo/client-event')
  assert.deepEqual(JSON.parse(calls[0].options.body), event)
  assert.deepEqual(Object.keys(JSON.parse(calls[0].options.body)).sort(), [
    'outcome', 'request_id', 'source', 'surface',
  ])
  assert.equal(calls[0].options.keepalive, true)
})

test('invalid request ids and sources never emit', async () => {
  let calls = 0
  const fakeFetch = async () => {
    calls += 1
    return { ok: true }
  }
  const reported = new Set()
  const base = {
    request_id: 'abcdef123456',
    source: 'xhs',
    outcome: 'load_error',
    surface: 'day',
  }

  assert.equal(await reportPhotoClientEvent(
    '', { ...base, request_id: 'unsafe' }, reported, fakeFetch,
  ), false)
  assert.equal(await reportPhotoClientEvent(
    '', { ...base, source: 'placeholder' }, reported, fakeFetch,
  ), false)
  assert.equal(calls, 0)
})

test('telemetry transport failure is fail-open and attempted only once', async () => {
  let calls = 0
  const failingFetch = async () => {
    calls += 1
    throw new Error('private transport detail')
  }
  const reported = new Set()
  const event = {
    request_id: 'abcdef123456',
    source: 'google_places',
    outcome: 'load_error',
    surface: 'day',
  }

  assert.equal(await reportPhotoClientEvent('', event, reported, failingFetch), false)
  assert.equal(await reportPhotoClientEvent('', event, reported, failingFetch), false)
  assert.equal(calls, 1)
})
