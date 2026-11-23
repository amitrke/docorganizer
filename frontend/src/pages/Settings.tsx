import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Hero } from '../components/Hero'
import { Flash } from '../components/Flash'
import {
  ApiError,
  activateAiProfile,
  createAiProfile,
  deactivateAiProfile,
  deleteAiProfile,
  getAiQueue,
  getMeta,
  listAiProfiles,
  reorderAiProfiles,
  retryUnsummarised,
  updateAiSettings,
  testAiProfile,
  testAiProfileDraft,
  updateAiProfile,
  type AiMode,
  type AiProfile,
  type AiProfileInput,
} from '../api/client'

const PROVIDER_LABELS: Record<string, string> = {
  ollama: 'Ollama (local)',
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA (build.nvidia.com)',
  mistral: 'Mistral',
  deepseek: 'DeepSeek',
  gemini: 'Google Gemini',
  poe: 'Poe',
  custom: 'Custom (OpenAI-compatible)',
}
const PROVIDER_ORDER = ['ollama', 'openrouter', 'nvidia', 'mistral', 'deepseek', 'gemini', 'poe', 'custom']

const BLANK_FORM: AiProfileInput = {
  name: '', provider: 'ollama', model: '', base_url: '', api_key: '', timeout: 180, max_tokens: 1200,
}

export default function Settings() {
  const queryClient = useQueryClient()
  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: getMeta })
  const { data: profiles } = useQuery({ queryKey: ['ai-profiles'], queryFn: listAiProfiles })
  const { data: queue } = useQuery({ queryKey: ['ai-queue'], queryFn: getAiQueue, refetchInterval: 15000 })

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [hasAutoSelected, setHasAutoSelected] = useState(false)
  const [form, setForm] = useState<AiProfileInput>(BLANK_FORM)
  const [flash, setFlash] = useState<{ message: string; kind: 'success' | 'error' } | null>(null)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  const selected = profiles?.find((p) => p.id === selectedId) ?? null

  // On first load, open on the active profile (or the first one) instead of
  // always landing on a blank draft. Runs once — later refetches (after
  // activate/save/delete) must not fight the user's current selection.
  useEffect(() => {
    if (hasAutoSelected || !profiles) return
    if (profiles.length > 0) {
      const preferred = profiles.find((p) => p.is_active) ?? profiles[0]
      setSelectedId(preferred.id)
    }
    setHasAutoSelected(true)
  }, [profiles, hasAutoSelected])

  // Load the selected profile into the form; a brand-new draft uses meta's defaults.
  useEffect(() => {
    if (selected) {
      setForm({
        name: selected.name, provider: selected.provider, model: selected.model,
        base_url: selected.base_url, api_key: '', timeout: selected.timeout, max_tokens: selected.max_tokens,
      })
    } else if (meta) {
      setForm({ ...BLANK_FORM, ...meta.default_settings, name: '', api_key: '' })
    }
    setTestResult(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, selected?.id, meta])

  function refresh() {
    return queryClient.invalidateQueries({ queryKey: ['ai-profiles'] })
  }

  function startNewProfile() {
    setSelectedId(null)
  }

  function handleProviderChange(provider: string) {
    // No JS-free heuristics needed here (this is a real SPA) — just clear a
    // now-stale base URL directly when the provider changes.
    setForm((f) => ({ ...f, provider, base_url: '' }))
  }

  async function handleSave() {
    setSaving(true)
    setFlash(null)
    try {
      if (selected) {
        await updateAiProfile(selected.id, form)
      } else {
        const created = await createAiProfile(form)
        setSelectedId(created.id)
      }
      await refresh()
      setForm((f) => ({ ...f, api_key: '' }))
      setFlash({ message: 'Saved', kind: 'success' })
    } catch (err) {
      setFlash({ message: err instanceof ApiError ? err.message : 'Save failed', kind: 'error' })
    } finally {
      setSaving(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const result = selected ? await testAiProfile(selected.id, form) : await testAiProfileDraft(form)
      setTestResult(result)
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : 'Test failed' })
    } finally {
      setTesting(false)
    }
  }

  async function handleActivate() {
    if (!selected) return
    await activateAiProfile(selected.id)
    await refresh()
    setFlash({ message: `"${selected.name}" is now enabled`, kind: 'success' })
  }

  async function handleDeactivate() {
    if (!selected) return
    await deactivateAiProfile(selected.id)
    await refresh()
    setFlash({ message: `"${selected.name}" is now disabled`, kind: 'success' })
  }

  async function handleMove(id: number, delta: -1 | 1) {
    if (!profiles) return
    const ids = profiles.map((p) => p.id)
    const from = ids.indexOf(id)
    const to = from + delta
    if (to < 0 || to >= ids.length) return
    ;[ids[from], ids[to]] = [ids[to], ids[from]]
    await reorderAiProfiles(ids)
    await refresh()
  }

  async function handleQueueSettings(patch: Parameters<typeof updateAiSettings>[0]) {
    try {
      await updateAiSettings(patch)
      await queryClient.invalidateQueries({ queryKey: ['ai-queue'] })
    } catch (err) {
      setFlash({ message: err instanceof ApiError ? err.message : 'Could not save setting', kind: 'error' })
    }
  }

  async function handleRetryAll() {
    const { queued } = await retryUnsummarised()
    await queryClient.invalidateQueries({ queryKey: ['ai-queue'] })
    setFlash({ message: `${queued} document${queued === 1 ? '' : 's'} queued for AI retry`, kind: 'success' })
  }

  async function handleDelete() {
    if (!selected) return
    await deleteAiProfile(selected.id)
    setSelectedId(null)
    await refresh()
    setFlash({ message: 'Profile deleted', kind: 'success' })
  }

  const isCustom = form.provider === 'custom'
  const providerDefault = meta?.provider_defaults[form.provider]?.base_url ?? ''

  return (
    <>
      <Hero title="AI Settings" subtitle="Enable several AI provider configs, set their order, and control background retries." active="settings" />
      <Flash message={flash?.message ?? null} kind={flash?.kind} />

      <div className="card">
        <h1>AI Provider Configs</h1>
        <p className="hint">
          Each config remembers its own provider, model, and API key. Enable as many as you like — they form a
          pool used by "Ask AI" and by background retries, in the order shown (use the arrows to reorder).
        </p>

        <ul className="category-list" style={{ marginBottom: 18 }}>
          {(profiles?.length ?? 0) === 0 && <li className="hint">No AI configs yet — create one below.</li>}
          {profiles?.map((p: AiProfile, index) => (
            <li
              key={p.id}
              className={`profile-row${p.id === selectedId ? ' selected' : ''}`}
              onClick={() => setSelectedId(p.id)}
            >
              <span>
                <strong>{p.name}</strong>{' '}
                <span className="hint">
                  {PROVIDER_LABELS[p.provider] ?? p.provider}{p.model ? ` · ${p.model}` : ''}
                </span>
              </span>
              <span>
                <button className="btn subtle" title="Move up" disabled={index === 0}
                  onClick={(e) => { e.stopPropagation(); handleMove(p.id, -1) }}>↑</button>
                <button className="btn subtle" title="Move down" disabled={index === profiles.length - 1}
                  onClick={(e) => { e.stopPropagation(); handleMove(p.id, 1) }}>↓</button>
                {p.is_active
                  ? <span className="badge st-filed">Enabled</span>
                  : <span className="badge st-skipped">Disabled</span>}
              </span>
            </li>
          ))}
        </ul>

        <div className="toolbar">
          <button className="btn subtle" onClick={startNewProfile} disabled={selectedId === null}>
            + New config
          </button>
        </div>

        <div className="action-panel">
          <h3>{selected ? `Edit "${selected.name}"` : 'New config'}</h3>

          <div className="inline-form">
            <div className="field">
              <label>Name</label>
              <input
                type="text"
                placeholder="e.g. Local Ollama"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="field">
              <label>Provider</label>
              <select value={form.provider} onChange={(e) => handleProviderChange(e.target.value)}>
                {PROVIDER_ORDER.map((key) => (
                  <option key={key} value={key}>{PROVIDER_LABELS[key]}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="inline-form">
            <div className="field">
              <label>Model</label>
              <input
                type="text"
                placeholder="e.g. mistral:7b-instruct"
                value={form.model}
                onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
              />
            </div>
            <div className="field">
              <label>{isCustom ? 'Base URL (required for Custom)' : 'Base URL (optional)'}</label>
              <input
                type="text"
                placeholder={providerDefault || 'e.g. https://api.openai.com/v1'}
                value={form.base_url}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
              />
            </div>
          </div>

          <div className="inline-form">
            <div className="field">
              <label>API key</label>
              <input
                type="password"
                autoComplete="off"
                placeholder={selected?.has_key ? 'leave blank to keep saved key' : 'required for most hosted providers'}
                value={form.api_key}
                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
              />
            </div>
          </div>
          <p className="hint">
            {selected ? (selected.has_key ? 'A key is currently saved. Leave blank to keep it.' : 'No key saved yet.') : 'Leave blank for keyless local/custom servers.'}
            {' '}You can enter multiple keys separated by commas — on a 429 (rate limit) response, the next key is tried automatically.
          </p>

          <div className="inline-form">
            <div className="field">
              <label>Timeout (seconds)</label>
              <input type="number" min={5} value={form.timeout} onChange={(e) => setForm((f) => ({ ...f, timeout: Number(e.target.value) }))} />
            </div>
            <div className="field">
              <label>Max response tokens</label>
              <input type="number" min={16} value={form.max_tokens} onChange={(e) => setForm((f) => ({ ...f, max_tokens: Number(e.target.value) }))} />
            </div>
          </div>

          <div className="inline-form" style={{ marginTop: 14 }}>
            <button onClick={handleSave} disabled={saving || !form.name.trim()}>
              {saving ? 'Saving…' : selected ? 'Save changes' : 'Create config'}
            </button>
            <button onClick={handleTest} disabled={testing}>{testing ? 'Testing…' : 'Test connection'}</button>
            {selected && !selected.is_active && (
              <button className="btn subtle" onClick={handleActivate}>Enable</button>
            )}
            {selected && selected.is_active && (
              <button className="btn subtle" onClick={handleDeactivate}>Disable</button>
            )}
            {selected && (
              <button className="btn danger" onClick={handleDelete}>Delete</button>
            )}
          </div>

          {testResult && <div style={{ marginTop: 12 }}><Flash message={testResult.message} kind={testResult.ok ? 'success' : 'error'} /></div>}
        </div>

        <div className="action-panel">
          <h3>Background AI</h3>
          <p className="hint">
            Documents with no AI summary are retried automatically with exponential backoff, using the enabled
            profiles above.
          </p>
          <div className="inline-form">
            <div className="field">
              <label>Profile mode</label>
              <select
                value={queue?.settings.mode ?? 'fallback'}
                onChange={(e) => handleQueueSettings({ mode: e.target.value as AiMode })}
              >
                <option value="fallback">Fallback — try in order until one works</option>
                <option value="round_robin">Round-robin — spread documents across profiles</option>
                <option value="consensus">Consensus — two models must agree on the category</option>
              </select>
            </div>
            <div className="field">
              <label>Max attempts per document</label>
              <input
                type="number" min={1}
                defaultValue={queue?.settings.max_attempts ?? 5}
                key={queue?.settings.max_attempts}
                onBlur={(e) => handleQueueSettings({ max_attempts: Math.max(1, Number(e.target.value) || 1) })}
              />
            </div>
          </div>
          <p>
            <label>
              <input
                type="checkbox"
                checked={queue?.settings.background_enabled ?? true}
                onChange={(e) => handleQueueSettings({ background_enabled: e.target.checked })}
              />{' '}Run background retries
            </label>
            <br />
            <label>
              <input
                type="checkbox"
                checked={queue?.settings.auto_apply ?? false}
                onChange={(e) => handleQueueSettings({ auto_apply: e.target.checked })}
              />{' '}Apply suggestions automatically (refiles by date and category). Off = store them for review on each document.
            </label>
          </p>
          <div className="inline-form">
            <span>
              <strong>{queue?.waiting ?? 0}</strong> waiting
              {' · '}<strong>{queue?.exhausted ?? 0}</strong> gave up
              {' · '}<strong>{queue?.pending_review ?? 0}</strong> awaiting review
            </span>
            <button onClick={handleRetryAll}>Retry AI for all unsummarised</button>
          </div>
        </div>

        <p className="hint" style={{ marginTop: 8 }}>
          API keys: <a href="https://openrouter.ai" target="_blank" rel="noopener noreferrer">OpenRouter</a>
          {' '}&middot;{' '}<a href="https://build.nvidia.com" target="_blank" rel="noopener noreferrer">NVIDIA</a>
          {' '}&middot;{' '}<a href="https://console.mistral.ai/api-keys" target="_blank" rel="noopener noreferrer">Mistral</a>
          {' '}&middot;{' '}<a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer">DeepSeek</a>
          {' '}&middot;{' '}<a href="https://makersuite.google.com/app/apikey" target="_blank" rel="noopener noreferrer">Gemini</a>
          {' '}&middot;{' '}<a href="https://poe.com/api_key" target="_blank" rel="noopener noreferrer">Poe</a>
        </p>
        <p className="hint">
          "Custom" points at any OpenAI-compatible <code>/chat/completions</code> endpoint — official OpenAI,
          a self-hosted LiteLLM/vLLM/llama.cpp/LM Studio server, or similar. Local servers usually need no API key.
        </p>
      </div>
    </>
  )
}
