import { useState } from 'react'

// v4 sub-agent node palette. Matches backend/app/agents/nodes/ registrations.
const NODE_TYPES = [
  { type: 'parse_node',     label: 'Parse',     icon: '📥', description: 'Extract values from the latest user message (regex or LLM).' },
  { type: 'condition_node', label: 'Condition', icon: '🔀', description: 'Dispatcher — routes to a node by evaluating predicates in order.' },
  { type: 'tool_call_node', label: 'Tool Call', icon: '🔧', description: 'Call a tool directly with templated params; write result to a variable.' },
  { type: 'interrupt_node', label: 'Interrupt', icon: '⏸', description: 'Pause and ask the user. The outer graph resumes with their reply.' },
  { type: 'llm_node',       label: 'LLM',       icon: '🤖', description: 'Free-form LLM turn with a scoped prompt and optional tool subset.' },
  { type: 'tool_node',      label: 'Tool Run',  icon: '⚙️', description: 'Execute tool calls emitted by the previous llm_node.' },
  { type: 'response_node',  label: 'Respond',   icon: '✅', description: 'Terminal — emit widget / glass / text (4 return modes).' },
]

export default function AddNodeMenu({ onAdd }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-sm text-gray-700 hover:border-gray-400 shadow-sm cursor-pointer"
      >
        <span>+</span> Add
      </button>
      {open && (
        <div className="absolute bottom-10 left-0 z-10 w-72 bg-white border border-gray-200 rounded-lg shadow-lg py-1">
          {NODE_TYPES.map((nt) => (
            <button
              key={nt.type}
              onClick={() => { onAdd(nt.type); setOpen(false) }}
              className="w-full text-left px-3 py-2 hover:bg-gray-50 cursor-pointer"
            >
              <div className="flex items-center gap-2">
                <span>{nt.icon}</span>
                <span className="text-sm font-medium text-gray-800">{nt.label}</span>
              </div>
              <p className="text-xs text-gray-400 ml-6">{nt.description}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
