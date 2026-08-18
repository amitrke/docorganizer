import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Hero } from '../components/Hero'
import { Flash } from '../components/Flash'
import { addCategory, listCategories, removeCategory, ApiError } from '../api/client'

export default function Categories() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['categories'], queryFn: listCategories })
  const [name, setName] = useState('')
  const [flash, setFlash] = useState<{ message: string; kind: 'success' | 'error' } | null>(null)

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['categories'] })
  }

  const addMutation = useMutation({
    mutationFn: (n: string) => addCategory(n),
    onSuccess: () => { setName(''); refresh(); setFlash({ message: 'Category added', kind: 'success' }) },
    onError: (err) => setFlash({ message: err instanceof ApiError ? err.message : 'Add failed', kind: 'error' }),
  })

  const removeMutation = useMutation({
    mutationFn: (n: string) => removeCategory(n),
    onSuccess: () => { refresh(); setFlash({ message: 'Category removed', kind: 'success' }) },
    onError: (err) => setFlash({ message: err instanceof ApiError ? err.message : 'Remove failed', kind: 'error' }),
  })

  return (
    <>
      <Hero title="Categories" subtitle="Manage the categories used for classification and filing." active="categories" />
      <Flash message={flash?.message ?? null} kind={flash?.kind} />

      <div className="card">
        <h1>Categories</h1>
        <p className="hint">
          Categories used by classification rules and filing. Stored in <code>config.yaml</code>, alongside the
          keyword rules that assign them (edit <code>config.yaml</code> directly to change rules).
        </p>

        <ul className="category-list">
          {(data?.configured.length ?? 0) === 0 && <li className="hint">No categories configured yet.</li>}
          {data?.configured.map((cat) => (
            <li key={cat}>
              <span>{cat}</span>
              <button className="btn subtle" style={{ padding: '4px 10px' }} onClick={() => removeMutation.mutate(cat)}>
                Remove
              </button>
            </li>
          ))}
        </ul>

        <form
          className="inline-form"
          onSubmit={(e) => { e.preventDefault(); if (name.trim()) addMutation.mutate(name.trim()) }}
        >
          <div className="field">
            <input type="text" placeholder="New category name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <button type="submit">Add category</button>
        </form>

        {data && data.db_only.length > 0 && (
          <>
            <h3 style={{ marginTop: 24 }}>Also seen in the database (not in the config list)</h3>
            <ul className="category-list">
              {data.db_only.map((cat) => <li key={cat}>{cat}</li>)}
            </ul>
          </>
        )}
      </div>
    </>
  )
}
