import { useEffect, useState } from 'react'
import client from '../../api/client'

function InspectorModal({ profile, onClose }) {
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('profile')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    client.get(`/profiles/${profile.login_id}/full`)
      .then((res) => { if (!cancelled) { setData(res.data); setLoading(false) } })
      .catch((e) => {
        if (cancelled) return
        setError(e?.response?.data?.detail || 'Failed to load')
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [profile.login_id])

  const accounts = data?.accounts || []
  const tx = data?.transactions || []

  const TABS = [
    { id: 'profile',      label: 'Profile',      count: null,            payload: data?.profile },
    { id: 'accounts',     label: 'Accounts',     count: accounts.length, payload: accounts },
    { id: 'transactions', label: 'Transactions', count: tx.length,       payload: tx },
    { id: 'summary',      label: 'Summary',      count: null,            payload: data?.summary },
    { id: 'raw',          label: 'Raw (all)',    count: null,            payload: data },
  ]
  const active = TABS.find((t) => t.id === tab) || TABS[0]
  const activeJson = active.payload ? JSON.stringify(active.payload, null, 2) : ''

  const copy = (s) => navigator.clipboard?.writeText(s)

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-6" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-5xl h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-gray-900">{profile.name}</h2>
            <p className="text-xs text-gray-500 font-mono">{profile.login_id}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-xl leading-none cursor-pointer"
            aria-label="Close"
          >×</button>
        </div>

        <div className="flex items-center gap-1 px-5 pt-2 border-b border-gray-100 flex-wrap">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-2 text-sm border-b-2 -mb-px cursor-pointer ${
                tab === t.id
                  ? 'border-blue-600 text-blue-600 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`}
            >
              {t.label}{t.count !== null && t.count > 0 ? ` (${t.count})` : ''}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => copy(activeJson)}
              disabled={loading || !!error || !activeJson}
              className="text-xs text-gray-600 hover:text-gray-900 border border-gray-200 rounded px-2 py-1 disabled:opacity-50 cursor-pointer"
            >Copy JSON</button>
          </div>
        </div>

        <div className="flex-1 overflow-hidden bg-gray-50">
          {loading && <div className="p-6 text-gray-400">Loading…</div>}
          {error && <div className="p-6 text-red-600">{error}</div>}
          {!loading && !error && (
            <pre className="h-full overflow-auto p-4 text-xs font-mono text-gray-800 whitespace-pre-wrap">
              {activeJson || `(no ${active.label.toLowerCase()})`}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ProfileCard({ profile, onSelect, selected }) {
  const [showInspector, setShowInspector] = useState(false)

  return (
    <>
      <div
        onClick={() => onSelect(profile)}
        className={`w-full text-left p-4 rounded-xl border-2 transition-all cursor-pointer flex items-center gap-3 ${
          selected
            ? 'border-blue-500 bg-blue-50 shadow-md'
            : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
        }`}
      >
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-gray-900 text-lg">{profile.name}</h3>
          <p className="text-sm text-gray-500 mt-1">
            {profile.city}, {profile.state} &middot; {profile.tier} &middot; {profile.account_count} accounts
          </p>
        </div>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setShowInspector(true) }}
          className="shrink-0 text-xs text-gray-600 hover:text-gray-900 border border-gray-300 rounded-lg px-2.5 py-1 bg-white hover:bg-gray-50 cursor-pointer"
          title="Inspect profile JSON and transactions"
        >
          View
        </button>
      </div>
      {showInspector && (
        <InspectorModal profile={profile} onClose={() => setShowInspector(false)} />
      )}
    </>
  )
}
