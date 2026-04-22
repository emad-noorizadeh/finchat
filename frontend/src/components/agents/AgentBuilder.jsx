import { useState, useEffect, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import client from '../../api/client'
import AgentCanvas from './graph/AgentCanvas'
import NodePropertiesPanel from './graph/NodePropertiesPanel'

// Inline icons — Unicode box-glyphs render inconsistently across systems,
// so use SVG so "full screen" is always visible.
const EnterFullIcon = ({ className = 'w-3.5 h-3.5' }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
       strokeLinecap="round" strokeLinejoin="round" className={className}>
    <polyline points="15 3 21 3 21 9" />
    <polyline points="9 21 3 21 3 15" />
    <line x1="21" y1="3" x2="14" y2="10" />
    <line x1="3" y1="21" x2="10" y2="14" />
  </svg>
)
const ExitFullIcon = ({ className = 'w-3.5 h-3.5' }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
       strokeLinecap="round" strokeLinejoin="round" className={className}>
    <polyline points="4 14 10 14 10 20" />
    <polyline points="20 10 14 10 14 4" />
    <line x1="14" y1="10" x2="21" y2="3" />
    <line x1="3" y1="21" x2="10" y2="14" />
  </svg>
)
const ChevronLeft = ({ className = 'w-3.5 h-3.5' }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
       strokeLinecap="round" strokeLinejoin="round" className={className}>
    <polyline points="15 18 9 12 15 6" />
  </svg>
)
const ChevronRight = ({ className = 'w-3.5 h-3.5' }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
       strokeLinecap="round" strokeLinejoin="round" className={className}>
    <polyline points="9 18 15 12 9 6" />
  </svg>
)

// Surfaces every node that carries an LLM prompt (parse_node + llm_node),
// rendered read-only as markdown. Clicking a node opens the right-panel
// editor for fine-grained edits. Replaces the legacy per-agent
// "system prompt" textbox — v4 templates have no agent-level prompt, only
// per-node prompts.
function PromptsOverview({ nodes, onOpenNode }) {
  const prompted = (nodes || []).filter((n) => {
    if (n.type === 'parse_node') return (n.data?.mode === 'llm') && !!n.data?.system_prompt
    if (n.type === 'llm_node')   return !!n.data?.system_prompt
    return false
  })

  if (prompted.length === 0) {
    return (
      <div className="text-xs text-gray-500 space-y-2">
        <p>
          This agent has no LLM prompts. Prompts live on <code>parse_node</code> (mode=llm)
          and <code>llm_node</code> nodes. Add one on the canvas, open the node,
          and the prompt editor will appear here.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-gray-500 leading-relaxed">
        Every LLM prompt this agent uses, rendered from its node. Click the
        node name to open it in the right panel for editing.
      </p>
      {prompted.map((n) => {
        const label = (n.data?.label || n.id)
        const badge = n.type === 'parse_node' ? 'Parse' : 'LLM'
        return (
          <div key={n.id} className="border border-gray-200 rounded-lg bg-gray-50">
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 bg-white rounded-t-lg">
              <div className="min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => onOpenNode?.(n)}
                  className="text-sm font-medium text-blue-600 hover:text-blue-800 hover:underline truncate text-left cursor-pointer"
                >
                  {label}
                </button>
                <div className="text-[10px] text-gray-400 font-mono truncate">{n.id}</div>
              </div>
              <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                {badge}
              </span>
            </div>
            <div className="px-3 py-2 text-xs text-gray-800 prose prose-sm max-w-none
                            prose-headings:mt-2 prose-headings:mb-1 prose-h1:text-sm prose-h2:text-[13px] prose-h3:text-[13px]
                            prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0
                            prose-code:text-[11px] prose-code:bg-white prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-[''] prose-code:after:content-['']">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {n.data.system_prompt}
              </ReactMarkdown>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// v4 sub-agent default graph — parse → condition → response (the minimum
// viable dispatcher shape). Authors extend with interrupt/tool_call/etc.
function buildDefaultGraph() {
  return {
    nodes: [
      {
        id: 'parse',
        type: 'parse_node',
        position: { x: 300, y: 40 },
        data: { label: 'Parse', mode: 'llm', source: 'last_user_message', output_schema: {}, writes: {} },
      },
      {
        id: 'dispatch',
        type: 'condition_node',
        position: { x: 300, y: 200 },
        data: { label: 'Dispatch' },
      },
      {
        id: 'respond',
        type: 'response_node',
        position: { x: 300, y: 360 },
        data: {
          label: 'Respond',
          return_mode: 'to_orchestrator',
          text_template: '',
        },
      },
    ],
    edges: [
      { id: 'e0', source: 'parse',    target: 'dispatch' },
      { id: 'e1', source: 'dispatch', target: 'respond', predicate: 'true' },
    ],
  }
}

const DEFAULT_GRAPH = buildDefaultGraph()

export default function AgentBuilder({ agentName, channel, onSave, onCancel }) {
  const [tools, setTools] = useState([])
  const [saving, setSaving] = useState(false)
  const [selectedNode, setSelectedNode] = useState(null)
  const [settingsTab, setSettingsTab] = useState('general')
  const [loading, setLoading] = useState(false)
  const [patterns, setPatterns] = useState([])
  const [patternMenuOpen, setPatternMenuOpen] = useState(false)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  // "full" = that panel occupies the whole workspace (other panel + canvas
  // hidden). "normal" = three-column layout.
  const [panelFullscreen, setPanelFullscreen] = useState(null)  // 'left' | 'right' | null
  const [leftWidth, setLeftWidth] = useState(288)   // 18rem default
  const [rightWidth, setRightWidth] = useState(320)  // 20rem default
  const dragging = useRef(null) // 'left' | 'right' | null
  const isEdit = !!(agentName && channel)

  // Drag-to-resize handlers
  const handleMouseDown = (side) => (e) => {
    e.preventDefault()
    dragging.current = side
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!dragging.current) return
      if (dragging.current === 'left') {
        const newWidth = Math.max(200, Math.min(500, e.clientX))
        setLeftWidth(newWidth)
      } else if (dragging.current === 'right') {
        const newWidth = Math.max(200, Math.min(500, window.innerWidth - e.clientX))
        setRightWidth(newWidth)
      }
    }
    const handleMouseUp = () => {
      dragging.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  const [form, setForm] = useState({
    name: '',
    channel: 'chat',
    display_name: '',
    description: '',
    search_hint: '',
    system_prompt: '',
    tool_names: [],
    constraints: { require_confirmation: false },
    response_format: 'text',
    is_read_only: false,
    max_iterations: 10,
    created_by: '',
    graph_definition: { ...DEFAULT_GRAPH },
  })

  useEffect(() => {
    client.get('/tools').then((res) => setTools(res.data)).catch(() => {})
    client.get('/agents/patterns').then((res) => setPatterns(res.data || [])).catch(() => {})

    // Load existing agent data if editing
    if (isEdit) {
      setLoading(true)
      client.get(`/agents/${agentName}/${channel}`).then((res) => {
        const d = res.data
        setForm({
          name: d.name || '',
          channel: d.channel || 'chat',
          display_name: d.display_name || '',
          description: d.description || '',
          search_hint: d.search_hint || '',
          system_prompt: d.system_prompt || '',
          tool_names: d.tools || [],
          constraints: {},
          response_format: 'text',
          is_read_only: d.is_read_only || false,
          is_regulated: d.is_regulated || false,
          locked_for_business_user_edit: d.locked_for_business_user_edit || false,
          supported_channels: d.supported_channels || [d.channel || 'chat'],
          entry_node: d.graph_definition?.nodes?.[0]?.id || '',
          template_name: d.template_name || d.name || '',
          status: d.status || 'draft',
          source: d.source || 'user',
          max_iterations: d.max_iterations || 10,
          created_by: '',
          graph_definition: d.graph_definition || buildDefaultGraph(),
        })
        setLoading(false)
      }).catch(() => setLoading(false))
    }
  }, [agentName, channel, isEdit])

  const setField = (field, value) => setForm((f) => ({ ...f, [field]: value }))

  const handleGraphChange = useCallback((graphDef) => {
    setForm((f) => ({ ...f, graph_definition: graphDef }))
  }, [])

  const handleNodeDataChange = (nodeId, newData) => {
    setForm((f) => {
      const updatedNodes = f.graph_definition.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: newData } : n
      )
      return { ...f, graph_definition: { ...f.graph_definition, nodes: updatedNodes } }
    })
  }

  // The backend's AgentUpsertRequest wants a compact payload centred on
  // graph_definition + governance flags. The other "classic" fields
  // (description, tool_names, etc.) are kept in-form for the side panel
  // but aren't persisted yet — Phase 1 of the DB-backed store focuses on
  // the graph. Extend later if we wire system_prompt storage.
  const buildPayload = () => {
    const templateName = form.template_name || `${form.name}_${form.channel}`
    return {
      name: templateName,
      agent_name: form.name,
      channel: form.channel,
      display_name: form.display_name,
      description: form.description || "",
      search_hint: form.search_hint || "",
      graph_definition: form.graph_definition,
      supported_channels: form.supported_channels?.length ? form.supported_channels : [form.channel],
      is_regulated: !!form.is_regulated,
      locked_for_business_user_edit: !!form.locked_for_business_user_edit,
      suspend_resume_allowed: false,
      entry_node: form.entry_node || form.graph_definition?.nodes?.[0]?.id || null,
    }
  }

  const isLocked = !!form.locked_for_business_user_edit

  const handleSave = async (deploy = false) => {
    if (isLocked) {
      alert("This template is locked for business-user edit. Changes must go through code review (edit app/agents/templates/*.json + PR).")
      return
    }
    setSaving(true)
    try {
      const payload = buildPayload()
      // Create-or-update via POST: the store does an upsert by name.
      await client.post('/agents', payload)
      if (deploy) {
        await client.post(`/agents/${encodeURIComponent(payload.name)}/deploy`)
      }
      onSave()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to save')
    }
    setSaving(false)
  }

  if (loading) {
    return (
      <div className="fixed inset-0 bg-gray-100 z-50 flex items-center justify-center">
        <p className="text-gray-400">Loading agent...</p>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-gray-100 z-50 flex flex-col">
      {/* Top bar */}
      <div className="h-14 bg-white border-b border-gray-200 flex items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <button onClick={onCancel} className="text-gray-500 hover:text-gray-700 cursor-pointer">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
            </svg>
          </button>
          <h1 className="text-lg font-semibold text-gray-800">
            {isEdit ? 'Edit Agent' : 'Create Agent'}
          </h1>
          {form.display_name && (
            <span className="text-gray-400">— {form.display_name}</span>
          )}
          {isLocked && (
            <span className="ml-2 px-2 py-0.5 text-[11px] font-medium bg-amber-100 text-amber-800 border border-amber-200 rounded">
              🔒 Regulated — code-reviewed flow (read-only)
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!isEdit && patterns.length > 0 && (
            <div className="relative">
              <button
                onClick={() => setPatternMenuOpen((v) => !v)}
                className="px-3 py-1.5 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 cursor-pointer"
              >Load pattern ▾</button>
              {patternMenuOpen && (
                <div className="absolute top-10 right-0 z-10 w-80 bg-white border border-gray-200 rounded-lg shadow-lg py-1">
                  {patterns.map((p) => (
                    <button
                      key={p.pattern_id}
                      onClick={() => {
                        setField('graph_definition', p.skeleton)
                        setPatternMenuOpen(false)
                      }}
                      className="w-full text-left px-3 py-2 hover:bg-gray-50 cursor-pointer"
                    >
                      <div className="text-sm font-medium text-gray-800">{p.display_name}</div>
                      <p className="text-xs text-gray-400 mt-0.5">{p.description}</p>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={() => handleSave(false)}
            disabled={saving || isLocked || !form.name || !form.display_name}
            title={isLocked ? 'Regulated template — edit through PR / code review' : ''}
            className="px-4 py-1.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            Save as Draft
          </button>
          <button
            onClick={() => handleSave(true)}
            disabled={saving || isLocked || !form.name || !form.display_name}
            title={isLocked ? 'Regulated template — edit through PR / code review' : ''}
            className="px-4 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {saving ? 'Saving...' : 'Save & Deploy'}
          </button>
        </div>
      </div>

      {/* 3-panel layout */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left-panel reopen button (shown when collapsed + not full-screen on right) */}
        {leftCollapsed && panelFullscreen !== 'right' && (
          <button
            onClick={() => setLeftCollapsed(false)}
            title="Show settings"
            className="absolute top-3 left-3 z-30 h-8 px-2.5 bg-white border border-gray-300 rounded-lg shadow-sm text-xs text-gray-700 hover:bg-gray-50 cursor-pointer flex items-center gap-1.5"
          >
            <ChevronRight /> <span>Settings</span>
          </button>
        )}
        {/* Left: Settings */}
        {!leftCollapsed && panelFullscreen !== 'right' && (
        <div
          className="bg-white border-r border-gray-200 overflow-hidden flex-shrink-0 relative"
          style={{ width: panelFullscreen === 'left' ? '100%' : leftWidth }}
        >
          <div className="absolute top-2 right-2 z-40 flex items-center gap-1 bg-white/85 backdrop-blur rounded">
            <button
              onClick={() => setPanelFullscreen(panelFullscreen === 'left' ? null : 'left')}
              title={panelFullscreen === 'left' ? 'Exit full screen' : 'Expand to full screen'}
              className="w-7 h-7 flex items-center justify-center text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded cursor-pointer"
            >
              {panelFullscreen === 'left' ? <ExitFullIcon /> : <EnterFullIcon />}
            </button>
            <button
              onClick={() => setLeftCollapsed(true)}
              title="Hide settings"
              className="w-7 h-7 flex items-center justify-center text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded cursor-pointer"
            >
              <ChevronLeft />
            </button>
          </div>
          <div className="h-full overflow-y-auto" style={{ width: panelFullscreen === 'left' ? '100%' : leftWidth }}>
          {/* Right-padding reserves the top-right corner for the fullscreen /
              collapse icon overlay so the "settings" tab isn't obscured. */}
          <div className="flex border-b border-gray-200 pr-20">
            {['general', 'prompt', 'settings'].map((tab) => (
              <button
                key={tab}
                onClick={() => setSettingsTab(tab)}
                className={`flex-1 py-2.5 text-xs font-medium capitalize cursor-pointer ${
                  settingsTab === tab
                    ? 'text-blue-600 border-b-2 border-blue-600'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          <div className="p-4 space-y-4">
            {settingsTab === 'general' && (
              <>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Display Name</label>
                  <input
                    value={form.display_name}
                    onChange={(e) => {
                      setField('display_name', e.target.value)
                      if (!isEdit) setField('name', e.target.value.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, ''))
                    }}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Name (slug)</label>
                  <input
                    value={form.name}
                    onChange={(e) => setField('name', e.target.value)}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm font-mono focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Channel</label>
                  <select
                    value={form.channel}
                    onChange={(e) => setField('channel', e.target.value)}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:border-blue-500"
                  >
                    <option value="chat">Chat</option>
                    <option value="voice">Voice</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Description</label>
                  <textarea
                    value={form.description}
                    onChange={(e) => setField('description', e.target.value)}
                    rows={3}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:border-blue-500 resize-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Search Hint</label>
                  <input
                    value={form.search_hint}
                    onChange={(e) => setField('search_hint', e.target.value)}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>
              </>
            )}

            {settingsTab === 'prompt' && (
              <PromptsOverview
                nodes={form.graph_definition?.nodes || []}
                onOpenNode={(n) => setSelectedNode({ id: n.id, type: n.type, data: n.data })}
              />
            )}

            {settingsTab === 'settings' && (
              <>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Max Iterations</label>
                  <input
                    type="number" min={1} max={30}
                    value={form.max_iterations}
                    onChange={(e) => setField('max_iterations', parseInt(e.target.value) || 10)}
                    className="w-20 px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Response Format</label>
                  <select
                    value={form.response_format}
                    onChange={(e) => setField('response_format', e.target.value)}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:border-blue-500"
                  >
                    <option value="text">Text</option>
                    <option value="confirmation_card">Confirmation Card</option>
                    <option value="widget">Widget</option>
                  </select>
                </div>
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.is_read_only}
                    onChange={(e) => setField('is_read_only', e.target.checked)}
                  />
                  <span className="text-gray-700">Read Only</span>
                </label>
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.constraints.require_confirmation}
                    onChange={(e) => setField('constraints', { ...form.constraints, require_confirmation: e.target.checked })}
                  />
                  <span className="text-gray-700">Require confirmation</span>
                </label>
              </>
            )}
          </div>
          </div>
        </div>
        )}

        {/* Left resize handle */}
        {!leftCollapsed && !panelFullscreen && (
          <div
            onMouseDown={handleMouseDown('left')}
            className="w-1 hover:w-1.5 bg-transparent hover:bg-blue-400 cursor-col-resize flex-shrink-0 transition-colors"
          />
        )}

        {/* Center: Graph canvas (hidden when a panel is full-screen) */}
        {!panelFullscreen && (
          <div className="flex-1 relative min-w-[300px]">
            <AgentCanvas
              key={`${form.name}-${form.channel}`}
              graphDef={form.graph_definition}
              onChange={handleGraphChange}
              onNodeSelect={(node) => { setSelectedNode(node) }}
            />
          </div>
        )}

        {/* Right-panel reopen button */}
        {selectedNode && rightCollapsed && panelFullscreen !== 'left' && (
          <button
            onClick={() => setRightCollapsed(false)}
            title="Show node properties"
            className="absolute top-3 right-3 z-30 h-8 px-2.5 bg-white border border-gray-300 rounded-lg shadow-sm text-xs text-gray-700 hover:bg-gray-50 cursor-pointer flex items-center gap-1.5"
          >
            <span>Properties</span> <ChevronLeft />
          </button>
        )}

        {/* Right resize handle */}
        {selectedNode && !rightCollapsed && !panelFullscreen && (
          <div
            onMouseDown={handleMouseDown('right')}
            className="w-1 hover:w-1.5 bg-transparent hover:bg-blue-400 cursor-col-resize flex-shrink-0 transition-colors"
          />
        )}

        {/* Right: Node properties */}
        {selectedNode && !rightCollapsed && panelFullscreen !== 'left' && (
          <div
            className="bg-white border-l border-gray-200 overflow-hidden flex-shrink-0 relative"
            style={{ width: panelFullscreen === 'right' ? '100%' : rightWidth }}
          >
            <div className="absolute top-2 right-2 z-40 flex items-center gap-1 bg-white/85 backdrop-blur rounded">
              <button
                onClick={() => setPanelFullscreen(panelFullscreen === 'right' ? null : 'right')}
                title={panelFullscreen === 'right' ? 'Exit full screen' : 'Expand to full screen'}
                className="w-7 h-7 flex items-center justify-center text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded cursor-pointer"
              >
                {panelFullscreen === 'right' ? <ExitFullIcon /> : <EnterFullIcon />}
              </button>
              <button
                onClick={() => setRightCollapsed(true)}
                title="Hide node properties"
                className="w-7 h-7 flex items-center justify-center text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded cursor-pointer"
              >
                <ChevronRight />
              </button>
            </div>
            <NodePropertiesPanel
              node={selectedNode}
              allNodes={form.graph_definition.nodes}
              allEdges={form.graph_definition.edges}
              agentName={form.name}
              onUpdate={handleNodeDataChange}
              onEdgesUpdate={(edges) => setForm((f) => ({
                ...f,
                graph_definition: { ...f.graph_definition, edges },
              }))}
            />
          </div>
        )}
      </div>
    </div>
  )
}
