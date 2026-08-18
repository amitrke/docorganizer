import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Hero } from '../components/Hero'
import { Flash } from '../components/Flash'
import { UploadPanel } from '../components/UploadPanel'
import { BulkAiModal } from '../components/BulkAiModal'
import { listDocuments } from '../api/client'

const PAGE_SIZE = 25

function isoDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function datePresets(): { label: string; from: string; to: string }[] {
  const today = new Date()
  const daysAgo = (n: number) => {
    const d = new Date(today)
    d.setDate(d.getDate() - n)
    return d
  }
  const thisYearStart = new Date(today.getFullYear(), 0, 1)
  const lastYearStart = new Date(today.getFullYear() - 1, 0, 1)
  const lastYearEnd = new Date(today.getFullYear() - 1, 11, 31)
  return [
    { label: 'Last 7 days', from: isoDate(daysAgo(6)), to: isoDate(today) },
    { label: 'Last 30 days', from: isoDate(daysAgo(29)), to: isoDate(today) },
    { label: 'Last 90 days', from: isoDate(daysAgo(89)), to: isoDate(today) },
    { label: 'This year', from: isoDate(thisYearStart), to: isoDate(today) },
    { label: 'Last year', from: isoDate(lastYearStart), to: isoDate(lastYearEnd) },
    { label: 'All time', from: '', to: '' },
  ]
}

function fmt(value: string | null | undefined, fallback = '(none)'): string {
  const text = (value ?? '').trim()
  return text || fallback
}

const SORT_COLUMNS: { key: string; label: string }[] = [
  { key: 'id', label: 'ID' },
  { key: 'filename', label: 'Filename' },
  { key: 'detected_date', label: 'Date' },
  { key: 'category', label: 'Category' },
  { key: 'classification_source', label: 'Source' },
  { key: 'filing_status', label: 'Status' },
]

export default function Browse() {
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()

  const q = params.get('q') ?? ''
  const status = params.get('status') ?? 'all'
  const category = params.get('category') ?? ''
  const dateFrom = params.get('date_from') ?? ''
  const dateTo = params.get('date_to') ?? ''
  const sortBy = params.get('sort_by') ?? 'detected_date'
  const sortDir = (params.get('sort_dir') === 'asc' ? 'asc' : 'desc') as 'asc' | 'desc'
  const page = Number(params.get('page') ?? '1')

  const [qInput, setQInput] = useState(q)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkOpen, setBulkOpen] = useState(false)
  const [flash, setFlash] = useState<{ message: string; kind: 'success' | 'error' } | null>(null)

  const presets = useMemo(datePresets, [])

  // First-ever visit (no date filter chosen yet, explicitly or otherwise) defaults
  // to "This year" rather than showing the entire archive.
  useEffect(() => {
    if (!params.has('date_from') && !params.has('date_to')) {
      const thisYear = presets.find((p) => p.label === 'This year')!
      updateParams({ date_from: thisYear.from, date_to: thisYear.to })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const queryKey = ['documents', { q, status, category, dateFrom, dateTo, sortBy, sortDir, page }]
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => listDocuments({
      q, status, category: category || undefined,
      date_from: dateFrom || undefined, date_to: dateTo || undefined,
      sort_by: sortBy, sort_dir: sortDir, page,
    }),
    placeholderData: (prev) => prev,
  })

  function updateParams(next: Record<string, string>) {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (key === 'date_from' || key === 'date_to') {
        // Always kept present once touched (even blank = explicit "All time"),
        // so a genuine first visit can be told apart from a deliberate clear.
        merged.set(key, value)
      } else if (value) {
        merged.set(key, value)
      } else {
        merged.delete(key)
      }
    }
    if (!('page' in next)) merged.delete('page')
    setParams(merged)
    setSelected(new Set())
  }

  function submitFilters(e: React.FormEvent) {
    e.preventDefault()
    updateParams({ q: qInput })
  }

  function applyPreset(preset: { from: string; to: string }) {
    updateParams({ date_from: preset.from, date_to: preset.to })
  }

  function toggleSort(column: string) {
    if (sortBy === column) {
      updateParams({ sort_by: column, sort_dir: sortDir === 'asc' ? 'desc' : 'asc' })
    } else {
      updateParams({ sort_by: column, sort_dir: 'desc' })
    }
  }

  function toggleSelected(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    if (!data) return
    setSelected((prev) => {
      const allSelected = data.items.every((doc) => prev.has(doc.id))
      if (allSelected) return new Set()
      return new Set(data.items.map((doc) => doc.id))
    })
  }

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['documents'] })
  }

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const selectedDocs = (data?.items ?? []).filter((doc) => selected.has(doc.id))

  return (
    <>
      <Hero
        title="Document Browser"
        subtitle="Search, filter, and open filed PDFs from your docorganizer index."
        active="home"
      />
      <Flash message={flash?.message ?? null} kind={flash?.kind} />

      <section className="filters">
        <UploadPanel onUploaded={refresh} />

        <form onSubmit={submitFilters} className="filters-row">
          <div className="search-field">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
            <input
              type="search"
              placeholder="Search terms (FTS)"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
            />
          </div>
          <select value={status} onChange={(e) => updateParams({ status: e.target.value === 'all' ? '' : e.target.value })}>
            <option value="all">All</option>
            <option value="pending">Pending</option>
            <option value="filed">Filed</option>
          </select>
          <select value={category} onChange={(e) => updateParams({ category: e.target.value })}>
            <option value="">All categories</option>
            {(data?.categories ?? []).map((cat) => (
              <option key={cat} value={cat}>{cat}</option>
            ))}
          </select>
          <label className="date-field">
            From <input type="date" value={dateFrom} onChange={(e) => updateParams({ date_from: e.target.value })} />
          </label>
          <label className="date-field">
            To <input type="date" value={dateTo} onChange={(e) => updateParams({ date_to: e.target.value })} />
          </label>
          <button type="submit">Search</button>
        </form>

        <div className="preset-row">
          {presets.map((preset) => {
            const isActive = dateFrom === preset.from && dateTo === preset.to
            return (
              <button
                key={preset.label}
                type="button"
                className={`page-link${isActive ? ' current' : ''}`}
                onClick={() => applyPreset(preset)}
              >
                {preset.label}
              </button>
            )
          })}
        </div>
      </section>

      {selected.size > 0 && (
        <div className="bulk-bar">
          <span>{selected.size} selected</span>
          <span className="spacer" />
          <button onClick={() => setBulkOpen(true)}>Ask AI for {selected.size} selected</button>
          <button className="btn ghost" onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
      )}

      <section className="table-wrap">
        <table>
          <thead>
            <tr>
              <th className="checkbox-col">
                <input
                  type="checkbox"
                  checked={(data?.items.length ?? 0) > 0 && data!.items.every((doc) => selected.has(doc.id))}
                  onChange={toggleSelectAll}
                />
              </th>
              {SORT_COLUMNS.map((col) => (
                <th key={col.key}>
                  <button type="button" className="sort-header" onClick={() => toggleSort(col.key)}>
                    {col.label}
                    {sortBy === col.key && (
                      <svg
                        width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                        strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"
                        style={{ transform: sortDir === 'asc' ? 'rotate(180deg)' : undefined }}
                      >
                        <path d="m6 9 6 6 6-6" />
                      </svg>
                    )}
                  </button>
                </th>
              ))}
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={8}><div className="empty">Loading…</div></td></tr>
            )}
            {!isLoading && (data?.items.length ?? 0) === 0 && (
              <tr><td colSpan={8}><div className="empty">No documents match this filter.</div></td></tr>
            )}
            {(data?.items ?? []).map((doc) => (
              <tr key={doc.id}>
                <td className="checkbox-col">
                  <input type="checkbox" checked={selected.has(doc.id)} onChange={() => toggleSelected(doc.id)} />
                </td>
                <td className="mono">{doc.id}</td>
                <td>{fmt(doc.filename)}</td>
                <td>{fmt(doc.detected_date)}</td>
                <td>{fmt(doc.category)}</td>
                <td><span className={`badge src-${doc.classification_source || 'other'}`}>{fmt(doc.classification_source)}</span></td>
                <td><span className={`badge st-${doc.filing_status || 'other'}`}>{fmt(doc.filing_status)}</span></td>
                <td className="actions">
                  <Link className="btn subtle" to={`/documents/${doc.id}`}>Details</Link>
                  <a className="btn" href={`/api/documents/${doc.id}/content`} target="_blank" rel="noopener noreferrer">View</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {totalPages > 1 && (
          <div className="pager">
            <span className="result-count">
              Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
            </span>
            <button className="page-link" disabled={page <= 1} onClick={() => updateParams({ page: String(page - 1) })}>&larr; Prev</button>
            <span className="page-link current">{page}</span>
            <button className="page-link" disabled={page >= totalPages} onClick={() => updateParams({ page: String(page + 1) })}>Next &rarr;</button>
          </div>
        )}
      </section>

      {bulkOpen && (
        <BulkAiModal
          docs={selectedDocs.map((d) => ({ id: d.id, filename: d.filename }))}
          onClose={() => setBulkOpen(false)}
          onApplied={() => {
            refresh()
            setFlash({ message: 'AI suggestion applied', kind: 'success' })
          }}
        />
      )}
    </>
  )
}
