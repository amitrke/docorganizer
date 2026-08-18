import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Hero } from '../components/Hero'
import { Flash } from '../components/Flash'
import {
  ApiError,
  activateAiProfile,
  createAiProfile,
  deleteAiProfile,
  getMeta,
  listAiProfiles,
  testAiProfile,
  testAiProfileDraft,
  updateAiProfile,
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
    setFlash({ message: `"${selected.name}" is now active`, kind: 'success' })
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
      <Hero title="AI Settings" subtitle="Save multiple AI provider configs and switch which one is active." active="settings" />
      <Flash message={flash?.message ?? null} kind={flash?.kind} />

      <div className="card">
        <h1>AI Provider Configs</h1>
        <p className="hint">
          Each config remembers its own provider, model, and API key. Only one is active at a time — that's
          the one "Ask AI" uses.
        </p>

        <ul className="category-list" style={{ marginBottom: 18 }}>
          {(profiles?.length ?? 0) === 0 && <li className="hint">No AI configs yet — create one below.</li>}
          {profiles?.map((p: AiProfile) => (
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
              {p.is_active && <span className="badge st-filed">Active</span>}
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
              <button className="btn subtle" onClick={handleActivate}>Activate</button>
            )}
            {selected && (
              <button className="btn danger" onClick={handleDelete}>Delete</button>
            )}
          </div>

          {testResult && <div style={{ marginTop: 12 }}><Flash message={testResult.message} kind={testResult.ok ? 'success' : 'error'} /></div>}
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
