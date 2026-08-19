import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Hero } from '../components/Hero'
import { Flash } from '../components/Flash'
import { ConfirmDialog } from '../components/ConfirmDialog'
import {
  applyAi,
  archiveDocument,
  askAi,
  commonFieldEntries,
  deleteDocument,
  getDocument,
  patchDocument,
  refileDocument,
  skipDocument,
  unarchiveDocument,
  type AiSuggestion,
} from '../api/client'

function fmt(value: string | null | undefined, fallback = '(none)'): string {
  const text = (value ?? '').trim()
  return text || fallback
}

export default function Detail() {
  const { id } = useParams()
  const docId = Number(id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [dateInput, setDateInput] = useState('')
  const [categoryInput, setCategoryInput] = useState('')
  const [dirtyDate, setDirtyDate] = useState(false)
  const [dirtyCategory, setDirtyCategory] = useState(false)
  const [suggestion, setSuggestion] = useState<AiSuggestion | null>(null)
  const [aiError, setAiError] = useState<string | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [flash, setFlash] = useState<{ message: string; kind: 'success' | 'error' } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const { data: doc, isLoading } = useQuery({
    queryKey: ['document', docId],
    queryFn: () => getDocument(docId),
  })

  function refetchDoc() {
    return queryClient.invalidateQueries({ queryKey: ['document', docId] })
  }

  const patchMutation = useMutation({
    mutationFn: (patch: { detected_date?: string; category?: string }) => patchDocument(docId, patch),
    onSuccess: () => {
      refetchDoc()
      setDirtyDate(false)
      setDirtyCategory(false)
      setFlash({ message: 'Saved', kind: 'success' })
    },
    onError: (err) => setFlash({ message: err instanceof Error ? err.message : 'Save failed', kind: 'error' }),
  })

  const refileMutation = useMutation({
    mutationFn: () => refileDocument(docId),
    onSuccess: () => { refetchDoc(); setFlash({ message: 'Re-filed', kind: 'success' }) },
    onError: (err) => setFlash({ message: err instanceof Error ? err.message : 'Refile failed', kind: 'error' }),
  })

  const skipMutation = useMutation({
    mutationFn: () => skipDocument(docId),
    onSuccess: () => { refetchDoc(); setFlash({ message: 'Marked skipped', kind: 'success' }) },
  })

  const archiveMutation = useMutation({
    mutationFn: () => archiveDocument(docId),
    onSuccess: () => { refetchDoc(); setFlash({ message: 'Archived', kind: 'success' }) },
  })

  const unarchiveMutation = useMutation({
    mutationFn: () => unarchiveDocument(docId),
    onSuccess: () => { refetchDoc(); setFlash({ message: 'Unarchived', kind: 'success' }) },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteDocument(docId),
    onSuccess: () => navigate('/?msg=Document+deleted'),
  })

  const applyMutation = useMutation({
    mutationFn: (s: AiSuggestion) => applyAi(docId, s),
    onSuccess: () => {
      refetchDoc()
      setSuggestion(null)
      setFlash({ message: 'AI suggestion applied', kind: 'success' })
    },
  })

  async function handleAskAi() {
    setAiLoading(true)
    setAiError(null)
    setSuggestion(null)
    try {
      const res = await askAi(docId)
      if (res.suggestion) setSuggestion(res.suggestion)
      else setAiError(res.error || 'AI suggestion unavailable.')
    } finally {
      setAiLoading(false)
    }
  }

  if (isLoading || !doc) {
    return (
      <>
        <Hero title="Document" subtitle="Loading…" active="home" />
        <div className="card"><div className="empty">Loading…</div></div>
      </>
    )
  }

  const currentDate = dirtyDate ? dateInput : doc.detected_date ?? ''
  const currentCategory = dirtyCategory ? categoryInput : doc.category ?? ''

  return (
    <>
      <Hero title={`Document #${doc.id}`} subtitle={doc.filename} active="home" />
      <Flash message={flash?.message ?? null} kind={flash?.kind} />

      <div className="card">
        <div className="detail-header">
          {doc.file_exists && (
            <a href={`/api/documents/${doc.id}/content`} target="_blank" rel="noopener noreferrer">
              <img
                className="doc-thumb-large"
                src={`/api/documents/${doc.id}/thumbnail`}
                alt=""
                onError={(e) => { e.currentTarget.style.display = 'none' }}
              />
            </a>
          )}
          <h1>Document #{doc.id} - {fmt(doc.filename)}</h1>
        </div>

        {(doc.ai_rationale || doc.ai_summary || commonFieldEntries(doc).length > 0 || Object.keys(doc.extracted_fields).length > 0) && (
          <div className="ai-stack">
            {commonFieldEntries(doc).length > 0 && (
              <section className="ai-card">
                <strong>Common fields</strong>
                <dl className="field-grid">
                  {commonFieldEntries(doc).map(([k, v]) => (
                    <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
                  ))}
                </dl>
              </section>
            )}
            {doc.ai_rationale && (
              <section className="ai-card"><strong>AI rationale</strong><p>{doc.ai_rationale}</p></section>
            )}
            {doc.ai_summary && (
              <section className="ai-card"><strong>Detailed summary</strong><p>{doc.ai_summary}</p></section>
            )}
            {Object.keys(doc.extracted_fields).length > 0 && (
              <section className="ai-card">
                <strong>Extracted fields</strong>
                <dl className="field-grid">
                  {Object.entries(doc.extracted_fields).map(([k, v]) => (
                    <div key={k}><dt>{k.replaceAll('_', ' ')}</dt><dd>{v}</dd></div>
                  ))}
                </dl>
              </section>
            )}
          </div>
        )}

        <dl>
          <dt>Date</dt><dd>{fmt(doc.detected_date)}</dd>
          <dt>Category</dt><dd>{fmt(doc.category)}</dd>
          <dt>Source</dt><dd>{fmt(doc.classification_source)}</dd>
          <dt>Status</dt><dd>{fmt(doc.filing_status)}</dd>
          <dt>Skipped</dt><dd>{doc.skipped ? 'yes' : 'no'}</dd>
          <dt>Created</dt><dd>{fmt(doc.created_at)}</dd>
          <dt>Reviewed</dt><dd>{fmt(doc.last_reviewed_at)}</dd>
          <dt>Path</dt><dd>{fmt(doc.filepath)}</dd>
        </dl>

        <div className="toolbar">
          <button className="btn ghost" onClick={() => navigate(-1)}>Back</button>
          {doc.file_exists && (
            <a className="btn" href={`/api/documents/${doc.id}/content`} target="_blank" rel="noopener noreferrer">Open Document</a>
          )}
        </div>
        {!doc.file_exists && <div className="notice">Document file is missing on disk. Check the stored filepath.</div>}

        <div className="action-panel">
          <h3>Edit date</h3>
          <div className="inline-form">
            <div className="field">
              <input
                type="date"
                value={currentDate}
                onChange={(e) => { setDateInput(e.target.value); setDirtyDate(true) }}
              />
            </div>
            <button onClick={() => patchMutation.mutate({ detected_date: currentDate })} disabled={!currentDate}>
              Save date
            </button>
          </div>
        </div>

        <div className="action-panel">
          <h3>Edit category</h3>
          <div className="inline-form">
            <div className="field">
              <input
                type="text"
                placeholder="(blank clears category)"
                value={currentCategory}
                onChange={(e) => { setCategoryInput(e.target.value); setDirtyCategory(true) }}
              />
            </div>
            <button onClick={() => patchMutation.mutate({ category: currentCategory })}>Save category</button>
          </div>
        </div>

        <div className="action-panel">
          <h3>AI</h3>
          <button onClick={handleAskAi} disabled={aiLoading}>
            {aiLoading ? (<><span className="spinner" /> Asking AI…</>) : 'Ask AI'}
          </button>
          {aiError && <div className="notice">{aiError}</div>}
          {suggestion && (
            <div className="ai-card pending">
              <strong>AI suggestion (not yet applied)</strong>
              <p><strong>Date:</strong> {suggestion.date || '(none)'} &nbsp; <strong>Category:</strong> {suggestion.category || '(none)'}</p>
              {suggestion.rationale && <p>{suggestion.rationale}</p>}
              {suggestion.summary && <p>{suggestion.summary}</p>}
              {commonFieldEntries(suggestion).length > 0 && (
                <dl className="field-grid">
                  {commonFieldEntries(suggestion).map(([k, v]) => (
                    <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
                  ))}
                </dl>
              )}
              {Object.keys(suggestion.fields).length > 0 && (
                <dl className="field-grid">
                  {Object.entries(suggestion.fields).map(([k, v]) => (
                    <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
                  ))}
                </dl>
              )}
              <button style={{ marginTop: 12 }} onClick={() => applyMutation.mutate(suggestion)}>
                Apply suggestion
              </button>
            </div>
          )}
        </div>

        <div className="action-panel">
          <h3>Filing</h3>
          <div className="inline-form">
            <button onClick={() => refileMutation.mutate()} disabled={!doc.detected_date}>
              Re-file to date/category folder
            </button>
            {doc.skipped ? (
              <span className="badge st-skipped">Skipped</span>
            ) : (
              <button onClick={() => skipMutation.mutate()}>Mark skipped</button>
            )}
            {doc.archived ? (
              <>
                <span className="badge st-archived">
                  Archived{doc.archived_reason === 'expired' ? ' (expired)' : ''}
                </span>
                <button className="btn subtle" onClick={() => unarchiveMutation.mutate()}>Unarchive</button>
              </>
            ) : (
              <button onClick={() => archiveMutation.mutate()}>Archive</button>
            )}
            <button className="btn danger" onClick={() => setConfirmDelete(true)}>Delete</button>
          </div>
          {!doc.detected_date && <p className="hint">Set a date before re-filing.</p>}
        </div>
      </div>

      {confirmDelete && (
        <ConfirmDialog
          title={`Delete document #${doc.id}?`}
          message={`This removes the database row for "${doc.filename}". The PDF file on disk is not deleted.`}
          confirmLabel="Yes, delete this row"
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </>
  )
}
