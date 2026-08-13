// 类型定义

export interface CityStay {
  city: string
  days: number
}

export type PartyType = 'solo' | 'couple' | 'friends' | 'family' | 'with_parents' | 'with_children'
export type TravelPace = 'intensive' | 'balanced' | 'relaxed'

export interface PreferenceConstraints {
  avoid_early_start: boolean
  earliest_start_time?: string | null
  mobility_notes: string[]
  food_notes: string[]
  other_notes: string[]
}

export interface PreferenceProfile {
  party_type: PartyType
  party_size: number
  budget_cny?: number | null
  pace: TravelPace
  interests: string[]
  special_requirements: string
  constraints: PreferenceConstraints
  inferred_interests: string[]
  parsing_notes: string[]
}

export interface PreferenceParseRequest {
  party_type: PartyType
  party_size: number
  budget_cny?: number | null
  pace: TravelPace
  interests: string[]
  special_requirements: string
  generation_id?: string
}

export interface PreferenceParseResponse {
  success: boolean
  profile: PreferenceProfile
  used_llm: boolean
  message: string
  generation_id?: string
}

export interface Location {
  longitude: number
  latitude: number
}

export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number
  poi_id?: string
  place_id?: string
  poi_match_status?: 'verified' | 'partial_match' | 'unverified'
  map_data_source?: 'google_places' | 'amap' | 'llm_unverified'
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
}

export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total_inter_city_transport?: number
  total: number
}

export interface DayPlan {
  date: string
  day_index: number
  start_time?: string | null
  city?: string
  is_transfer_day?: boolean
  transfer_info?: string
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
}

export interface WeatherInfo {
  date: string
  city?: string
  day_weather: string
  night_weather: string
  day_temp: number | null
  night_temp: number | null
  wind_direction: string
  wind_power: string
  precipitation_probability?: number | null
  data_source?: 'google_weather' | 'amap' | 'llm_general'
  verification_status?: 'verified' | 'partial' | 'unverified' | 'unavailable'
  degraded?: boolean
}

export interface WeatherResult {
  provider: 'google_weather' | 'amap' | 'unavailable'
  city?: string
  request_success: boolean
  data_available: boolean
  degraded: boolean
  reason?: string | null
  days: WeatherInfo[]
}

export interface TripPlan {
  city: string
  cities?: string[]
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  weather_results?: WeatherResult[]
  overall_suggestions: string
  budget?: Budget
  risks?: RiskItem[]
  validation_status?: 'passed' | 'issues_found' | 'degraded' | null
  revision_count?: 0 | 1
  revision_summary?: string | null
  plan_version?: number
}

export type TripPatchOperationType =
  | 'replace_poi'
  | 'remove_poi'
  | 'add_poi'
  | 'update_start_time'
  | 'update_transport'
  | 'update_meal'
  | 'update_day_pace'

export interface TripChangeDiff {
  changed_day_indices: number[]
  changed_fields: string[]
  added_pois: string[]
  removed_pois: string[]
  replaced_pois: string[]
  unchanged_day_indices: number[]
}

export interface TripPatchResult {
  success: boolean
  updated_plan?: TripPlan | null
  graph_data?: KnowledgeGraphData | null
  changed_day_indices: number[]
  change_summary: string[]
  diff?: TripChangeDiff | null
  validation_status?: 'passed' | 'issues_found' | 'degraded' | null
  risks: RiskItem[]
  requires_regeneration: boolean
  regeneration_reason?: string | null
  error?: string | null
  plan_version: number
  patch_request_id: string
}

export type RiskSeverity = 'info' | 'warning' | 'blocking'
export type RiskType = 'earliest_start' | 'mobility' | 'budget' | 'route_feasibility' | 'validation_unavailable' | 'pacing'

export interface RiskItem {
  id: string
  type: RiskType
  severity: RiskSeverity
  day_index?: number | null
  related_poi_names: string[]
  title: string
  message: string
  evidence: Record<string, unknown>
  suggestion: string
  source: 'rule_validator'
  revisable: boolean
}

export interface TripFormData {
  city: string
  cities?: CityStay[]
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
  language?: string
  preference_profile?: PreferenceProfile
  generation_id?: string
}

export interface TripPlanResponse {
  success: boolean
  message: string
  plan_id?: string
  data?: TripPlan
  graph_data?: KnowledgeGraphData
}

export interface PortfolioExampleTrip {
  schema_version: 'portfolio.example_trip.v1'
  example: true
  generated_at: string
  source_semantics: string
  demo_request: {
    destination: string
    dates: string
    travelers: string
    pace: TravelPace
    interests: string[]
    transportation: string
  }
  grounding_summary: {
    status: 'verified' | 'partial' | 'unverified'
    message: string
  }
  route_uncertainty: string
  pacing_summary: string
  result: TripPlanResponse
}

export interface TripHistoryItem {
  plan_id: string
  task_id: string
  city: string
  start_date: string
  end_date: string
  travel_days: number
  updated_at: string
  overall_suggestions?: string
}

export type TripTaskStatus = 'processing' | 'completed' | 'failed'

export type TripTaskStage =
  | 'submitted'
  | 'initializing'
  | 'attraction_search'
  | 'weather_search'
  | 'hotel_search'
  | 'planning'
  | 'validating'
  | 'critic'
  | 'revising'
  | 'revalidating'
  | 'graph_building'
  | 'completed'
  | 'failed'

export interface TripTaskEvent {
  task_id: string
  plan_id: string
  status: TripTaskStatus
  stage: TripTaskStage
  progress: number
  message: string
  error?: string
  result?: TripPlanResponse
}

export interface TripTaskStatusResponse {
  task_id: string
  plan_id: string
  status: TripTaskStatus
  stage?: TripTaskStage
  progress?: number
  progress_text?: string
  message?: string
  error?: string
  result?: TripPlanResponse
  request_payload?: TripFormData
}

export interface BackendRuntimeSettings {
  vite_amap_web_js_key: string
  google_maps_proxy_configured: boolean
  openai_base_url: string
  openai_model: string
  openai_configured: boolean
  xhs_configured: boolean
  amap_server_configured: boolean
  google_server_configured: boolean
}

export interface RuntimeSettings {
  api_base_url: string
  vite_amap_web_js_key: string
  google_maps_proxy_configured: boolean
  openai_base_url: string
  openai_model: string
  openai_configured: boolean
  xhs_configured: boolean
  amap_server_configured: boolean
  google_server_configured: boolean
}

// ============ 知识图谱类型 ============

export interface GraphNode {
  id: string
  name: string
  category: number
  symbolSize: number
  itemStyle?: { color: string }
  value?: string
}

export interface GraphEdge {
  source: string
  target: string
  label?: string
}

export interface GraphCategory {
  name: string
}

export interface KnowledgeGraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  categories: GraphCategory[]
}

// ============ AI 行程问答类型 ============

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface TripChatRequest {
  message: string
  trip_plan: object
  history: ChatMessage[]
}

export interface TripChatResponse {
  success: boolean
  reply: string
}
