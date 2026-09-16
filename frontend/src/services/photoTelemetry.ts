export type RemotePhotoSource = 'google_places' | 'xhs'
export type PhotoRenderOutcome = 'load_success' | 'load_error'
export type PhotoSurface = 'overview' | 'day'

export type PhotoClientEvent = {
  request_id: string
  source: RemotePhotoSource
  outcome: PhotoRenderOutcome
  surface: PhotoSurface
}

const PHOTO_REQUEST_ID_PATTERN = /^[0-9a-f]{12}$/

export const isPhotoRequestId = (value: unknown): value is string =>
  typeof value === 'string' && PHOTO_REQUEST_ID_PATTERN.test(value)

export const isRemotePhotoSource = (value: unknown): value is RemotePhotoSource =>
  value === 'google_places' || value === 'xhs'

export const clearFailedPhotoState = <TAttribution>(
  name: string,
  photos: Record<string, string>,
  attributions: Record<string, TAttribution[]>,
  sources: Record<string, RemotePhotoSource | 'placeholder'>,
  reasons: Record<string, string | undefined>,
): void => {
  delete photos[name]
  delete attributions[name]
  sources[name] = 'placeholder'
  reasons[name] = 'browser_load_error'
}

export const reportPhotoClientEvent = async (
  apiBase: string,
  event: PhotoClientEvent,
  reported: Set<string>,
  fetchImpl: typeof fetch = fetch,
): Promise<boolean> => {
  if (
    !isPhotoRequestId(event.request_id)
    || !isRemotePhotoSource(event.source)
    || !['load_success', 'load_error'].includes(event.outcome)
    || !['overview', 'day'].includes(event.surface)
  ) {
    return false
  }

  // One terminal browser outcome per request and rendered surface.
  const key = `${event.request_id}:${event.source}:${event.surface}`
  if (reported.has(key)) return false
  reported.add(key)

  try {
    const response = await fetchImpl(`${apiBase}/api/poi/photo/client-event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(event),
      keepalive: true,
    })
    return response.ok
  } catch {
    // Observability must never affect image rendering or fallback behavior.
    return false
  }
}
