import { useEffect, useState } from 'react'
import { applyAi, bulkAskAi, commonFieldEntries, type AiSuggestion } from '../api/client'

interface ItemState {
  status: 'pending' | 'suggested' | 'error' | 'applied' | 'applying'
  suggestion?: AiSuggestion
  error?: string
}

export function BulkAiModal({
  docs,
  onClose,
  onApplied,
}: {
  docs: { id: number; filename: string }[]
  onClose: () => void
  onApplied: () => void
}) {
  const [items, setItems] = useState<Record<number, ItemState>>(() =>
    Object.fromEntries(docs.map((d) => [d.id, { status: 'pending' }])),
  )

  useEffect(() => {
    const stop = bulkAskAi(docs.map((d) => d.id), (event) => {
      if ('doc_id' in event) {
        setItems((prev) => ({
          ...prev,
          [event.doc_id]:
            event.status === 'suggested'
              ? { status: 'suggested', suggestion: event.suggestion }
              : { status: 'error', error: event.error },
        }))
      }
    })
    return stop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function applyOne(id: number) {
    const item = items[id]
    if (!item?.suggestion) return
    setItems((prev) => ({ ...prev, [id]: { ...prev[id], status: 'applying' } }))
    await applyAi(id, item.suggestion)
    setItems((prev) => ({ ...prev, [id]: { ...prev[id], status: 'applied' } }))
    onApplied()
  }

  async function applyAllReady() {
    for (const doc of docs) {
      if (items[doc.id]?.status === 'suggested') {
        await applyOne(doc.id)
      }
    }
  }

  const readyCount = docs.filter((d) => items[d.id]?.status === 'suggested').length
  const stillWorking = docs.some((d) => items[d.id]?.status === 'pending')

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Ask AI — {docs.length} document{docs.length === 1 ? '' : 's'}</h2>
        <div>
          {docs.map((doc) => {
            const item = items[doc.id] ?? { status: 'pending' as const }
            const s = item.suggestion
            return (
              <div key={doc.id} className="bulk-ai-row">
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600 }}>{doc.filename}</div>
                  {item.status === 'pending' && <span className="hint"><span className="spinner" /> Waiting…</span>}
                  {item.status === 'error' && <span className="hint" style={{ color: 'var(--danger)' }}>{item.error}</span>}
                  {s && (
                    <div className="ai-card pending" style={{ marginTop: 8 }}>
                      <p style={{ marginBottom: 4 }}>
                        <strong>Date:</strong> {s.date || '(none)'} &nbsp; <strong>Category:</strong> {s.category || '(none)'}
                      </p>
                      {s.rationale && <p>{s.rationale}</p>}
                      {s.summary && <p>{s.summary}</p>}
                      {commonFieldEntries(s).length > 0 && (
                        <dl className="field-grid">
                          {commonFieldEntries(s).map(([k, v]) => (
                            <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
                          ))}
                        </dl>
                      )}
                      {Object.keys(s.fields).length > 0 && (
                        <dl className="field-grid">
                          {Object.entries(s.fields).map(([k, v]) => (
                            <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
                          ))}
                        </dl>
                      )}
                    </div>
                  )}
                </div>
                {item.status === 'suggested' && (
                  <button onClick={() => applyOne(doc.id)}>Apply</button>
                )}
                {item.status === 'applying' && <span className="spinner" />}
                {item.status === 'applied' && <span className="badge st-filed">Applied</span>}
              </div>
            )
          })}
        </div>
        <div className="toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
          <button onClick={applyAllReady} disabled={readyCount === 0}>
            Apply all ready ({readyCount})
          </button>
          <button className="btn ghost" onClick={onClose} disabled={stillWorking}>
            {stillWorking ? 'Working…' : 'Close'}
          </button>
        </div>
      </div>
    </div>
  )
}
