import type { TripPlanResponse, TripTaskEvent, TripTaskStatusResponse } from '@/types'

export interface SubmittedTripTask {
  task_id: string
  plan_id: string
  ws_url: string
}

export interface TripTaskMonitorOptions {
  onTaskEvent?: (event: TripTaskEvent) => void
  useWebSocket?: boolean
  pollFallbackDelayMs?: number
  pollIntervalMs?: number
  maxConsecutivePollErrors?: number
}

export interface TripTaskMonitorDependencies {
  createWebSocket: (url: string) => WebSocket
  fetchStatus: (taskId: string) => Promise<TripTaskStatusResponse>
}

export class TripTaskFailedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'TripTaskFailedError'
  }
}

const toTaskEvent = (status: TripTaskStatusResponse): TripTaskEvent => ({
  task_id: status.task_id,
  plan_id: status.plan_id,
  status: status.status,
  stage: status.stage || (status.status === 'completed' ? 'completed' : status.status === 'failed' ? 'failed' : 'initializing'),
  progress: status.progress ?? (status.status === 'processing' ? 0 : 100),
  message: status.message || status.progress_text || '',
  error: status.error,
  result: status.result,
})

/**
 * Resolve a trip task from WebSocket events, with status polling as a safety net.
 * Polling also starts after a short delay when the socket stays open but never
 * delivers a final payload, preventing a permanently spinning loading screen.
 */
export function monitorTripTask(
  task: SubmittedTripTask,
  wsUrl: string,
  options: TripTaskMonitorOptions,
  dependencies: TripTaskMonitorDependencies,
): Promise<TripPlanResponse> {
  const useWebSocket = options.useWebSocket !== false
  const fallbackDelay = options.pollFallbackDelayMs ?? 1500
  const pollInterval = options.pollIntervalMs ?? 2000
  const maxPollErrors = options.maxConsecutivePollErrors ?? 5

  return new Promise((resolve, reject) => {
    let settled = false
    let socket: WebSocket | null = null
    let pollTimer: ReturnType<typeof setTimeout> | null = null
    let pollInFlight = false
    let consecutivePollErrors = 0

    const cleanup = () => {
      if (pollTimer !== null) clearTimeout(pollTimer)
      pollTimer = null
      if (socket && socket.readyState < 2) socket.close()
      socket = null
    }

    const finish = (result: TripPlanResponse) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(result)
    }

    const fail = (reason: unknown) => {
      if (settled) return
      settled = true
      cleanup()
      reject(reason instanceof Error ? reason : new Error(String(reason)))
    }

    const handleEvent = (event: TripTaskEvent): boolean => {
      options.onTaskEvent?.(event)
      if (event.status === 'failed') {
        fail(new TripTaskFailedError(event.error || event.message || '旅行计划生成失败'))
        return true
      }
      if (event.status === 'completed' && event.result) {
        finish(event.result)
        return true
      }
      return false
    }

    const schedulePoll = (delay = pollInterval) => {
      if (settled || pollTimer !== null) return
      pollTimer = setTimeout(() => {
        pollTimer = null
        void pollOnce()
      }, delay)
    }

    const pollOnce = async () => {
      if (settled || pollInFlight) return
      pollInFlight = true
      try {
        const status = await dependencies.fetchStatus(task.task_id)
        consecutivePollErrors = 0
        if (!handleEvent(toTaskEvent(status))) schedulePoll()
      } catch (error) {
        consecutivePollErrors += 1
        if (consecutivePollErrors >= maxPollErrors) {
          fail(error)
        } else {
          schedulePoll()
        }
      } finally {
        pollInFlight = false
      }
    }

    // Always arm polling: it covers early close and a connected socket that
    // never receives (or cannot parse) the final payload.
    schedulePoll(useWebSocket ? fallbackDelay : 0)

    if (!useWebSocket) return

    try {
      socket = dependencies.createWebSocket(wsUrl)
      socket.onmessage = (event) => {
        try {
          const taskEvent = JSON.parse(String(event.data)) as TripTaskEvent
          if (!handleEvent(taskEvent) && taskEvent.status === 'completed') {
            schedulePoll(0)
          }
        } catch {
          schedulePoll(0)
        }
      }
      socket.onerror = () => schedulePoll(0)
      socket.onclose = () => {
        socket = null
        schedulePoll(0)
      }
    } catch {
      socket = null
      schedulePoll(0)
    }
  })
}
