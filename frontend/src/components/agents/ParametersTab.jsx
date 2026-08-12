import { useEffect, useState } from 'react'

// Agent-level Planner-fillable parameters editor.
//
// Shape stored on the template (and sent to POST /agents as `parameters`):
//   { properties: {name: {type, enum?, description?}},
//     required: [...], writes: {name: variable} }
//
// These merge into the sub-agent tool's OpenAI schema so the orchestrator
// LLM fills them when it invokes the agent; valid values seed the inner
// graph's `variables`, letting parse_node skip/narrow its extraction call.
// Row editor by default; a raw-JSON toggle covers hand-authored schemas
// (mirrors ParamsEditor's fallback in NodePropertiesPanel).

const TYPES = ['string', 'number', 'integer', 'boolean']

function toRows(parameters) {
  const props = parameters?.properties || {}
  const required = new Set(parameters?.required || [])
  const writes = parameters?.writes || {}
  return Object.entries(props).map(([name, spec]) => ({
    name,
    type: spec?.type || 'string',
    enumText: Array.isArray(spec?.enum) ? spec.enum.join(', ') : '',
    description: spec?.description || '',
    required: required.has(name),
    writesTo: writes[name] || '',
  }))
}

function toParameters(rows) {
  const properties = {}
  const required = []
  const writes = {}
  for (const r of rows) {
    const name = (r.name || '').trim()
    if (!name) continue
    const spec = { type: r.type || 'string' }
    if ((r.description || '').trim()) spec.description = r.description.trim()
    const enumVals = (r.enumText || '')
      .split(',').map((s) => s.trim()).filter(Boolean)
      .map((s) => {
        if (r.type === 'number') { const n = Number(s); return Number.isNaN(n) ? null : n }
        if (r.type === 'integer') { const n = parseInt(s, 10); return Number.isNaN(n) ? null : n }
        return s
      })
      .filter((v) => v !== null)
    if (enumVals.length && r.type !== 'boolean') spec.enum = enumVals
    properties[name] = spec
    if (r.required) required.push(name)
    const w = (r.writesTo || '').trim()
    if (w && w !== name) writes[name] = w
  }
  if (!Object.keys(properties).length) return {}
  return { properties, required, writes }
}

function rowProblem(row, rows, idx) {
  const name = (row.name || '').trim()
  if (!name) return null
  if (name === 'message') return "'message' is reserved — it always carries the user's full request"
  if (!/^[a-z][a-z0-9_]*$/.test(name)) return 'name must be snake_case (a-z, 0-9, _)'
  if (rows.some((r, i) => i !== idx && (r.name || '').trim() === name)) return 'duplicate name'
  const w = (row.writesTo || '').trim()
  if (w.startsWith('_')) return "writes-to may not start with '_' (reserved)"
  return null
}

export default function ParametersTab({ parameters, onChange, isLocked }) {
  const [rawMode, setRawMode] = useState(false)
  const [rawText, setRawText] = useState(null)   // lazily seeded on toggle
  const [rawErr, setRawErr] = useState('')

  // Rows live in local state: a just-added blank row (or one mid-rename)
  // serializes to nothing, so deriving rows purely from `parameters` would
  // make it vanish on the next render. Re-sync only when the prop changes
  // to something our own rows didn't produce (e.g. async hydration on edit
  // load, or the raw-JSON editor).
  const [rows, setRowsState] = useState(() => toRows(parameters))
  useEffect(() => {
    const incoming = JSON.stringify(parameters || {})
    if (incoming !== JSON.stringify(toParameters(rows))) {
      setRowsState(toRows(parameters))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parameters])
  const setRows = (next) => {
    setRowsState(next)
    onChange(toParameters(next))
  }
  const updateRow = (idx, key, value) =>
    setRows(rows.map((r, i) => (i === idx ? { ...r, [key]: value } : r)))
  const addRow = () =>
    setRows([...rows, { name: '', type: 'string', enumText: '', description: '', required: false, writesTo: '' }])
  const removeRow = (idx) => setRows(rows.filter((_, i) => i !== idx))

  const enterRaw = () => {
    setRawText(JSON.stringify(parameters && Object.keys(parameters).length ? parameters : { properties: {}, required: [], writes: {} }, null, 2))
    setRawErr('')
    setRawMode(true)
  }
  const onRawChange = (text) => {
    setRawText(text)
    try {
      const v = JSON.parse(text || '{}')
      onChange(v && Object.keys(v.properties || {}).length ? v : {})
      setRawErr('')
    } catch (ex) {
      setRawErr(String(ex.message || ex))
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg bg-sky-50 border border-sky-200 px-3 py-2.5 text-[12px] text-sky-900 leading-relaxed">
        <strong>Planner-fillable parameters.</strong> Declared parameters appear in
        this agent's tool schema, so the orchestrator LLM fills them in the same
        call that invokes the agent — values pre-seed the graph's variables and
        the entry <code className="text-[11px]">parse_node</code> skips fields that
        are already filled (one fewer LLM call). The <em>description</em> is what
        the orchestrator reads: say precisely when to fill the value and tell it
        to omit anything the user didn't state. Parameters are agent-level —
        shared across chat and voice variants.
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">
          {rows.length ? `${rows.length} parameter${rows.length > 1 ? 's' : ''}` : 'No parameters declared'}
        </span>
        <button
          type="button"
          onClick={() => (rawMode ? setRawMode(false) : enterRaw())}
          className="text-[11px] text-blue-600 hover:text-blue-800 cursor-pointer"
        >
          {rawMode ? 'Form editor' : 'Edit as JSON'}
        </button>
      </div>

      {rawMode ? (
        <div>
          <textarea
            value={rawText ?? ''}
            onChange={(e) => onRawChange(e.target.value)}
            rows={18}
            disabled={isLocked}
            className={`w-full px-3 py-2 text-sm font-mono leading-snug border rounded focus:outline-none focus:ring-2 resize-y ${rawErr ? 'border-red-300 focus:ring-red-200' : 'border-gray-200 focus:ring-blue-200'}`}
          />
          {rawErr && <p className="text-[11px] text-red-500 mt-0.5">{rawErr}</p>}
        </div>
      ) : (
        <>
          {rows.map((row, idx) => {
            const problem = rowProblem(row, rows, idx)
            return (
              <div key={idx} className="border border-gray-200 rounded-lg p-3 space-y-2 bg-gray-50">
                <div className="flex gap-1.5 items-center">
                  <input
                    className="flex-1 px-2 py-1 text-xs font-mono border border-gray-200 rounded"
                    placeholder="parameter_name"
                    value={row.name}
                    disabled={isLocked}
                    onChange={(e) => updateRow(idx, 'name', e.target.value)}
                  />
                  <select
                    className="px-2 py-1 text-xs border border-gray-200 rounded bg-white"
                    value={row.type}
                    disabled={isLocked}
                    onChange={(e) => updateRow(idx, 'type', e.target.value)}
                  >
                    {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <button
                    type="button"
                    className="text-red-500 text-[11px] hover:text-red-700 cursor-pointer"
                    disabled={isLocked}
                    onClick={() => removeRow(idx)}
                  >✕</button>
                </div>

                {row.type !== 'boolean' && (
                  <input
                    className="w-full px-2 py-1 text-xs font-mono border border-gray-200 rounded"
                    placeholder="allowed values, comma-separated (optional enum)"
                    value={row.enumText}
                    disabled={isLocked}
                    onChange={(e) => updateRow(idx, 'enumText', e.target.value)}
                  />
                )}

                <textarea
                  className="w-full px-2 py-1 text-xs border border-gray-200 rounded resize-y"
                  rows={2}
                  placeholder="Description the orchestrator LLM reads — when to fill this, and to omit it unless the user stated it."
                  value={row.description}
                  disabled={isLocked}
                  onChange={(e) => updateRow(idx, 'description', e.target.value)}
                />

                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-1.5 text-[11px] text-gray-700 cursor-pointer" title="Required forces the orchestrator to always supply a value — it will guess when unsure. Leave off unless truly mandatory; the parser and interrupts collect missing values.">
                    <input
                      type="checkbox"
                      className="h-3 w-3"
                      checked={row.required}
                      disabled={isLocked}
                      onChange={(e) => updateRow(idx, 'required', e.target.checked)}
                    />
                    required
                  </label>
                  <input
                    className="flex-1 px-2 py-1 text-[11px] font-mono border border-gray-200 rounded"
                    placeholder={`writes to variable (default: ${(row.name || '').trim() || 'name'})`}
                    value={row.writesTo}
                    disabled={isLocked}
                    onChange={(e) => updateRow(idx, 'writesTo', e.target.value)}
                  />
                </div>

                {problem && <p className="text-[11px] text-red-500">{problem}</p>}
              </div>
            )
          })}

          <button
            type="button"
            onClick={addRow}
            disabled={isLocked}
            className="w-full text-xs py-1.5 border border-dashed border-gray-300 rounded text-gray-600 hover:bg-gray-50 cursor-pointer"
          >+ Parameter</button>
        </>
      )}

      <p className="text-[11px] text-gray-400 italic leading-relaxed">
        Safety: parameters can never write to a confirmation slot (an
        interrupt's target) or a tool result variable — saves that try are
        rejected. Data-collection interrupts can opt in per-node via
        "Planner may pre-fill" in the interrupt editor. On interrupt replies
        the parser always runs in full, so user corrections still win.
      </p>
    </div>
  )
}
