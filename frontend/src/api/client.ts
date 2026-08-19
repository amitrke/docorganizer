export interface Document {
  id: number
  filename: string
  filepath: string
  content_hash: string | null
  file_size: number | null
  detected_date: string | null
  category: string | null
  ai_suggested_category: string | null
  classification_source: string
  ai_rationale: string | null
  ai_summary: string | null
  extracted_fields: Record<string, string>
  primary_person: string | null
  issuing_organization: string | null
  reference_number: string | null
  amount: string | null
  effective_date: string | null
  expiry_date: string | null
  archived_at: string | null
  archived_reason: string | null
  archived: boolean
  filing_status: string
  last_reviewed_at: string | null
  skipped: boolean
  created_at: string
  file_exists?: boolean
}

export interface DocumentListResponse {
  items: Document[]
  total: number
  page: number
  page_size: number
  categories: string[]
}

export interface AiSuggestion {
  date: string | null
  category: string | null
  rationale: string
  summary: string
  fields: Record<string, string>
  primary_person: string | null
  issuing_organization: string | null
  reference_number: string | null
  amount: string | null
  effective_date: string | null
  expiry_date: string | null
}

export interface AskAiResponse {
  suggestion: AiSuggestion | null
  error: string | null
}

const COMMON_FIELD_LABELS: [key: keyof AiSuggestion, label: string][] = [
  ['primary_person', 'Primary person'],
  ['issuing_organization', 'Issuing organization'],
  ['reference_number', 'Reference number'],
  ['amount', 'Amount'],
  ['effective_date', 'Effective date'],
  ['expiry_date', 'Expiry date'],
]

export function commonFieldEntries(source: Pick<AiSuggestion,
  'primary_person' | 'issuing_organization' | 'reference_number' | 'amount' | 'effective_date' | 'expiry_date'
>): [string, string][] {
  return COMMON_FIELD_LABELS
    .map(([key, label]) => [label, source[key]] as [string, string | null])
    .filter((pair): pair is [string, string] => Boolean(pair[1]))
}

export interface UploadResult {
  status: 'filed' | 'duplicate' | 'not_pdf' | 'failed' | string
  filename: string
  doc_id?: number
  dest?: string
  detected_date?: string | null
  category?: string | null
  path?: string
  message?: string
}

export interface CategoriesResponse {
  configured: string[]
  db_only: string[]
}

export interface ProviderDefault {
  base_url: string
  requires_key: boolean
}

export interface DefaultAiSettings {
  provider: string
  model: string
  base_url: string
  timeout: number
  max_tokens: number
}

export interface MetaResponse {
  provider_defaults: Record<string, ProviderDefault>
  default_settings: DefaultAiSettings
}

export interface AiProfile {
  id: number
  name: string
  provider: string
  model: string
  base_url: string
  timeout: number
  max_tokens: number
  is_active: boolean
  has_key: boolean
}

export interface AiProfileInput {
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string
  timeout: number
  max_tokens: number
}

export interface TestConnectionResult {
  ok: boolean
  message: string
}

export interface BulkAskAiEvent {
  doc_id: number
  status: 'suggested' | 'error'
  suggestion?: AiSuggestion
  error?: string
}

class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) {
    return undefined as T
  }
  return res.json() as Promise<T>
}

export interface DocumentListParams {
  q?: string
  status?: string
  category?: string
  date_from?: string
  date_to?: string
  expiry_from?: string
  expiry_to?: string
  amount_min?: number
  amount_max?: number
  person?: string
  organization?: string
  reference_number?: string
  sort_by?: string
  sort_dir?: 'asc' | 'desc'
  page?: number
}

export function listDocuments(params: DocumentListParams): Promise<DocumentListResponse> {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.status && params.status !== 'all') search.set('status', params.status)
  if (params.category) search.set('category', params.category)
  if (params.date_from) search.set('date_from', params.date_from)
  if (params.date_to) search.set('date_to', params.date_to)
  if (params.expiry_from) search.set('expiry_from', params.expiry_from)
  if (params.expiry_to) search.set('expiry_to', params.expiry_to)
  if (params.amount_min !== undefined) search.set('amount_min', String(params.amount_min))
  if (params.person) search.set('person', params.person)
  if (params.organization) search.set('organization', params.organization)
  if (params.reference_number) search.set('reference_number', params.reference_number)
  if (params.amount_max !== undefined) search.set('amount_max', String(params.amount_max))
  if (params.sort_by) search.set('sort_by', params.sort_by)
  if (params.sort_dir) search.set('sort_dir', params.sort_dir)
  search.set('page', String(params.page ?? 1))
  return request(`/documents?${search.toString()}`)
}

export function getDocument(id: number): Promise<Document> {
  return request(`/documents/${id}`)
}

export function patchDocument(id: number, patch: { detected_date?: string; category?: string }): Promise<Document> {
  return request(`/documents/${id}`, { method: 'PATCH', body: JSON.stringify(patch) })
}

export function askAi(id: number): Promise<AskAiResponse> {
  return request(`/documents/${id}/ask-ai`, { method: 'POST' })
}

export function applyAi(id: number, suggestion: AiSuggestion): Promise<Document> {
  return request(`/documents/${id}/apply-ai`, {
    method: 'POST',
    body: JSON.stringify({
      detected_date: suggestion.date,
      category: suggestion.category,
      rationale: suggestion.rationale,
      summary: suggestion.summary,
      fields: suggestion.fields,
      primary_person: suggestion.primary_person,
      issuing_organization: suggestion.issuing_organization,
      reference_number: suggestion.reference_number,
      amount: suggestion.amount,
      effective_date: suggestion.effective_date,
      expiry_date: suggestion.expiry_date,
    }),
  })
}

export function refileDocument(id: number): Promise<Document> {
  return request(`/documents/${id}/refile`, { method: 'POST' })
}

export function skipDocument(id: number): Promise<Document> {
  return request(`/documents/${id}/skip`, { method: 'POST' })
}

export function archiveDocument(id: number): Promise<Document> {
  return request(`/documents/${id}/archive`, { method: 'POST' })
}

export function unarchiveDocument(id: number): Promise<Document> {
  return request(`/documents/${id}/unarchive`, { method: 'POST' })
}

export function bulkArchive(docIds: number[]): Promise<{ archived_ids: number[] }> {
  return request('/documents/bulk-archive', { method: 'POST', body: JSON.stringify({ doc_ids: docIds }) })
}

export function deleteDocument(id: number): Promise<void> {
  return request(`/documents/${id}`, { method: 'DELETE' })
}

export function uploadFile(file: File, onProgress?: (pct: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/upload')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch {
          reject(new Error('Invalid response from server'))
        }
      } else {
        reject(new Error(`Upload failed (${xhr.status})`))
      }
    }
    xhr.onerror = () => reject(new Error('Upload failed'))
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

export function bulkAskAi(docIds: number[], onEvent: (event: BulkAskAiEvent | { status: 'done' }) => void): () => void {
  const controller = new AbortController()
  fetch('/api/documents/bulk-ask-ai', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_ids: docIds }),
    signal: controller.signal,
  }).then(async (res) => {
    if (!res.body) return
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        const payload = JSON.parse(line.slice(5).trim())
        onEvent(payload)
      }
    }
  }).catch((err) => {
    if (err?.name !== 'AbortError') {
      console.error('bulk-ask-ai stream failed', err)
    }
  })
  return () => controller.abort()
}

export function listCategories(): Promise<CategoriesResponse> {
  return request('/categories')
}

export function addCategory(name: string): Promise<{ name: string }> {
  return request('/categories', { method: 'POST', body: JSON.stringify({ name }) })
}

export function removeCategory(name: string): Promise<void> {
  return request(`/categories/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export function listAiProfiles(): Promise<AiProfile[]> {
  return request('/ai-profiles')
}

export function createAiProfile(payload: AiProfileInput): Promise<AiProfile> {
  return request('/ai-profiles', { method: 'POST', body: JSON.stringify(payload) })
}

export function updateAiProfile(id: number, payload: Partial<AiProfileInput>): Promise<AiProfile> {
  return request(`/ai-profiles/${id}`, { method: 'PATCH', body: JSON.stringify(payload) })
}

export function deleteAiProfile(id: number): Promise<void> {
  return request(`/ai-profiles/${id}`, { method: 'DELETE' })
}

export function activateAiProfile(id: number): Promise<AiProfile> {
  return request(`/ai-profiles/${id}/activate`, { method: 'POST' })
}

export function testAiProfileDraft(payload: AiProfileInput): Promise<TestConnectionResult> {
  return request('/ai-profiles/test', { method: 'POST', body: JSON.stringify(payload) })
}

export function testAiProfile(id: number, payload: AiProfileInput): Promise<TestConnectionResult> {
  return request(`/ai-profiles/${id}/test`, { method: 'POST', body: JSON.stringify(payload) })
}

export function getMeta(): Promise<MetaResponse> {
  return request('/meta')
}

export { ApiError }
