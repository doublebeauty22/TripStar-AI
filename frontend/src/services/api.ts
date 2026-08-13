import axios from 'axios'
import type {
  BackendRuntimeSettings,
  PreferenceParseRequest,
  PreferenceParseResponse,
  RuntimeSettings,
  TripFormData,
  TripHistoryItem,
  TripPlanResponse,
  PortfolioExampleTrip,
  TripPatchResult,
  TripTaskEvent,
  TripTaskStatusResponse,
} from '@/types'
import { i18n } from '@/i18n'
import { monitorTripTask } from './tripTaskLifecycle'

const ENV_API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
const ENV_AMAP_WEB_JS_KEY = import.meta.env.VITE_AMAP_WEB_JS_KEY ?? ''
const RUNTIME_API_BASE_STORAGE_KEY = 'tripstar.runtime.api_base_url'
const RUNTIME_AMAP_WEB_JS_KEY_STORAGE_KEY = 'tripstar.runtime.amap_web_js_key'
const LEGACY_GOOGLE_MAPS_API_KEY_STORAGE_KEY = 'tripstar.runtime.google_maps_api_key'
const DEFAULT_RUNTIME_BACKEND_SETTINGS: BackendRuntimeSettings = {
  vite_amap_web_js_key: '',
  google_maps_proxy_configured: false,
  openai_base_url: '',
  openai_model: '',
  openai_configured: false,
  xhs_configured: false,
  amap_server_configured: false,
  google_server_configured: false,
}

export const RUNTIME_SETTINGS_UPDATED_EVENT = 'tripstar:runtime-settings-updated'
const t = i18n.global.t

// Remove any Server Key cached by older frontend builds. It is never read or written again.
if (typeof window !== 'undefined') {
  window.localStorage.removeItem(LEGACY_GOOGLE_MAPS_API_KEY_STORAGE_KEY)
}

const normalizeBaseUrl = (value: string | null | undefined): string => {
  const text = String(value ?? '').trim()
  return text.replace(/\/+$/, '')
}

const normalizeText = (value: unknown): string => String(value ?? '').trim()

const resolveDefaultApiBaseUrl = (): string => {
  const fromEnv = normalizeBaseUrl(ENV_API_BASE_URL)
  if (fromEnv) return fromEnv
  // 同源部署（Docker / 云端）：API 与前端在同一 origin 下
  if (typeof window !== 'undefined' && window.location) {
    return normalizeBaseUrl(window.location.origin) || ''
  }
  // Same-origin is also safe for SSR/test contexts; Vite can proxy it in development.
  return ''
}

const DEFAULT_API_BASE_URL = resolveDefaultApiBaseUrl()
const DEFAULT_AMAP_WEB_JS_KEY = normalizeText(ENV_AMAP_WEB_JS_KEY)

export interface SubmitTripPlanResponse {
  task_id: string
  plan_id: string
  status: 'processing'
  ws_url: string
  message: string
}

interface GenerateTripPlanOptions {
  onTaskCreated?: (task: SubmitTripPlanResponse) => void
  onTaskEvent?: (event: TripTaskEvent) => void
}

interface RuntimeSettingsApiResponse {
  success: boolean
  message?: string
  data?: Partial<BackendRuntimeSettings>
}

interface TripHistoryResponse {
  items?: TripHistoryItem[]
}

export const getRuntimeApiBaseUrl = (): string => {
  if (typeof window === 'undefined') {
    return DEFAULT_API_BASE_URL
  }
  const saved = normalizeBaseUrl(window.localStorage.getItem(RUNTIME_API_BASE_STORAGE_KEY))
  return saved || DEFAULT_API_BASE_URL
}

export const setRuntimeApiBaseUrl = (value: string): string => {
  const normalized = normalizeBaseUrl(value) || DEFAULT_API_BASE_URL
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(RUNTIME_API_BASE_STORAGE_KEY, normalized)
  }
  return normalized
}

export const getRuntimeMapJsKey = (): string => {
  if (typeof window === 'undefined') {
    return DEFAULT_AMAP_WEB_JS_KEY
  }
  const saved = normalizeText(window.localStorage.getItem(RUNTIME_AMAP_WEB_JS_KEY_STORAGE_KEY))
  return saved || DEFAULT_AMAP_WEB_JS_KEY
}

export const setRuntimeMapJsKey = (value: string): string => {
  const normalized = normalizeText(value)
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(RUNTIME_AMAP_WEB_JS_KEY_STORAGE_KEY, normalized)
  }
  return normalized
}

const getWsBaseUrl = (): string => getRuntimeApiBaseUrl().replace(/^http/i, 'ws').replace(/\/+$/, '')

const normalizeBackendRuntimeSettings = (
  data?: Partial<BackendRuntimeSettings>
): BackendRuntimeSettings => ({
  vite_amap_web_js_key: normalizeText(
    data?.vite_amap_web_js_key ?? DEFAULT_RUNTIME_BACKEND_SETTINGS.vite_amap_web_js_key
  ),
  google_maps_proxy_configured: Boolean(data?.google_maps_proxy_configured),
  openai_base_url:
    normalizeText(data?.openai_base_url ?? DEFAULT_RUNTIME_BACKEND_SETTINGS.openai_base_url) ||
    DEFAULT_RUNTIME_BACKEND_SETTINGS.openai_base_url,
  openai_model:
    normalizeText(data?.openai_model ?? DEFAULT_RUNTIME_BACKEND_SETTINGS.openai_model) ||
    DEFAULT_RUNTIME_BACKEND_SETTINGS.openai_model,
  openai_configured: Boolean(data?.openai_configured),
  xhs_configured: Boolean(data?.xhs_configured),
  amap_server_configured: Boolean(data?.amap_server_configured),
  google_server_configured: Boolean(data?.google_server_configured),
})

const emitRuntimeSettingsUpdated = () => {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(RUNTIME_SETTINGS_UPDATED_EVENT))
}

const apiClient = axios.create({
  timeout: 0, // 无超时限制，等待后端返回结果
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    config.baseURL = getRuntimeApiBaseUrl()
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    console.error('响应错误:', error.response?.status, error.message)
    const publicMessage = error.response?.data?.error?.message
    if (typeof publicMessage === 'string' && publicMessage.trim()) {
      error.message = publicMessage
    }
    return Promise.reject(error)
  }
)

export async function getBackendRuntimeSettings(): Promise<BackendRuntimeSettings> {
  try {
    const response = await apiClient.get<RuntimeSettingsApiResponse>('/api/settings')
    return normalizeBackendRuntimeSettings(response.data?.data)
  } catch (error: any) {
    console.error('读取运行时配置失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '读取配置失败')
  }
}

export async function updateBackendRuntimeSettings(
  updates: Partial<BackendRuntimeSettings>
): Promise<BackendRuntimeSettings> {
  try {
    const response = await apiClient.put<RuntimeSettingsApiResponse>('/api/settings', updates)
    return normalizeBackendRuntimeSettings(response.data?.data)
  } catch (error: any) {
    console.error('保存运行时配置失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '保存配置失败')
  }
}

export async function getRuntimeSettings(): Promise<RuntimeSettings> {
  const backend = await getBackendRuntimeSettings()
  const apiBaseUrl = getRuntimeApiBaseUrl()
  const mapJsKey = getRuntimeMapJsKey() || backend.vite_amap_web_js_key

  return {
    api_base_url: apiBaseUrl,
    ...backend,
    vite_amap_web_js_key: mapJsKey,
  }
}

export async function saveRuntimeSettings(settings: RuntimeSettings): Promise<RuntimeSettings> {
  const previousApiBaseUrl = getRuntimeApiBaseUrl()
  const targetApiBaseUrl = normalizeBaseUrl(settings.api_base_url) || previousApiBaseUrl
  const updates: Partial<BackendRuntimeSettings> = {
    vite_amap_web_js_key: settings.vite_amap_web_js_key,
    openai_base_url: settings.openai_base_url,
    openai_model: settings.openai_model,
  }
  setRuntimeApiBaseUrl(targetApiBaseUrl)

  let backend: BackendRuntimeSettings
  try {
    backend = await updateBackendRuntimeSettings(updates)
  } catch (error) {
    setRuntimeApiBaseUrl(previousApiBaseUrl)
    throw error
  }

  const apiBaseUrl = setRuntimeApiBaseUrl(targetApiBaseUrl)
  const mapJsKey = setRuntimeMapJsKey(settings.vite_amap_web_js_key || backend.vite_amap_web_js_key)
  emitRuntimeSettingsUpdated()

  return {
    api_base_url: apiBaseUrl,
    ...backend,
    vite_amap_web_js_key: mapJsKey || backend.vite_amap_web_js_key,
  }
}

/**
 * 提交旅行规划任务（立即返回 task_id）
 */
export async function submitTripPlan(formData: TripFormData): Promise<SubmitTripPlanResponse> {
  try {
    const response = await apiClient.post('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('提交旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || error.message || t('api.submitTripPlanFailed'))
  }
}

/** 将特殊要求解析为可确认的最小 Preference Profile。 */
export async function parsePreferenceProfile(
  request: PreferenceParseRequest
): Promise<PreferenceParseResponse> {
  try {
    const response = await apiClient.post<PreferenceParseResponse>('/api/preferences/parse', request)
    return response.data
  } catch (error: any) {
    console.error('解析旅行偏好失败:', error)
    throw new Error(error.response?.data?.detail || error.message || t('api.parsePreferenceFailed'))
  }
}

/**
 * 轮询任务状态
 */
export async function pollTaskStatus(taskId: string): Promise<TripTaskStatusResponse> {
  try {
    const response = await apiClient.get<TripTaskStatusResponse>(`/api/trip/status/${taskId}`)
    return response.data
  } catch (error: any) {
    console.error('查询任务状态失败:', error)
    throw new Error(error.response?.data?.detail || error.message || t('api.queryTaskStatusFailed'))
  }
}

/** Load the pre-generated public example without creating a planning task. */
export async function getExampleTrip(): Promise<PortfolioExampleTrip> {
  try {
    const response = await apiClient.get<PortfolioExampleTrip>('/api/demo/example-trip')
    const payload = response.data
    if (
      payload?.example !== true
      || payload.schema_version !== 'portfolio.example_trip.v1'
      || !payload.result?.success
      || !payload.result.data
    ) {
      throw new Error(t('api.exampleTripInvalid'))
    }
    return payload
  } catch (error: any) {
    const message = error?.response?.data?.error?.message || error?.message
    throw new Error(message || t('api.exampleTripUnavailable'))
  }
}

export async function patchTripPlan(
  taskId: string,
  instruction: string,
  currentPlanVersion: number,
  patchRequestId: string,
): Promise<TripPatchResult> {
  try {
    const response = await apiClient.post<TripPatchResult>(`/api/trip/${taskId}/patch`, {
      instruction,
      current_plan_version: currentPlanVersion,
      patch_request_id: patchRequestId,
    })
    return response.data
  } catch (error: any) {
    if (error.response?.status === 409) {
      throw new Error(error.response?.data?.detail || t('result.chat.versionConflict'))
    }
    throw new Error(error.response?.data?.detail || error.message || t('result.chat.patchFailed'))
  }
}

export async function getTripHistory(limit = 8): Promise<TripHistoryItem[]> {
  try {
    const response = await apiClient.get<TripHistoryResponse>('/api/trip/history', {
      params: { limit },
    })
    return Array.isArray(response.data?.items) ? response.data.items : []
  } catch (error: any) {
    console.error('查询历史计划失败:', error)
    throw new Error(error.response?.data?.detail || error.message || t('api.queryTaskStatusFailed'))
  }
}

/**
 * 生成旅行计划（兼容旧接口，内部使用轮询）
 */
export async function generateTripPlan(
  formData: TripFormData,
  options?: GenerateTripPlanOptions
): Promise<TripPlanResponse> {
  const task = await submitTripPlan(formData)
  options?.onTaskCreated?.(task)

  const wsUrl = task.ws_url.startsWith('ws://') || task.ws_url.startsWith('wss://')
    ? task.ws_url
    : `${getWsBaseUrl()}${task.ws_url}`

  return monitorTripTask(task, wsUrl, { onTaskEvent: options?.onTaskEvent }, {
    createWebSocket: url => new WebSocket(url),
    fetchStatus: pollTaskStatus,
  })
}

/** 恢复刷新或断线前已经提交的任务。 */
export async function resumeTripPlan(
  taskId: string,
  options?: Pick<GenerateTripPlanOptions, 'onTaskEvent'>,
): Promise<TripPlanResponse> {
  return monitorTripTask(
    { task_id: taskId, plan_id: taskId, ws_url: '' },
    '',
    { onTaskEvent: options?.onTaskEvent, useWebSocket: false },
    {
      createWebSocket: url => new WebSocket(url),
      fetchStatus: pollTaskStatus,
    },
  )
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || t('api.healthCheckFailed'))
  }
}

export default apiClient
