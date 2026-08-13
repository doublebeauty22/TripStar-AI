const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const ts = require('typescript')

const sourcePath = path.resolve(__dirname, '../src/services/tripTaskLifecycle.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
  fileName: sourcePath,
}).outputText
const lifecycleModule = { exports: {} }
new Function('require', 'module', 'exports', compiled)(require, lifecycleModule, lifecycleModule.exports)

const { monitorTripTask, TripTaskFailedError } = lifecycleModule.exports

const completedResult = {
  success: true,
  message: 'ok',
  plan_id: 'plan-1',
  data: { city: '东京', days: [] },
  graph_data: { nodes: [], edges: [], categories: [] },
}

class FakeSocket {
  constructor() {
    this.readyState = 1
    this.onmessage = null
    this.onerror = null
    this.onclose = null
  }

  close() {
    this.readyState = 3
  }

  emit(payload) {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }

  disconnect() {
    this.readyState = 3
    this.onclose?.()
  }
}

const task = { task_id: 'task-1', plan_id: 'plan-1', ws_url: '/ws/task-1' }

test('WebSocket normal completion resolves final result', async () => {
  const socket = new FakeSocket()
  const promise = monitorTripTask(task, 'ws://test', { pollFallbackDelayMs: 1000 }, {
    createWebSocket: () => socket,
    fetchStatus: async () => { throw new Error('polling should not run') },
  })
  socket.emit({
    task_id: 'task-1', plan_id: 'plan-1', status: 'completed', stage: 'completed',
    progress: 100, message: 'done', result: completedResult,
  })
  assert.deepEqual(await promise, completedResult)
})

test('early WebSocket close falls back to completed status', async () => {
  const socket = new FakeSocket()
  const promise = monitorTripTask(task, 'ws://test', {
    pollFallbackDelayMs: 0,
    pollIntervalMs: 0,
  }, {
    createWebSocket: () => socket,
    fetchStatus: async () => ({
      task_id: 'task-1', plan_id: 'plan-1', status: 'completed', result: completedResult,
    }),
  })
  socket.disconnect()
  assert.deepEqual(await promise, completedResult)
})

test('failed task rejects immediately with backend reason', async () => {
  const promise = monitorTripTask(task, '', {
    useWebSocket: false,
    pollIntervalMs: 0,
  }, {
    createWebSocket: () => { throw new Error('unused') },
    fetchStatus: async () => ({
      task_id: 'task-1', plan_id: 'plan-1', status: 'failed', error: '真实后端错误',
    }),
  })
  await assert.rejects(promise, error => (
    error instanceof TripTaskFailedError && error.message === '真实后端错误'
  ))
})

test('refresh recovery resolves an already completed task by status', async () => {
  const result = await monitorTripTask(task, '', {
    useWebSocket: false,
    pollIntervalMs: 0,
  }, {
    createWebSocket: () => { throw new Error('unused') },
    fetchStatus: async () => ({
      task_id: 'task-1', plan_id: 'plan-1', status: 'completed', result: completedResult,
    }),
  })
  assert.equal(result.plan_id, 'plan-1')
})

test('loading state always ends after completion', async () => {
  let loading = true
  try {
    await monitorTripTask(task, '', {
      useWebSocket: false,
      pollIntervalMs: 0,
    }, {
      createWebSocket: () => { throw new Error('unused') },
      fetchStatus: async () => ({
        task_id: 'task-1', plan_id: 'plan-1', status: 'completed', result: completedResult,
      }),
    })
  } finally {
    loading = false
  }
  assert.equal(loading, false)
})
