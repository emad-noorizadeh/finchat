import { useEffect, useMemo, useState } from 'react'
import WidgetRenderer from '../components/widgets/WidgetRenderer'

const COMPOSABLE_STYLE = {
  full: 'bg-green-100 text-green-800',
  degraded: 'bg-amber-100 text-amber-800',
  never: 'bg-gray-200 text-gray-700',
}

const TIER_ACCENT = {
  1: 'border-l-blue-400',
  2: 'border-l-gray-300',
}

function Badge({ label, className = 'bg-gray-100 text-gray-700' }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[11px] font-medium ${className}`}>
      {label}
    </span>
  )
}

function WidgetTile({ widgetType, entry, onClick }) {
  const accent = TIER_ACCENT[entry.tier] || 'border-l-gray-300'
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative text-left bg-white rounded-lg border border-gray-200 border-l-4 ${accent} px-4 py-3 hover:border-gray-300 hover:shadow-sm transition-all cursor-pointer`}
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <h3 className="text-sm font-semibold text-gray-800 truncate">{entry.display_name}</h3>
        <Badge label={`T${entry.tier}`} />
      </div>
      <p className="text-xs text-gray-500 line-clamp-2 mb-2.5">{entry.description}</p>
      <div className="flex items-center gap-1.5 flex-wrap">
        <Badge
          label={entry.composable}
          className={COMPOSABLE_STYLE[entry.composable] || 'bg-gray-100 text-gray-700'}
        />
        {entry.slot_combination && (
          <Badge label="designed composite" className="bg-indigo-100 text-indigo-800" />
        )}
        <span className="text-[11px] text-gray-400 font-mono ml-auto">{widgetType}</span>
      </div>
    </button>
  )
}

function DetailView({ widgetType, entry, onBack }) {
  const preview = { widget: widgetType, data: entry.sample_data, status: 'pending' }

  return (
    <div className="space-y-5">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 cursor-pointer"
      >
        <span>←</span> Back to widgets
      </button>

      <div>
        <div className="flex items-start justify-between gap-3 mb-1.5">
          <h1 className="text-2xl font-bold text-gray-800">{entry.display_name}</h1>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <Badge label={`tier ${entry.tier}`} />
            <Badge
              label={`composable: ${entry.composable}`}
              className={COMPOSABLE_STYLE[entry.composable] || 'bg-gray-100 text-gray-700'}
            />
            {entry.slot_combination && (
              <Badge label="designed composite" className="bg-indigo-100 text-indigo-800" />
            )}
          </div>
        </div>
        <p className="text-sm text-gray-600">{entry.description}</p>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-4 text-xs text-gray-500 font-mono space-y-1">
        <div><span className="text-gray-400">type:</span> {widgetType}</div>
        <div><span className="text-gray-400">render tool:</span> {entry.render_tool || '—'}</div>
        {entry.default_data_var && (
          <div><span className="text-gray-400">default data slot:</span> {entry.default_data_var}</div>
        )}
        {entry.slot_combination && (
          <div>
            <span className="text-gray-400">required slots:</span>{' '}
            {entry.slot_combination.join(', ')}
          </div>
        )}
        <div><span className="text-gray-400">standalone render:</span> {entry.standalone_render}</div>
        {entry.composite_render && (
          <div><span className="text-gray-400">composite render:</span> {entry.composite_render}</div>
        )}
      </div>

      {entry.degradation_note && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800">
          <strong className="font-semibold">Degradation: </strong>{entry.degradation_note}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div>
          <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-2 font-semibold">Fields</h3>
          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left py-1.5 px-3 text-[11px] uppercase tracking-wide text-gray-500 font-medium">Name</th>
                  <th className="text-left py-1.5 px-3 text-[11px] uppercase tracking-wide text-gray-500 font-medium">Type</th>
                  <th className="text-left py-1.5 px-3 text-[11px] uppercase tracking-wide text-gray-500 font-medium">Required</th>
                </tr>
              </thead>
              <tbody>
                {entry.fields.map((f) => (
                  <tr key={f.name} className="border-t border-gray-100 first:border-t-0">
                    <td className="py-1.5 px-3 font-mono text-xs text-gray-700">{f.name}</td>
                    <td className="py-1.5 px-3 text-xs text-gray-500">{f.type}</td>
                    <td className="py-1.5 px-3 text-xs">
                      {f.required ? (
                        <span className="text-amber-700">required</span>
                      ) : (
                        <span className="text-gray-400">optional</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {entry.voice_summary_template && (
            <div className="mt-4">
              <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-2 font-semibold">Voice summary template</h3>
              <code className="block text-xs text-gray-700 bg-gray-50 border border-gray-200 rounded-lg p-3 font-mono whitespace-pre-wrap">
                {entry.voice_summary_template}
              </code>
            </div>
          )}
        </div>

        <div>
          <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-2 font-semibold">Preview</h3>
          <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
            <WidgetRenderer widget={preview} onAction={() => {}} />
          </div>
        </div>
      </div>
    </div>
  )
}

export default function WidgetsPage() {
  const [catalog, setCatalog] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [search, setSearch] = useState('')
  const [tierFilter, setTierFilter] = useState('all')

  useEffect(() => {
    let cancelled = false
    fetch('/api/widgets/catalog')
      .then((r) => r.json())
      .then((d) => { if (!cancelled) setCatalog(d) })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [])

  const entries = useMemo(() => Object.entries(catalog?.widgets || {}), [catalog])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return entries.filter(([type, entry]) => {
      if (tierFilter !== 'all' && String(entry.tier) !== tierFilter) return false
      if (!q) return true
      return (
        type.toLowerCase().includes(q) ||
        entry.display_name.toLowerCase().includes(q) ||
        (entry.description || '').toLowerCase().includes(q)
      )
    })
  }, [entries, search, tierFilter])

  if (error) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-gray-800 mb-2">Widgets</h1>
        <p className="text-red-600">Failed to load catalog: {error}</p>
      </div>
    )
  }

  if (!catalog) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-gray-800 mb-2">Widgets</h1>
        <p className="text-gray-500">Loading…</p>
      </div>
    )
  }

  if (selected && catalog.widgets[selected]) {
    return (
      <div className="p-6">
        <DetailView
          widgetType={selected}
          entry={catalog.widgets[selected]}
          onBack={() => setSelected(null)}
        />
      </div>
    )
  }

  return (
    <div className="p-6">
      <header className="flex items-end justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Widgets</h1>
          <p className="text-sm text-gray-500">
            {entries.length} widgets — the catalog drives the Agent Builder, render tools, and the Presenter.
          </p>
        </div>
        <div className="text-[11px] text-gray-400 font-mono">v{catalog.version}</div>
      </header>

      <div className="flex items-center gap-3 mb-4">
        <input
          type="text"
          placeholder="Search widgets…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 max-w-md px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:border-blue-500"
        />
        <select
          value={tierFilter}
          onChange={(e) => setTierFilter(e.target.value)}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:border-blue-500 cursor-pointer"
        >
          <option value="all">All tiers</option>
          <option value="1">Tier 1 (designed)</option>
          <option value="2">Tier 2 (generic)</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-gray-400">No widgets match your filters.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {filtered.map(([type, entry]) => (
            <WidgetTile
              key={type}
              widgetType={type}
              entry={entry}
              onClick={() => setSelected(type)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
