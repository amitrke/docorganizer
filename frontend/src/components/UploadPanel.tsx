import { useRef, useState } from 'react'
import { uploadFile, type UploadResult } from '../api/client'

interface UploadItem {
  id: string
  name: string
  progress: number
  status: 'uploading' | 'processing' | 'done' | 'error'
  result?: UploadResult
  message?: string
}

const CONCURRENCY = 3

async function runWithConcurrency<T>(items: T[], limit: number, worker: (item: T) => Promise<void>) {
  let cursor = 0
  async function next(): Promise<void> {
    const index = cursor++
    if (index >= items.length) return
    await worker(items[index])
    return next()
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => next()))
}

export function UploadPanel({ onUploaded }: { onUploaded: () => void }) {
  const [items, setItems] = useState<UploadItem[]>([])
  const [dragActive, setDragActive] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return
    const files = Array.from(fileList)
    const newItems: UploadItem[] = files.map((f, i) => ({
      id: `${Date.now()}-${i}-${f.name}`,
      name: f.name,
      progress: 0,
      status: 'uploading',
    }))
    setItems((prev) => [...newItems, ...prev])

    runWithConcurrency(files, CONCURRENCY, async (file) => {
      const id = newItems.find((it) => it.name === file.name)!.id
      try {
        const result = await uploadFile(file, (pct) => {
          setItems((prev) => prev.map((it) => (it.id === id ? { ...it, progress: pct, status: pct >= 100 ? 'processing' : 'uploading' } : it)))
        })
        setItems((prev) => prev.map((it) => (it.id === id ? { ...it, progress: 100, status: result.status === 'failed' || result.status === 'not_pdf' ? 'error' : 'done', result } : it)))
      } catch (err) {
        setItems((prev) => prev.map((it) => (it.id === id ? { ...it, status: 'error', message: err instanceof Error ? err.message : 'Upload failed' } : it)))
      }
    }).then(onUploaded)
  }

  function describe(item: UploadItem): string {
    if (item.status === 'uploading') return `Uploading… ${item.progress}%`
    if (item.status === 'processing') return 'Processing…'
    if (item.status === 'error') return item.message || item.result?.message || 'Not a PDF'
    const r = item.result
    if (!r) return 'Done'
    if (r.status === 'filed') return `Filed${r.category ? ` → ${r.category}` : ''}${r.detected_date ? ` (${r.detected_date})` : ''}`
    if (r.status === 'duplicate') return 'Duplicate — already indexed'
    return r.status
  }

  return (
    <div style={{ marginBottom: 14 }}>
      <div
        className={`dropzone${dragActive ? ' active' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragActive(true) }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragActive(false)
          handleFiles(e.dataTransfer.files)
        }}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M7 18a4.6 4.6 0 0 1-.6-9.16 5.5 5.5 0 0 1 10.6-2.1A4.5 4.5 0 0 1 17 18H7Z" />
          <path d="M12 12v6" />
          <path d="m9.5 14.5 2.5-2.5 2.5 2.5" />
        </svg>
        Drop PDFs here, or click to choose files
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => {
            handleFiles(e.target.files)
            e.target.value = ''
          }}
        />
      </div>
      {items.length > 0 && (
        <div className="upload-list">
          {items.map((item) => (
            <div key={item.id} className="upload-row">
              <span className="filename">{item.name}</span>
              <span>{describe(item)}</span>
              <div className="progress-track">
                <div
                  className={`progress-fill${item.status === 'error' ? ' error' : ''}`}
                  style={{ width: `${item.status === 'error' ? 100 : item.progress}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
