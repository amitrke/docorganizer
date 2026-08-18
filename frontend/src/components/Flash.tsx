export function Flash({ message, kind = 'success' }: { message: string | null; kind?: 'success' | 'error' }) {
  if (!message) return null
  return <div className={`flash ${kind}`}>{message}</div>
}
