import type { WeatherInfo, WeatherResult } from '@/types'

export const getWeatherUnavailableDescription = (
  weatherDays: WeatherInfo[],
  results: WeatherResult[],
): string => {
  if (weatherDays.length > 0) return '天气数据暂不可用'

  const unsupportedOnly = results.length > 0 && results.every((result) => (
    !result.data_available
    && (result.primary_failure_reason ?? result.reason) === 'unsupported_location'
  ))

  return unsupportedOnly ? '当前天气源暂不覆盖该地区' : '天气数据暂不可用'
}
