import { Handle, Position } from 'reactflow'

// Handle styling — green = source (drag FROM), blue = target (drop ON).
// Slightly larger than reactflow's default 6px so they're easier to grab;
// hover grows them further and adds a ring for unambiguous targeting.
// The `!` prefix overrides reactflow's injected default styles.
const HANDLE_BASE = '!w-2.5 !h-2.5 !border-2 !border-white transition-all duration-100 hover:!w-3.5 hover:!h-3.5 hover:!ring-2 cursor-crosshair'
const HANDLE_SOURCE = `${HANDLE_BASE} !bg-emerald-500 hover:!ring-emerald-300`
const HANDLE_TARGET = `${HANDLE_BASE} !bg-blue-500 hover:!ring-blue-300`

const STYLE = {
  parse_node:     { bg: 'bg-sky-50',     border: 'border-sky-300',     text: 'text-sky-800',     icon: '📥', title: 'Parse' },
  condition_node: { bg: 'bg-amber-50',   border: 'border-amber-300',   text: 'text-amber-800',   icon: '🔀', title: 'Condition' },
  tool_call_node: { bg: 'bg-violet-50',  border: 'border-violet-300',  text: 'text-violet-800',  icon: '🔧', title: 'Tool Call' },
  parallel_tools_node: { bg: 'bg-violet-50', border: 'border-violet-400', text: 'text-violet-900', icon: '🔧🔧', title: 'Parallel Tools' },
  interrupt_node: { bg: 'bg-rose-50',    border: 'border-rose-300',    text: 'text-rose-800',    icon: '⏸',  title: 'Interrupt' },
  llm_node:       { bg: 'bg-indigo-50',  border: 'border-indigo-300',  text: 'text-indigo-800',  icon: '🤖', title: 'LLM' },
  tool_node:      { bg: 'bg-slate-50',   border: 'border-slate-300',   text: 'text-slate-800',   icon: '⚙️', title: 'Tool Run' },
  response_node:  { bg: 'bg-emerald-50', border: 'border-emerald-300', text: 'text-emerald-800', icon: '✅', title: 'Respond' },
}

function summary(type, data) {
  if (!data) return ''
  switch (type) {
    case 'parse_node':
      return data.mode === 'llm'
        ? `LLM parse · ${Object.keys(data.output_schema || {}).length} fields`
        : `regex · ${(data.extractors || []).length} extractors`
    case 'condition_node':
      return 'Dispatcher (see edges)'
    case 'tool_call_node':
      return data.tool ? `${data.tool} → ${data.output_var || '?'}` : 'no tool'
    case 'parallel_tools_node': {
      const entries = Array.isArray(data.tools) ? data.tools : []
      if (!entries.length) return 'no entries'
      const names = entries.map((e) => e.tool).filter(Boolean)
      return `${entries.length} in parallel · ${names.slice(0, 2).join(', ')}${names.length > 2 ? ', …' : ''}`
    }
    case 'interrupt_node':
      return data.targets_slot ? `→ ${data.targets_slot}` : 'general prompt'
    case 'llm_node':
      return data.system_prompt ? 'has prompt' : 'no prompt'
    case 'tool_node':
      return 'runs prior LLM tool_calls'
    case 'response_node':
      return `mode: ${data.return_mode || 'to_orchestrator'}`
    default:
      return ''
  }
}

// Badge resolution — explicit data.kind wins. The id/label regex below is a
// fallback so existing seed templates (response_failed, response_retry_*)
// keep their visual hint without needing a template edit.
function responseBadge(id, data) {
  if (!data) return null
  const kind = data.kind || ''
  if (kind === 'failure') return { text: 'failure', cls: 'bg-rose-100 text-rose-700' }
  if (kind === 'retry')   return { text: 'retry exhausted', cls: 'bg-amber-100 text-amber-700' }
  if (data.is_escape_target) return { text: 'escape target', cls: 'bg-amber-100 text-amber-700' }
  // Fallback heuristics for older templates without explicit kind.
  if (!kind && (/retry/i.test(id) || /retry/i.test(data.label || ''))) {
    return { text: 'retry exhausted', cls: 'bg-amber-100 text-amber-700' }
  }
  if (!kind && (/fail/i.test(id) || /fail/i.test(data.label || ''))) {
    return { text: 'failure', cls: 'bg-rose-100 text-rose-700' }
  }
  if ((data.return_mode || 'to_orchestrator') === 'widget') {
    return { text: 'widget', cls: 'bg-emerald-100 text-emerald-700' }
  }
  if (data.return_mode === 'glass') {
    return { text: 'glass', cls: 'bg-emerald-100 text-emerald-700' }
  }
  return null
}

export default function SubAgentNode({ id, type, data, selected }) {
  const style = STYLE[type] || { bg: 'bg-gray-50', border: 'border-gray-300', text: 'text-gray-700', icon: '•', title: type }
  const label = (data && data.label) || style.title
  const badge = type === 'response_node' ? responseBadge(id, data) : null
  return (
    <div
      className={`rounded-lg border ${style.border} ${style.bg} shadow-sm px-3 py-2 min-w-[160px] max-w-[200px] ${selected ? 'ring-2 ring-blue-400' : ''}`}
    >
      {/* Six handles per node — the canvas picks which ones each edge uses
          based on the source/target node positions in the two-column
          dispatcher layout. Side handles are split on Y (40% / 60%) so
          forward and loop edges between the same pair don't overlap.

          Colour convention: sources are green (drag FROM), targets are
          blue (drop ON). Handles grow + show a ring on hover so users
          can see exactly where to grab. */}
      <Handle type="target" position={Position.Top} id="t" className={HANDLE_TARGET} />
      <Handle type="source" position={Position.Bottom} id="b" className={HANDLE_SOURCE} />
      <Handle type="source" position={Position.Left} id="l-out" style={{ top: '60%' }} className={HANDLE_SOURCE} />
      <Handle type="target" position={Position.Left} id="l-in" style={{ top: '40%' }} className={HANDLE_TARGET} />
      <Handle type="source" position={Position.Right} id="r-out" style={{ top: '60%' }} className={HANDLE_SOURCE} />
      <Handle type="target" position={Position.Right} id="r-in" style={{ top: '40%' }} className={HANDLE_TARGET} />
      <div className="flex items-center gap-1.5 justify-between">
        <div className="flex items-center gap-1.5">
          <span className="text-base leading-none">{style.icon}</span>
          <span className={`text-[11px] font-semibold uppercase tracking-wide ${style.text}`}>{style.title}</span>
        </div>
        {badge && (
          <span className={`text-[9px] font-medium px-1.5 py-0.5 rounded ${badge.cls}`}>{badge.text}</span>
        )}
      </div>
      <div className="mt-1 text-xs text-gray-800 font-medium truncate">{label}</div>
      <div className="text-[11px] text-gray-500 truncate">{summary(type, data)}</div>
    </div>
  )
}

// Per-type wrappers so reactflow's nodeTypes map dispatches correctly. Each
// wrapper just forwards to SubAgentNode with its type prop — keeps the
// visual component single-source.
export const ParseNode     = (props) => <SubAgentNode {...props} type="parse_node" />
export const ConditionNode = (props) => <SubAgentNode {...props} type="condition_node" />
export const ToolCallNode  = (props) => <SubAgentNode {...props} type="tool_call_node" />
export const ParallelToolsNode = (props) => <SubAgentNode {...props} type="parallel_tools_node" />
export const InterruptNode = (props) => <SubAgentNode {...props} type="interrupt_node" />
export const LlmNode       = (props) => <SubAgentNode {...props} type="llm_node" />
export const ToolNode      = (props) => <SubAgentNode {...props} type="tool_node" />
export const ResponseNode  = (props) => <SubAgentNode {...props} type="response_node" />
