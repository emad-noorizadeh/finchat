import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Parsers registered in backend/app/agents/parsers/__init__.py
const PARSERS = ['money', 'yes_no', 'account_keyword', 'last4']

const RETURN_MODES = ['widget', 'glass', 'to_presenter', 'to_orchestrator']


function Section({ title, children }) {
  return (
    <div className="space-y-2 border-t border-gray-100 pt-3">
      <h4 className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold">{title}</h4>
      {children}
    </div>
  )
}

// Field with an Edit / Preview toggle — the Preview tab renders markdown
// (GFM). Used for system prompts so authors can see headings + bullet lists
// + inline code laid out the way the LLM ultimately sees them.
function MarkdownField({
  label, value, onChange, placeholder = '',
  autoGrow = true, minRows = 10, defaultView = 'edit',
}) {
  const [view, setView] = useState(defaultView)
  const effectiveRows = autoGrow
    ? Math.max(minRows, (String(value || '').split('\n').length) + 1)
    : minRows
  const empty = !value || !String(value).trim()
  return (
    <div className="block">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-600 font-medium">{label}</span>
        <div className="inline-flex text-[11px] border border-gray-200 rounded overflow-hidden">
          <button
            type="button"
            onClick={() => setView('edit')}
            className={`px-2 py-0.5 ${view === 'edit' ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >Edit</button>
          <button
            type="button"
            onClick={() => setView('preview')}
            className={`px-2 py-0.5 border-l border-gray-200 ${view === 'preview' ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >Preview</button>
        </div>
      </div>
      {view === 'edit' ? (
        <textarea
          value={value || ''}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={effectiveRows}
          className="mt-1 w-full px-3 py-2 text-sm leading-snug border border-gray-200 rounded focus:outline-none focus:ring-2 focus:ring-blue-200 resize-y font-mono"
        />
      ) : (
        <div className="mt-1 w-full px-4 py-3 text-sm leading-relaxed border border-gray-200 rounded bg-gray-50 prose prose-sm max-w-none
                        prose-headings:mt-3 prose-headings:mb-2 prose-h1:text-base prose-h2:text-sm prose-h3:text-sm
                        prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5
                        prose-code:text-[12px] prose-code:bg-white prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-[''] prose-code:after:content-['']">
          {empty ? (
            <p className="text-gray-400 italic">No prompt yet — switch to Edit to add one.</p>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
          )}
        </div>
      )}
    </div>
  )
}


function TextField({
  label, value, onChange, placeholder = '',
  multiline = false, rows = 6, autoGrow = false, minRows = 6,
}) {
  // Auto-growing textareas stay "always expanded" — grow with content so
  // long system prompts never hide behind a scrollbar. Line-count-based
  // heuristic is good enough without a ResizeObserver dance.
  const effectiveRows = multiline && autoGrow
    ? Math.max(minRows, (String(value || '').split('\n').length) + 1)
    : rows
  return (
    <label className="block">
      <span className="text-xs text-gray-600 font-medium">{label}</span>
      {multiline ? (
        <textarea
          value={value || ''}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={effectiveRows}
          className="mt-1 w-full px-3 py-2 text-sm leading-snug border border-gray-200 rounded focus:outline-none focus:ring-2 focus:ring-blue-200 resize-y"
        />
      ) : (
        <input
          type="text"
          value={value || ''}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="mt-1 w-full px-3 py-2 text-sm border border-gray-200 rounded focus:outline-none focus:ring-2 focus:ring-blue-200"
        />
      )}
    </label>
  )
}

function SelectField({ label, value, onChange, options, includeBlank = true }) {
  const optionValues = new Set(options.map((o) => (typeof o === 'string' ? o : o.value)))
  const needsOrphan = value && !optionValues.has(value)
  return (
    <label className="block">
      <span className="text-xs text-gray-600 font-medium">{label}</span>
      <select
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full px-3 py-2 text-sm border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
      >
        {includeBlank && <option value="">—</option>}
        {needsOrphan && <option value={value}>{value} (custom)</option>}
        {options.map((o) => (
          <option key={typeof o === 'string' ? o : o.value} value={typeof o === 'string' ? o : o.value}>
            {typeof o === 'string' ? o : o.label}
          </option>
        ))}
      </select>
    </label>
  )
}


// Combo field = dropdown-to-pick + text input for a custom value. The
// dropdown always shows every suggestion (browsers filter <datalist> by
// current input text, which hides everything else once a name is set).
// Picking from the dropdown sets the value; typing in the text input
// overrides it (e.g. for sub-agent-internal tool names that aren't in
// /api/tools).
function ComboField({ label, value, onChange, options, placeholder = '' }) {
  const matches = (v) => options.some((o) => (typeof o === 'string' ? o : o.value) === v)
  const isCustom = !!value && !matches(value)
  return (
    <div className="space-y-1.5">
      <span className="text-xs text-gray-600 font-medium">{label}</span>
      <select
        value={isCustom ? '__custom__' : (value || '')}
        onChange={(e) => onChange(e.target.value === '__custom__' ? value : e.target.value)}
        className="w-full px-3 py-2 text-sm border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
      >
        <option value="">— choose —</option>
        {options.map((o) => (
          <option key={typeof o === 'string' ? o : o.value} value={typeof o === 'string' ? o : o.value}>
            {typeof o === 'string' ? o : o.label}
          </option>
        ))}
        <option value="__custom__">Custom…</option>
      </select>
      <input
        type="text"
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2 text-sm border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200 font-mono"
      />
      {isCustom && (
        <p className="text-[11px] text-amber-600">Custom value — not in the registered tool list.</p>
      )}
    </div>
  )
}

function CheckboxField({ label, value, onChange }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={!!value}
        onChange={(e) => onChange(e.target.checked)}
        className="h-3.5 w-3.5"
      />
      <span className="text-xs text-gray-700">{label}</span>
    </label>
  )
}

function JsonField({ label, value, onChange, placeholder = '{}', rows = 8 }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2))
  const [err, setErr] = useState('')
  useEffect(() => {
    setText(JSON.stringify(value ?? {}, null, 2))
  }, [value])
  return (
    <label className="block">
      <span className="text-xs text-gray-600 font-medium">{label}</span>
      <textarea
        value={text}
        onChange={(e) => {
          const v = e.target.value
          setText(v)
          try {
            onChange(JSON.parse(v || '{}'))
            setErr('')
          } catch (ex) {
            setErr(String(ex.message || ex))
          }
        }}
        placeholder={placeholder}
        rows={rows}
        className={`mt-1 w-full px-3 py-2 text-sm font-mono leading-snug border rounded focus:outline-none focus:ring-2 resize-y ${err ? 'border-red-300 focus:ring-red-200' : 'border-gray-200 focus:ring-blue-200'}`}
      />
      {err && <p className="text-[11px] text-red-500 mt-0.5">{err}</p>}
    </label>
  )
}


// --- Parse node ---

function ParseNodeEditor({ data, update }) {
  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />
      <SelectField label="Mode" value={data.mode} onChange={(v) => update('mode', v)}
        options={['regex', 'llm']} includeBlank={false} />

      {data.mode === 'regex' && (
        <Section title="Extractors">
          {(data.extractors || []).map((ex, idx) => (
            <div key={idx} className="flex gap-1.5 items-center">
              <input
                className="flex-1 px-2 py-1 text-xs border border-gray-200 rounded"
                placeholder="slot_name"
                value={ex.slot || ''}
                onChange={(e) => {
                  const next = (data.extractors || []).map((x, i) => i === idx ? { ...x, slot: e.target.value } : x)
                  update('extractors', next)
                }}
              />
              <select
                className="px-2 py-1 text-xs border border-gray-200 rounded bg-white"
                value={ex.parser || ''}
                onChange={(e) => {
                  const next = (data.extractors || []).map((x, i) => i === idx ? { ...x, parser: e.target.value } : x)
                  update('extractors', next)
                }}
              >
                <option value="">—</option>
                {PARSERS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <button
                className="text-red-500 text-[11px] hover:text-red-700"
                onClick={() => update('extractors', (data.extractors || []).filter((_, i) => i !== idx))}
              >✕</button>
            </div>
          ))}
          <button
            onClick={() => update('extractors', [...(data.extractors || []), { slot: '', parser: 'money' }])}
            className="w-full text-xs py-1 border border-dashed border-gray-300 rounded text-gray-600 hover:bg-gray-50"
          >+ Extractor</button>
        </Section>
      )}

      {data.mode === 'llm' && (
        <Section title="LLM parse">
          <MarkdownField label="System prompt" value={data.system_prompt}
            onChange={(v) => update('system_prompt', v)}
            placeholder="Extract X from the user's utterance; return nulls if absent."
            minRows={10} />
          <JsonField label="Output schema (field → {type, nullable})"
            value={data.output_schema}
            onChange={(v) => update('output_schema', v)}
            placeholder='{"amount": {"type": "number", "nullable": true}}' />
          <JsonField label="Writes (field → variable name)"
            value={data.writes}
            onChange={(v) => update('writes', v)}
            placeholder='{"amount": "amount"}' />
          <TextField label="LLM variant (default: sub_agent)"
            value={data.llm_variant}
            onChange={(v) => update('llm_variant', v)}
            placeholder="sub_agent" />
        </Section>
      )}
    </div>
  )
}


// --- Condition node (dispatcher) ---

function ConditionNodeEditor({ data, update, edges, reorderEdges, updateEdgePredicate }) {
  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />
      <div className="rounded-lg bg-sky-50 border border-sky-200 px-3 py-2.5 text-[12px] text-sky-900 leading-relaxed">
        <strong>Deterministic router.</strong> At runtime every predicate is evaluated
        in the order shown below; the <em>first</em> one that returns true decides the
        next node. No LLM is involved. Use the arrows to reorder priority.
      </div>
      <Section title="Outgoing edges (priority = array order)">
        {(edges || []).length === 0 && (
          <p className="text-xs text-gray-400 italic">No outgoing edges. Connect this to targets on the canvas.</p>
        )}
        {(edges || []).map((e, idx) => (
          <div key={e.id} className="border border-gray-200 rounded p-2 space-y-1 bg-gray-50">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-gray-700">#{idx} → {e.target}</span>
              <div className="flex gap-1">
                <button
                  onClick={() => reorderEdges(idx, -1)}
                  disabled={idx === 0}
                  className="text-[11px] px-1.5 py-0.5 bg-white border border-gray-200 rounded disabled:opacity-30"
                >↑</button>
                <button
                  onClick={() => reorderEdges(idx, 1)}
                  disabled={idx === edges.length - 1}
                  className="text-[11px] px-1.5 py-0.5 bg-white border border-gray-200 rounded disabled:opacity-30"
                >↓</button>
              </div>
            </div>
            <TextField
              label="Predicate (DSL)"
              value={e.predicate || ''}
              onChange={(v) => updateEdgePredicate(e.id, v)}
              placeholder="has(variables.amount) && variables.amount > 0"
            />
          </div>
        ))}
      </Section>
      <p className="text-[11px] text-gray-400 italic">
        At runtime, the first edge whose predicate is true wins. Use `has(x)`, `is_empty(x)`, `==`, `!=`, `{'&&'} / ||`, `!`.
      </p>
    </div>
  )
}


// --- Tool call node ---

function ToolCallNodeEditor({ data, update, nodeIds, agentName }) {
  const [tools, setTools] = useState(null)
  useEffect(() => {
    const q = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : ''
    fetch(`/api/tools${q}`).then((r) => r.json()).then((d) => setTools(d || [])).catch(() => setTools([]))
  }, [agentName])

  // tool_call_node dispatches AgentTools (tools with declared actions) via
  // tool.dispatch(action, params, context). Plain BaseTools don't have a
  // dispatch surface — showing them here would only mislead. BaseTools are
  // still selectable in llm_node's `tools` field where they're called by
  // the LLM directly through the tool_caller closure.
  const rawList = Array.isArray(tools) ? tools : []
  const list = rawList.filter((t) => Array.isArray(t.actions) && t.actions.length > 0)
  const toolByName = new Map(list.map((t) => [t.name, t]))
  const currentTool = data.tool ? toolByName.get(data.tool) : null
  const actions = currentTool?.actions || []

  const toolOptions = list.map((t) => ({
    value: t.name,
    label: t.agent_scoped ? `${t.name} — ${t.agent || 'sub-agent'}` : t.name,
  }))
  const actionOptions = actions.map((a) => ({
    value: a.name,
    label: a.description ? `${a.name} — ${a.description.slice(0, 60)}${a.description.length > 60 ? '…' : ''}` : a.name,
  }))

  const currentAction = actions.find((a) => a.name === data.action)

  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />

      <ComboField
        label="Tool"
        value={data.tool}
        onChange={(v) => {
          update('tool', v)
          // Clear action when switching tools — previous action likely invalid.
          update('action', '')
        }}
        options={toolOptions}
        placeholder="Tool name"
      />
      {currentTool?.description && (
        <p className="text-[11px] text-gray-500 italic -mt-1">{currentTool.description}</p>
      )}

      {actions.length > 0 ? (
        <SelectField
          label="Action"
          value={data.action || ''}
          onChange={(v) => update('action', v)}
          options={actionOptions}
          includeBlank={true}
        />
      ) : (
        <TextField
          label="Action (optional — tool has no declared actions)"
          value={data.action || ''}
          onChange={(v) => update('action', v || null)}
          placeholder="validate / submit / etc."
        />
      )}
      {currentAction?.description && (
        <p className="text-[11px] text-gray-500 italic -mt-1">{currentAction.description}</p>
      )}

      <ParamsEditor
        schema={currentAction?.params_schema}
        value={data.params}
        onChange={(v) => update('params', v)} />

      <TextField label="Output variable" value={data.output_var}
        onChange={(v) => update('output_var', v)}
        placeholder="validation_result" />
      <OutputSchemaPreview
        schema={currentAction?.output_schema}
        outputVar={data.output_var || ''} />

      <JsonField label="post_write (state resets on success)"
        value={data.post_write}
        onChange={(v) => update('post_write', v)}
        placeholder='{"acknowledged_validation_failure": false}' />
      <SelectField label="On error →" value={data.on_error}
        onChange={(v) => update('on_error', v)}
        options={['abort', ...nodeIds]} includeBlank={false} />
    </div>
  )
}


// --- Params editor: schema-driven form with per-field inputs ---
//
// When the action's params_schema declares `properties`, render a typed form
// instead of a free-form JSON textarea. Each property becomes its own input
// picked by type (text / number / checkbox / dropdown). Template strings
// (`{{variables.X}}`) are accepted in any string-typed field — store as a
// string regardless of declared numeric type.
//
// Fallback to JsonField when:
//   - no schema declared on the action
//   - additionalProperties: true (free-shape)
//   - schema has no `properties` (legacy / passthrough actions)

function ParamsEditor({ schema, value, onChange }) {
  const props = schema?.properties || null
  const noKnownProps = !props || Object.keys(props).length === 0
  const allowExtras = schema?.additionalProperties === true

  if (noKnownProps || allowExtras) {
    return (
      <div className="space-y-1">
        <JsonField label="Params (templated — {{variables.X}} is resolved)"
          value={value || {}}
          onChange={(v) => onChange(v)}
          placeholder={schema ? JSON.stringify(sampleFromSchema(schema), null, 2) : '{"amount": "{{variables.amount}}"}'} />
        {noKnownProps && schema && (
          <p className="text-[11px] text-gray-400 italic">
            This action declares no fixed parameters. Use JSON for free-form input.
          </p>
        )}
      </div>
    )
  }

  const params = (typeof value === 'object' && value !== null) ? value : {}
  const required = new Set(schema.required || [])

  const setField = (key, v) => {
    const next = { ...params }
    if (v === undefined) delete next[key]
    else next[key] = v
    onChange(next)
  }

  return (
    <div className="space-y-2">
      <div className="text-xs text-gray-600 font-medium">Params (typed inputs from schema)</div>
      {Object.entries(props).map(([key, spec]) => (
        <ParamField key={key}
          name={key}
          spec={spec}
          required={required.has(key)}
          value={params[key]}
          onChange={(v) => setField(key, v)} />
      ))}
    </div>
  )
}

// Single param field — picks its rendering by type. Template strings are
// allowed for every field (so even a `number` field accepts
// `{{variables.amount}}`). The "T" template toggle lets the author switch
// between literal-value mode and template-string mode.
function ParamField({ name, spec, required, value, onChange }) {
  const declaredType = Array.isArray(spec?.type)
    ? spec.type.find((t) => t !== 'null')
    : (spec?.type || 'string')
  const nullable = spec?.nullable === true || (Array.isArray(spec?.type) && spec.type.includes('null'))
  const isTemplate = typeof value === 'string' && /\{\{/.test(value)
  const headerName = `${name}${required ? ' *' : ''}`

  // Enum: dropdown (no template mode — enum values are fixed)
  if (Array.isArray(spec?.enum) && spec.enum.length) {
    return (
      <label className="block">
        <span className="text-[11px] text-gray-600">{headerName} <span className="text-gray-400">enum</span></span>
        <select
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)}
          className="mt-0.5 w-full px-2 py-1 text-sm border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
        >
          {!required && <option value="">—</option>}
          {spec.enum.map((opt) => <option key={String(opt)} value={opt}>{String(opt)}</option>)}
        </select>
        {spec.description && <span className="block text-[10px] text-gray-400 italic">{spec.description}</span>}
      </label>
    )
  }

  // Boolean: checkbox (template mode opt-in)
  if (declaredType === 'boolean' && !isTemplate) {
    return (
      <div className="flex items-center justify-between gap-2 py-0.5">
        <label className="flex items-center gap-2 cursor-pointer flex-1">
          <input type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
            className="h-3.5 w-3.5" />
          <span className="text-[11px] text-gray-700">{headerName} <span className="text-gray-400">bool</span></span>
        </label>
        <TemplateToggle onSwitch={() => onChange('{{variables.X}}')} />
      </div>
    )
  }

  // Object / array fall back to JSON for THIS field only.
  if (declaredType === 'object' || declaredType === 'array') {
    return (
      <JsonField label={`${headerName} — ${declaredType}`}
        value={value ?? (declaredType === 'array' ? [] : {})}
        onChange={onChange} rows={4} />
    )
  }

  // Number / integer / string / default → text-style input. Numbers go
  // through `Number(...)` unless the value looks like a template.
  const inputType = (declaredType === 'number' || declaredType === 'integer') && !isTemplate ? 'number' : 'text'
  const onTextChange = (raw) => {
    if (raw === '') return onChange(nullable ? null : undefined)
    if (/^\{\{/.test(raw) || inputType === 'text') return onChange(raw)
    const n = Number(raw)
    onChange(Number.isFinite(n) ? n : raw)
  }
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-gray-600">{headerName} <span className="text-gray-400">{declaredType}{isTemplate ? ' (template)' : ''}</span></span>
        {isTemplate
          ? <TemplateToggle off onSwitch={() => onChange(declaredType === 'boolean' ? false : (declaredType === 'number' ? 0 : ''))} />
          : <TemplateToggle onSwitch={() => onChange('{{variables.X}}')} />}
      </div>
      <input
        type={inputType}
        value={value ?? ''}
        onChange={(e) => onTextChange(e.target.value)}
        placeholder={spec?.description || (inputType === 'number' ? '0' : 'value or {{variables.X}}')}
        className="w-full px-2 py-1 text-sm border border-gray-200 rounded focus:outline-none focus:ring-2 focus:ring-blue-200"
      />
      {spec?.description && <span className="block text-[10px] text-gray-400 italic">{spec.description}</span>}
    </div>
  )
}

function TemplateToggle({ off = false, onSwitch }) {
  return (
    <button type="button" onClick={onSwitch}
      title={off ? 'Switch back to literal value' : 'Use a {{variables.X}} template instead'}
      className={`text-[10px] px-1.5 py-0.5 rounded border ${off ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50'}`}>
      {off ? 'literal' : '{{}}'}
    </button>
  )
}


// --- Output schema preview: shows what fields the action will return ---
//
// Helps the author write `{{variables.<output_var>.<field>}}` references in
// downstream text_template / widget data_template fields. Each field gets a
// copy-to-clipboard button that yields the full template token.

function OutputSchemaPreview({ schema, outputVar }) {
  if (!schema || typeof schema !== 'object') return null
  const props = schema.properties || {}
  const keys = Object.keys(props)
  if (!keys.length) return null
  const slot = outputVar || '<output_var>'

  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text) } catch { /* ignore */ }
  }

  return (
    <details className="text-[11px] border border-gray-100 rounded bg-gray-50" open>
      <summary className="cursor-pointer select-none px-2 py-1 font-medium text-gray-600">
        Returns — click a field to copy its template token
      </summary>
      <div className="px-2 py-1.5 space-y-1">
        {keys.map((k) => {
          const fieldType = props[k]?.type
          const token = `{{variables.${slot}.${k}}}`
          const typeLabel = Array.isArray(fieldType) ? fieldType.join('|') : (fieldType || 'any')
          return (
            <button key={k} type="button" onClick={() => copy(token)}
              className="block w-full text-left px-1.5 py-1 hover:bg-white rounded border border-transparent hover:border-gray-200">
              <span className="font-mono text-gray-800">{k}</span>
              <span className="ml-1.5 text-gray-400">{typeLabel}</span>
              <span className="block font-mono text-[10px] text-blue-700 mt-0.5">{token}</span>
              {props[k]?.description && (
                <span className="block text-gray-400 italic mt-0.5">{props[k].description}</span>
              )}
            </button>
          )
        })}
      </div>
    </details>
  )
}

function sampleFromSchema(schema) {
  if (!schema || typeof schema !== 'object') return {}
  const props = schema.properties || {}
  const sample = {}
  for (const [key, spec] of Object.entries(props)) {
    if (spec.default !== undefined) sample[key] = spec.default
    else if (spec.type === 'number') sample[key] = '{{variables.amount}}'
    else if (spec.type === 'string') sample[key] = '{{variables.X}}'
    else if (spec.type === 'array') sample[key] = '{{variables.X}}'
    else sample[key] = null
  }
  return sample
}


// --- Interrupt node ---

function InterruptNodeEditor({ data, update, slotNames, supportedChannels }) {
  const channels = Array.isArray(supportedChannels) && supportedChannels.length
    ? supportedChannels
    : ['chat']
  const showChat = channels.includes('chat')
  const showVoice = channels.includes('voice')
  const multiChannel = showChat && showVoice

  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />

      {showChat && (
        <TextField label={multiChannel ? 'Prompt (chat)' : 'Prompt'}
          value={data.prompt_template}
          onChange={(v) => update('prompt_template', v)}
          placeholder="What matters most to you in a card?" multiline />
      )}
      {showVoice && (
        <TextField label={multiChannel ? 'Voice prompt (optional override)' : 'Voice prompt'}
          value={data.voice_prompt_template}
          onChange={(v) => update('voice_prompt_template', v)}
          placeholder="What matters most to you?" multiline />
      )}
      {!showChat && !showVoice && (
        <p className="text-[11px] text-amber-600 italic">
          This template declares no supported channels — please set at least one in General settings.
        </p>
      )}

      <SelectField label="Targets slot (for retry tracking)"
        value={data.targets_slot}
        onChange={(v) => update('targets_slot', v || null)}
        options={['', ...slotNames]} />
      <p className="text-[11px] text-gray-400 italic">
        Emits the prompt to the user, then pauses the inner graph. On the
        user's reply, the sub-agent re-runs from <code>entry_node</code>
        with the reply appended to messages — typically your parse_node
        extracts the new info and dispatch routes again.
      </p>
    </div>
  )
}


// --- LLM node (free-form) ---

function LlmNodeEditor({ data, update }) {
  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />
      <MarkdownField label="System prompt" value={data.system_prompt}
        onChange={(v) => update('system_prompt', v)}
        placeholder="You are the Help sub-agent..."
        minRows={12} />
      <TextField label="Tools (comma-separated names)"
        value={(data.tools || []).join(', ')}
        onChange={(v) => update('tools', v.split(',').map((s) => s.trim()).filter(Boolean))}
        placeholder="knowledge_search, get_profile_data" />
      <JsonField label="Output schema (optional — required for regulated sub-agents)"
        value={data.output_schema || {}}
        onChange={(v) => update('output_schema', Object.keys(v).length ? v : null)} />
    </div>
  )
}


// --- Tool node (executes prior LLM tool_calls) ---

function ToolNodeEditor({ data, update }) {
  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />
      <p className="text-[11px] text-gray-400 italic">
        No configuration. Runs the tool_calls emitted by the previous llm_node.
      </p>
    </div>
  )
}


// --- Response node (4 return modes) ---

function ResponseNodeEditor({ data, update }) {
  const rm = data.return_mode || 'to_orchestrator'
  const widget = data.widget || {}
  const updateWidget = (k, v) => update('widget', { ...widget, [k]: v })

  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />
      <SelectField label="Return mode" value={rm}
        onChange={(v) => update('return_mode', v)}
        options={RETURN_MODES} includeBlank={false} />
      <SelectField label="Variant (visual badge + routing hint)"
        value={data.kind || 'normal'}
        onChange={(v) => update('kind', v === 'normal' ? '' : v)}
        options={[
          { value: 'normal',  label: 'Normal' },
          { value: 'failure', label: 'Failure — sub-agent failed' },
          { value: 'retry',   label: 'Retry exhausted — slot capture gave up' },
        ]}
        includeBlank={false} />
      <CheckboxField label="Escape target (routed to on abort / topic_change)"
        value={data.is_escape_target}
        onChange={(v) => update('is_escape_target', v)} />

      {rm === 'widget' && (
        <Section title="Widget">
          <TextField label="Widget type" value={widget.widget_type}
            onChange={(v) => updateWidget('widget_type', v)}
            placeholder="transfer_form / profile_card / …" />
          <TextField label="Title" value={widget.title}
            onChange={(v) => updateWidget('title', v)}
            placeholder="Confirm transfer" />
          <JsonField label="Data template ({{variables.X}} is resolved)"
            value={widget.data_template}
            onChange={(v) => updateWidget('data_template', v)} />
        </Section>
      )}

      {rm === 'glass' && (
        <TextField label="Glass template (TTS-ready)" value={data.glass_template}
          onChange={(v) => update('glass_template', v)}
          placeholder="Done. I transferred ${{variables.amount}}..." multiline />
      )}

      {rm === 'to_presenter' && (
        <>
          <JsonField label="Slot writes (main_slot → {{sub.variable}})"
            value={data.slot_writes}
            onChange={(v) => update('slot_writes', v)} />
          <p className="text-[11px] text-amber-600 italic">
            Regulated sub-agents cannot use this mode — the loader rejects it.
          </p>
        </>
      )}

      {rm === 'to_orchestrator' && (
        <TextField label="Text template (the parent LLM paraphrases)"
          value={data.text_template}
          onChange={(v) => update('text_template', v)}
          placeholder="I finished the transfer of ${{variables.amount}}." multiline />
      )}
    </div>
  )
}


// --- Edge editor ---

function EdgeEditor({ edge, allEdges, allNodes, onEdgesUpdate, onClose }) {
  const idx = (allEdges || []).findIndex((e) => e.id === edge.id)
  const total = (allEdges || []).length
  const sourceNode = (allNodes || []).find((n) => n.id === edge.source)
  const targetNode = (allNodes || []).find((n) => n.id === edge.target)

  const updateField = (field, value) => {
    const next = (allEdges || []).map((e) => e.id === edge.id ? { ...e, [field]: value } : e)
    onEdgesUpdate?.(next)
  }
  const reorder = (delta) => {
    const j = idx + delta
    if (j < 0 || j >= total) return
    const next = (allEdges || []).slice()
    ;[next[idx], next[j]] = [next[j], next[idx]]
    onEdgesUpdate?.(next)
  }
  const deleteEdge = () => {
    if (!confirm('Delete this edge?')) return
    const next = (allEdges || []).filter((e) => e.id !== edge.id)
    onEdgesUpdate?.(next)
    onClose?.()
  }

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-gray-500 space-y-1">
        <div><span className="font-medium text-gray-700">From:</span> {sourceNode?.id} <span className="text-gray-400">({sourceNode?.type})</span></div>
        <div><span className="font-medium text-gray-700">To:</span> {targetNode?.id} <span className="text-gray-400">({targetNode?.type})</span></div>
        <div><span className="font-medium text-gray-700">Order:</span> #{idx} of {total} — lower indexes evaluate first</div>
      </div>
      <TextField label="Predicate (DSL — see docs Part 4.1)"
        value={edge.predicate || ''}
        onChange={(v) => updateField('predicate', v || null)}
        placeholder='has(variables.X) && variables.X != "Y"' multiline />
      <TextField label="Label (optional — shown on the edge)"
        value={edge.label || ''}
        onChange={(v) => updateField('label', v || null)}
        placeholder="leave blank to show the predicate" />
      <div className="flex items-center gap-2 pt-2">
        <button onClick={() => reorder(-1)} disabled={idx <= 0}
          className="px-2 py-1 text-xs border border-gray-200 rounded hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed">↑ move up</button>
        <button onClick={() => reorder(1)} disabled={idx >= total - 1}
          className="px-2 py-1 text-xs border border-gray-200 rounded hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed">↓ move down</button>
        <button onClick={deleteEdge}
          className="ml-auto px-2 py-1 text-xs border border-rose-200 text-rose-700 rounded hover:bg-rose-50">Delete edge</button>
      </div>
      <p className="text-[11px] text-gray-400 italic pt-2 border-t border-gray-100">
        Tip: drag this edge's endpoint to a different node to re-route, or press <kbd className="px-1 py-0.5 bg-gray-100 border border-gray-200 rounded text-[10px]">Delete</kbd> with the edge selected.
      </p>
    </div>
  )
}


// --- Main panel ---

export default function NodePropertiesPanel({ node, edge, onEdgeDeselect, allNodes, allEdges, agentName, supportedChannels, onUpdate, onEdgesUpdate }) {
  if (edge) {
    return (
      <div className="p-4 space-y-4 overflow-y-auto h-full">
        <div className="space-y-0.5 sticky top-0 bg-white -mx-4 pl-4 pr-20 py-2 border-b border-gray-100 z-10">
          <div className="text-[10px] uppercase tracking-wide text-gray-400 font-mono">edge</div>
          <h3 className="text-sm font-semibold text-gray-800 truncate">{edge.id}</h3>
        </div>
        <EdgeEditor edge={edge} allEdges={allEdges} allNodes={allNodes} onEdgesUpdate={onEdgesUpdate} onClose={onEdgeDeselect} />
      </div>
    )
  }
  if (!node) {
    return (
      <div className="p-4 text-center">
        <p className="text-xs text-gray-400 italic">Select a node or edge to edit its properties.</p>
      </div>
    )
  }

  const update = (field, value) => {
    onUpdate(node.id, { ...node.data, [field]: value })
  }

  const nodeIds = (allNodes || []).map((n) => n.id).filter((id) => id !== node.id)

  // Slot names inferred from writes / extractors / targets_slot on parse + interrupt nodes.
  const slotNames = Array.from(new Set([
    ...((allNodes || []).flatMap((n) =>
      n.type === 'parse_node'
        ? [...Object.values(n.data?.writes || {}), ...(n.data?.extractors || []).map((x) => x.slot).filter(Boolean)]
        : n.type === 'interrupt_node' && n.data?.targets_slot
        ? [n.data.targets_slot]
        : []
    )),
  ])).filter(Boolean)

  const outgoingEdges = (allEdges || [])
    .filter((e) => e.source === node.id)

  const reorderEdges = (idx, delta) => {
    const globalIdxOfItem = (allEdges || []).findIndex((e) => e.id === outgoingEdges[idx].id)
    const swapItem = outgoingEdges[idx + delta]
    if (!swapItem) return
    const globalIdxOfSwap = (allEdges || []).findIndex((e) => e.id === swapItem.id)
    const next = (allEdges || []).slice()
    ;[next[globalIdxOfItem], next[globalIdxOfSwap]] = [next[globalIdxOfSwap], next[globalIdxOfItem]]
    onEdgesUpdate?.(next)
  }

  const updateEdgePredicate = (edgeId, predicate) => {
    const next = (allEdges || []).map((e) => e.id === edgeId ? { ...e, predicate } : e)
    onEdgesUpdate?.(next)
  }

  return (
    <div className="p-4 space-y-4 overflow-y-auto h-full">
      <div className="space-y-0.5 sticky top-0 bg-white -mx-4 pl-4 pr-20 py-2 border-b border-gray-100 z-10">
        <div className="text-[10px] uppercase tracking-wide text-gray-400 font-mono">{node.type}</div>
        <h3 className="text-sm font-semibold text-gray-800">{node.id}</h3>
      </div>

      {node.type === 'parse_node' && (
        <ParseNodeEditor data={node.data} update={update} />
      )}
      {node.type === 'condition_node' && (
        <ConditionNodeEditor data={node.data} update={update}
          edges={outgoingEdges}
          reorderEdges={reorderEdges}
          updateEdgePredicate={updateEdgePredicate} />
      )}
      {node.type === 'tool_call_node' && (
        <ToolCallNodeEditor data={node.data} update={update} nodeIds={nodeIds} agentName={agentName} />
      )}
      {node.type === 'interrupt_node' && (
        <InterruptNodeEditor data={node.data} update={update} slotNames={slotNames} supportedChannels={supportedChannels} />
      )}
      {node.type === 'llm_node' && (
        <LlmNodeEditor data={node.data} update={update} />
      )}
      {node.type === 'tool_node' && (
        <ToolNodeEditor data={node.data} update={update} />
      )}
      {node.type === 'response_node' && (
        <ResponseNodeEditor data={node.data} update={update} />
      )}
    </div>
  )
}
