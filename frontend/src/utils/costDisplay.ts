import type { TripPlanResponse, TaskCostDisplay } from '@/types'

type Translate = (key: string, args?: Record<string, string | number>) => string
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const CACHE_KEY = 'tripstar.taskCostDisplay'

const positiveAmount = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0

const validDisplay = (value: unknown): value is TaskCostDisplay => {
  if (!value || typeof value !== 'object') return false
  const budget = (value as TaskCostDisplay).budget_cny
  return budget === null || positiveAmount(budget)
}

const amountText = (value: number) => String(Math.round((value + Number.EPSILON) * 100) / 100)

// Only server response metadata associated with this task may supply its cap.
// Plan/model fields and the last form summary are deliberately not consulted.
export const responseTaskCostDisplay = (
  planId: string, response?: Pick<TripPlanResponse, 'plan_id' | 'cost_display'> | null,
): TaskCostDisplay | null => planId && response?.plan_id === planId && validDisplay(response.cost_display)
  ? { budget_cny: response.cost_display.budget_cny } : null

export const cacheTaskCostDisplay = (storage: StorageLike, response: TripPlanResponse) => {
  const display = responseTaskCostDisplay(response.plan_id || '', response)
  if (display) storage.setItem(CACHE_KEY, JSON.stringify({ plan_id: response.plan_id, cost_display: display }))
  else storage.removeItem(CACHE_KEY)
}

export const readTaskCostDisplay = (storage: StorageLike, planId: string): TaskCostDisplay | null => {
  try { return responseTaskCostDisplay(planId, JSON.parse(storage.getItem(CACHE_KEY) || 'null')) }
  catch { return null }
}

export const referenceCost = (value: unknown, t: Translate, perNight = false): string =>
  positiveAmount(value)
    ? t(perNight ? 'result.cost.hotelEstimate' : 'result.cost.estimate', { amount: amountText(value) })
    : t('result.cost.unknown')

export const budgetOverview = (display: TaskCostDisplay | null, total: unknown, t: Translate) => {
  const cap = display?.budget_cny
  const capText = positiveAmount(cap)
    ? `${t('result.cost.budgetCap')}：${t('result.cost.cnyAmount', { amount: amountText(cap) })}`
    : t(display && cap === null ? 'result.cost.capUnset' : 'result.cost.capUnknown')
  const totalText = `${t('result.cost.referenceTotal')}：${positiveAmount(total)
    ? t('result.cost.cnyAmount', { amount: amountText(total) }) : t('result.cost.unknown')}`
  let comparison = ''
  if (positiveAmount(cap) && positiveAmount(total)) {
    comparison = t(total > cap ? 'result.cost.aboveCap' : total === cap ? 'result.cost.atCap' : 'result.cost.belowCap')
  }
  return { cap: capText, total: totalText, comparison }
}
