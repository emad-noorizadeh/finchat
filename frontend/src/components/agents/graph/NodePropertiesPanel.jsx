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

  const list = Array.isArray(tools) ? tools : []
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

      <JsonField label="Params (templated — {{variables.X}} is resolved)"
        value={data.params}
        onChange={(v) => update('params', v)}
        placeholder={currentAction?.params_schema ? JSON.stringify(sampleFromSchema(currentAction.params_schema), null, 2) : '{"amount": "{{variables.amount}}"}'} />
      {currentAction?.params_schema?.properties && (
        <details className="text-[11px] text-gray-500">
          <summary className="cursor-pointer select-none">Expected params schema</summary>
          <pre className="mt-1 p-2 bg-gray-50 border border-gray-200 rounded overflow-x-auto">
            {JSON.stringify(currentAction.params_schema, null, 2)}
          </pre>
        </details>
      )}

      <TextField label="Output variable" value={data.output_var}
        onChange={(v) => update('output_var', v)}
        placeholder="validation_result" />
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

function InterruptNodeEditor({ data, update, slotNames }) {
  return (
    <div className="space-y-3">
      <TextField label="Node label" value={data.label} onChange={(v) => update('label', v)} />
      <TextField label="Prompt (chat)" value={data.prompt_template}
        onChange={(v) => update('prompt_template', v)}
        placeholder="How much would you like to transfer?" multiline />
      <TextField label="Voice prompt (optional override)" value={data.voice_prompt_template}
        onChange={(v) => update('voice_prompt_template', v)}
        placeholder="How much should I transfer?" multiline />
      <SelectField label="Targets slot (for retry tracking)"
        value={data.targets_slot}
        onChange={(v) => update('targets_slot', v || null)}
        options={['', ...slotNames]} />
      <p className="text-[11px] text-gray-400 italic">
        Emits a pause. The outer graph waits for the user's reply, then
        re-enters the sub-agent with the reply appended to messages.
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


// --- Main panel ---

export default function NodePropertiesPanel({ node, allNodes, allEdges, agentName, onUpdate, onEdgesUpdate }) {
  if (!node) {
    return (
      <div className="p-4 text-center">
        <p className="text-xs text-gray-400 italic">Select a node to edit its properties.</p>
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
        <InterruptNodeEditor data={node.data} update={update} slotNames={slotNames} />
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
