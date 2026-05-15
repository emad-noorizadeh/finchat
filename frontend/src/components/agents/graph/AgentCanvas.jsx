import { useCallback, useEffect, useRef, useState } from 'react'
import ReactFlow, { Background, Controls, MarkerType, addEdge, reconnectEdge, useNodesState, useEdgesState } from 'reactflow'
import 'reactflow/dist/style.css'

import {
  ParseNode, ConditionNode, ToolCallNode, InterruptNode,
  LlmNode, ToolNode, ResponseNode,
} from './nodes/SubAgentNode'
import AddNodeMenu from './AddNodeMenu'

const nodeTypes = {
  parse_node: ParseNode,
  condition_node: ConditionNode,
  tool_call_node: ToolCallNode,
  interrupt_node: InterruptNode,
  llm_node: LlmNode,
  tool_node: ToolNode,
  response_node: ResponseNode,
}

const DEFAULT_NODE_DATA = {
  parse_node: {
    label: 'Parse',
    mode: 'llm',
    source: 'last_user_message',
    system_prompt: '',
    output_schema: {},
    writes: {},
    extractors: [],
  },
  condition_node: {
    label: 'Dispatch',
  },
  tool_call_node: {
    label: 'Tool Call',
    tool: '',
    params: {},
    output_var: 'result',
    post_write: {},
    on_error: 'abort',
  },
  interrupt_node: {
    label: 'Ask user',
    prompt_template: '',
    voice_prompt_template: '',
    targets_slot: '',
  },
  llm_node: {
    label: 'LLM',
    system_prompt: '',
    tools: [],
    output_schema: null,
  },
  tool_node: {
    label: 'Run tools',
  },
  response_node: {
    label: 'Respond',
    return_mode: 'to_orchestrator',
    text_template: '',
    glass_template: '',
    widget: { widget_type: '', title: '', data_template: {} },
    slot_writes: {},
    is_escape_target: false,
  },
}

// Two-column dispatcher layout — tailored to the regulated-sub-agent shape.
//
//   parse_node(s)           (top row)
//        |
//   condition_node          (center "hub")
//     /       \
//  interrupt   tool_call/llm/tool   (left + right columns)
//     \       /
//   response_node(s)        (bottom row)
//
// Graphs without a condition_node fall back to a layered top-down layout so
// arbitrary sub-agents still render reasonably.

const CENTER_X = 600
const COL_LEFT_X = 250
const COL_RIGHT_X = 950
const Y_TOP = 40
const Y_HUB = 320
const Y_RESP = 680
const Y_COL_STEP = 100
const COMPUTE_TYPES = new Set(['tool_call_node', 'llm_node', 'tool_node'])

function computeTwoColumnLayout(nodes) {
  const positions = new Map()

  const parses = nodes.filter((n) => n.type === 'parse_node')
  const hubs = nodes.filter((n) => n.type === 'condition_node')
  const interrupts = nodes.filter((n) => n.type === 'interrupt_node')
  const computes = nodes.filter((n) => COMPUTE_TYPES.has(n.type))
  const responses = nodes.filter((n) => n.type === 'response_node')

  // Parse row — spread horizontally around center.
  const parseStep = 240
  const parseStartX = CENTER_X - ((parses.length - 1) * parseStep) / 2
  parses.forEach((n, i) => positions.set(n.id, { x: parseStartX + i * parseStep, y: Y_TOP }))

  // Hub(s) — primary condition_node centered; any extras stacked below.
  hubs.forEach((n, i) => positions.set(n.id, { x: CENTER_X, y: Y_HUB + i * 120 }))

  // Left column: interrupts, centered vertically on hub.
  const leftCenterOffset = ((interrupts.length - 1) * Y_COL_STEP) / 2
  interrupts.forEach((n, i) =>
    positions.set(n.id, { x: COL_LEFT_X, y: Y_HUB - leftCenterOffset + i * Y_COL_STEP })
  )

  // Right column: tool_call / llm / tool nodes.
  const rightCenterOffset = ((computes.length - 1) * Y_COL_STEP) / 2
  computes.forEach((n, i) =>
    positions.set(n.id, { x: COL_RIGHT_X, y: Y_HUB - rightCenterOffset + i * Y_COL_STEP })
  )

  // Response row.
  const respStep = 240
  const respStartX = CENTER_X - ((responses.length - 1) * respStep) / 2
  responses.forEach((n, i) => positions.set(n.id, { x: respStartX + i * respStep, y: Y_RESP }))

  // Anything unclassified — place on a tail row so it's visible.
  let tailX = 80
  for (const n of nodes) {
    if (!positions.has(n.id)) {
      positions.set(n.id, { x: tailX, y: Y_RESP + 180 })
      tailX += 240
    }
  }
  return positions
}

function computeLayeredFallback(nodes, edges) {
  // Simple BFS-by-entry depth assignment for non-dispatcher graphs.
  const incoming = new Set(edges.map((e) => e.target))
  const adj = new Map()
  for (const e of edges) {
    if (!adj.has(e.source)) adj.set(e.source, [])
    adj.get(e.source).push(e.target)
  }
  const depth = new Map()
  const queue = nodes.filter((n) => !incoming.has(n.id)).map((n) => n.id)
  for (const id of queue) depth.set(id, 0)
  let idx = 0
  while (idx < queue.length) {
    const id = queue[idx++]
    const d = depth.get(id) ?? 0
    for (const t of adj.get(id) || []) {
      if (!depth.has(t) || depth.get(t) < d + 1) {
        depth.set(t, d + 1)
        queue.push(t)
      }
    }
  }
  const byDepth = new Map()
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0
    if (!byDepth.has(d)) byDepth.set(d, [])
    byDepth.get(d).push(n)
  }
  const positions = new Map()
  for (const [d, group] of byDepth) {
    const y = Y_TOP + d * 160
    const startX = CENTER_X - ((group.length - 1) * 230) / 2
    group.forEach((n, i) => positions.set(n.id, { x: Math.max(40, startX + i * 230), y }))
  }
  return positions
}

function computeLayout(nodes, edges) {
  const hasHub = nodes.some((n) => n.type === 'condition_node')
  return hasHub ? computeTwoColumnLayout(nodes) : computeLayeredFallback(nodes, edges)
}

function ensurePositions(nodes, edges) {
  const layout = computeLayout(nodes, edges)
  return nodes.map((n) => {
    if (n.position && typeof n.position.x === 'number' && typeof n.position.y === 'number') {
      return n.position
    }
    return layout.get(n.id) || { x: 250, y: 40 }
  })
}

// Handle assignment based on the two-column dispatcher layout's canonical
// Y-zones (top row, hub row, column row, response row):
//   - parse row   (y ≈ Y_TOP)
//   - hub row     (y ≈ Y_HUB)
//   - column rows (y spread around Y_HUB on the left / right columns)
//   - response row (y ≈ Y_RESP)
//
// Rules:
//   1. Same column (|Δx| small) → vertical b → t.
//   2. Row transition top→hub or hub→responses → b → t (natural downflow).
//   3. Horizontal between hub and a side column → r-out↔l-in or l-out↔r-in.
//      Side handles split on Y (in at 40%, out at 60%) mean forward and
//      loop edges between the same pair occupy different corridors.
function pickHandles(positions, edge) {
  const s = positions.get(edge.source)
  const t = positions.get(edge.target)
  if (!s || !t) return { sourceHandle: 'b', targetHandle: 't' }
  const dx = t.x - s.x
  const dy = t.y - s.y
  const SAME_COL = 80

  if (Math.abs(dx) < SAME_COL) {
    return { sourceHandle: 'b', targetHandle: 't' }
  }
  // Canonical downward row transitions: parse → hub, hub/column → response.
  const srcInTopBand = s.y <= Y_TOP + 60
  const srcInHubBand = s.y >= Y_HUB - 50 && s.y <= Y_HUB + 50
  const tgtInHubBand = t.y >= Y_HUB - 50 && t.y <= Y_HUB + 50
  const tgtInRespBand = t.y >= Y_RESP - 60
  const rowTransition =
    (srcInTopBand && tgtInHubBand) ||
    (srcInHubBand && tgtInRespBand)
  if (rowTransition && dy > 0) {
    return { sourceHandle: 'b', targetHandle: 't' }
  }
  // Horizontal hub ↔ column: use side handles so forward and loop edges
  // between the same pair don't share a corridor.
  if (dx > 0) return { sourceHandle: 'r-out', targetHandle: 'l-in' }
  return { sourceHandle: 'l-out', targetHandle: 'r-in' }
}

// Synthetic runtime-injected edges — the template compiler adds priority-0
// edges from every condition_node to any response_node marked
// is_escape_target or (by convention) with retry-exhausted wording. Those
// edges don't exist in the authored template JSON, so the canvas would show
// those response nodes as disconnected. Render them as dashed "system"
// edges to make routing visible.
function synthesizeRuntimeEdges(nodes, authoredEdges) {
  const synthetic = []
  const conditionIds = nodes.filter((n) => n.type === 'condition_node').map((n) => n.id)
  const authoredKey = new Set(authoredEdges.map((e) => `${e.source}→${e.target}`))

  for (const n of nodes) {
    if (n.type !== 'response_node') continue
    const data = n.data || {}
    const isEscape = !!data.is_escape_target
    const isRetry = /retry/i.test(n.id) || /retry/i.test(data.label || '')
    if (!isEscape && !isRetry) continue
    for (const src of conditionIds) {
      const key = `${src}→${n.id}`
      if (authoredKey.has(key)) continue
      synthetic.push({
        id: `runtime_${src}_${n.id}`,
        source: src,
        target: n.id,
        label: isEscape ? 'escape (runtime)' : 'retry exhausted (runtime)',
        data: { runtime: true },
      })
    }
  }
  return synthetic
}

export default function AgentCanvas({ graphDef, onChange, onNodeSelect, onEdgeSelect }) {
  const rawNodes = graphDef?.nodes || []
  const rawEdges = graphDef?.edges || []
  const positions = ensurePositions(rawNodes, rawEdges)
  const positionById = new Map(rawNodes.map((n, i) => [n.id, positions[i]]))
  const typeById = new Map(rawNodes.map((n) => [n.id, n.type]))
  // Per-source authored-edge counts. Used to drop the priority badge on
  // single-out edges where #N adds noise and to keep it on condition
  // fan-outs where order is load-bearing.
  const outDegreeBySource = new Map()
  for (const e of rawEdges) {
    outDegreeBySource.set(e.source, (outDegreeBySource.get(e.source) || 0) + 1)
  }

  const [nodes, setNodes, onNodesChange] = useNodesState(
    rawNodes.map((n, i) => ({
      id: n.id,
      type: n.type,
      position: positions[i],
      data: n.data || {},
    }))
  )

  const authoredRaw = rawEdges.map((e, i) => ({
    id: e.id || `e_${i}`,
    source: e.source,
    target: e.target,
    predicate: e.predicate,
    _i: i,
  }))
  const runtimeRaw = synthesizeRuntimeEdges(rawNodes, rawEdges).map((e) => ({ ...e, _runtime: true }))

  const toReactFlowEdge = (e) => {
    // Prefer persisted handles when the author has reconnected the edge to
    // a specific port. Fall back to the layout-driven pickHandles when no
    // explicit handle is set (i.e. the edge was authored before any drag).
    const computed = pickHandles(positionById, e)
    const sourceHandle = e.sourceHandle ?? computed.sourceHandle
    const targetHandle = e.targetHandle ?? computed.targetHandle
    const isLoop = sourceHandle === 'l-out' || sourceHandle === 'r-out'
      ? (sourceHandle === 'l-out' && targetHandle === 'r-in') ||
        (sourceHandle === 'r-out' && targetHandle === 'l-in' &&
         (positionById.get(e.target)?.y ?? 0) < (positionById.get(e.source)?.y ?? 0))
      : false
    const stroke = e._runtime ? '#d97706' : (isLoop ? '#60a5fa' : '#94a3b8')
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle,
      targetHandle,
      // userLabel preserved in data so syncToParent can persist it without
      // round-tripping the auto-computed display string.
      data: { predicate: e.predicate, priority: e._i ?? 0, runtime: !!e._runtime, userLabel: e.label || null },
      type: 'smoothstep',
      animated: !e._runtime,
      labelStyle: { fontSize: 10, fill: e._runtime ? '#d97706' : '#6b7280', fontWeight: 500 },
      labelBgStyle: {
        fill: e._runtime ? '#fffbeb' : '#f9fafb',
        stroke: e._runtime ? '#fde68a' : '#e5e7eb',
        strokeWidth: 1,
      },
      labelBgPadding: [6, 3],
      // Label rule:
      //   1. User-set label always wins.
      //   2. Otherwise, show a small "#N" priority badge ONLY when the
      //      source is a condition_node with more than one outgoing edge
      //      (the case where execution order is load-bearing).
      //   3. Otherwise no label — the predicate lives in the EdgeEditor,
      //      not on the canvas, so we don't clutter the graph.
      label: (() => {
        if (e.label) return e.label
        const sourceType = typeById.get(e.source)
        const outCount = outDegreeBySource.get(e.source) || 0
        if (sourceType === 'condition_node' && outCount > 1) {
          return `#${e._i ?? 0}`
        }
        return undefined
      })(),
      style: {
        stroke,
        strokeWidth: isLoop ? 1.25 : 1.5,
        strokeDasharray: e._runtime ? '4 3' : (isLoop ? '2 4' : undefined),
      },
      // Arrowhead so direction is obvious — colour-matches the edge stroke.
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 16, height: 16 },
    }
  }

  const [edges, setEdges, onEdgesChange] = useEdgesState([
    ...authoredRaw.map(toReactFlowEdge),
    ...runtimeRaw.map(toReactFlowEdge),
  ])


  // Re-sync canvas internal state when graphDef changes externally (e.g. the
  // user edited a predicate / label / node data via the right panel). Without
  // this, useEdgesState/useNodesState only read graphDef once at mount and
  // panel edits never reach the canvas — predicates appear unchanged on the
  // edge labels even though they ARE saved to form state.
  //
  // We compare a "authored fingerprint" of the incoming graphDef against the
  // current internal state; if they match (i.e. the change was OUR change
  // echoing back via syncToParent), do nothing — that avoids a render loop.
  const authoredFingerprint = (graphDef?.edges || [])
    .map((e, i) => `${e.id || `e_${i}`}|${e.source}|${e.target}|${e.sourceHandle || ''}|${e.targetHandle || ''}|${e.predicate || ''}|${e.label || ''}`)
    .join('\n')
  const nodeFingerprint = (graphDef?.nodes || [])
    .map((n) => `${n.id}|${n.type}|${JSON.stringify(n.data || {})}`)
    .join('\n')
  const lastSyncedFingerprint = useRef({ edges: '', nodes: '' })

  useEffect(() => {
    if (lastSyncedFingerprint.current.edges === authoredFingerprint) return
    lastSyncedFingerprint.current.edges = authoredFingerprint
    // Re-derive edges (preserving handle picking + runtime overlays).
    const nextAuthored = (graphDef?.edges || []).map((e, i) => ({
      id: e.id || `e_${i}`, source: e.source, target: e.target,
      sourceHandle: e.sourceHandle, targetHandle: e.targetHandle,
      predicate: e.predicate, label: e.label, _i: i,
    }))
    const nextRuntime = synthesizeRuntimeEdges(graphDef?.nodes || [], graphDef?.edges || [])
      .map((e) => ({ ...e, _runtime: true }))
    setEdges([...nextAuthored.map(toReactFlowEdge), ...nextRuntime.map(toReactFlowEdge)])
  }, [authoredFingerprint])

  useEffect(() => {
    if (lastSyncedFingerprint.current.nodes === nodeFingerprint) return
    lastSyncedFingerprint.current.nodes = nodeFingerprint
    setNodes((current) => {
      // Preserve current positions (drag-set in the canvas) when applying
      // upstream data changes; the panel never changes positions.
      const posById = new Map(current.map((n) => [n.id, n.position]))
      return (graphDef?.nodes || []).map((n, i) => ({
        id: n.id,
        type: n.type,
        position: posById.get(n.id) || positions[i] || { x: 250, y: 40 },
        data: n.data || {},
      }))
    })
  }, [nodeFingerprint])

  const syncToParent = useCallback(
    (n, e) => {
      onChange({
        nodes: n.map((node) => ({
          id: node.id,
          type: node.type,
          position: node.position,
          data: node.data,
        })),
        // Runtime-synthesised edges (escape / retry) live in the UI only;
        // don't echo them back into the template.
        //
        // Edge.label here is the COMPUTED display label (`[1] foo…`) when
        // the user hasn't set their own. We persist only user-authored
        // labels — stored on edge.data.userLabel — so that subsequent
        // re-derives recompute the display from the latest predicate.
        // Without this round-trip stripping, the stale computed label
        // would shadow the new predicate-derived label.
        edges: e
          .filter((edge) => !edge.data?.runtime)
          .map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            // Persist the handle endpoints so handle-only reconnections
            // (drag arrowhead from one port to another on the SAME node)
            // survive save/reload. Backend treats these as opaque UI data.
            sourceHandle: edge.sourceHandle || null,
            targetHandle: edge.targetHandle || null,
            predicate: edge.data?.predicate || null,
            label: edge.data?.userLabel || null,
          })),
      })
    },
    [onChange]
  )

  const onConnect = useCallback(
    (params) => {
      const newEdge = {
        ...params,
        id: `e_${Date.now()}`,
        data: { predicate: null, priority: edges.length },
        type: 'smoothstep',
        animated: true,
        style: { stroke: '#94a3b8', strokeWidth: 1.5 },
        labelStyle: { fontSize: 10, fill: '#6b7280', fontWeight: 500 },
        labelBgStyle: { fill: '#f9fafb', stroke: '#e5e7eb', strokeWidth: 1 },
        labelBgPadding: [6, 3],
        markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8', width: 16, height: 16 },
      }
      const updated = addEdge(newEdge, edges)
      setEdges(updated)
      syncToParent(nodes, updated)
    },
    [edges, nodes, setEdges, syncToParent]
  )

  const handleNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes)
      setTimeout(() => {
        setNodes((currentNodes) => {
          syncToParent(currentNodes, edges)
          return currentNodes
        })
      }, 0)
    },
    [onNodesChange, edges, setNodes, syncToParent]
  )

  // ReactFlow fires onEdgesChange for selection, position, AND deletion.
  // We propagate everything to local edge state, then sync deletions back
  // to the parent form so saves include the removal.
  const handleEdgesChange = useCallback(
    (changes) => {
      onEdgesChange(changes)
      const hasDeletion = changes.some((c) => c.type === 'remove')
      if (hasDeletion) {
        setTimeout(() => {
          setEdges((current) => {
            syncToParent(nodes, current)
            return current
          })
        }, 0)
      }
    },
    [onEdgesChange, nodes, setEdges, syncToParent]
  )

  const handleEdgeClick = useCallback(
    (_evt, edge) => {
      if (edge?.data?.runtime) return  // runtime-synthesized edges aren't authorable
      onEdgeSelect?.(edge.id)
      onNodeSelect?.(null)
    },
    [onEdgeSelect, onNodeSelect]
  )

  // Drag an edge's endpoint to a different node to re-route.
  const handleReconnect = useCallback(
    (oldEdge, newConnection) => {
      if (oldEdge?.data?.runtime) return  // can't re-route synthetic edges
      const updated = reconnectEdge(oldEdge, newConnection, edges)
      setEdges(updated)
      syncToParent(nodes, updated)
    },
    [edges, nodes, setEdges, syncToParent]
  )

  const handleAddNode = (type) => {
    const id = `${type}_${Date.now()}`
    const newNode = {
      id,
      type,
      position: { x: 300, y: 300 + nodes.length * 50 },
      data: { ...DEFAULT_NODE_DATA[type] },
    }
    const updated = [...nodes, newNode]
    setNodes(updated)
    syncToParent(updated, edges)
  }

  const handleNodeClick = (_, node) => {
    onNodeSelect(node)
    onEdgeSelect?.(null)
  }

  // `fitView` only fires on the very first mount, so when graphDef arrives
  // async (initial render has zero nodes, then setNodes populates), the
  // viewport is left over from the empty state. Re-fit once the first
  // non-empty node set lands. After that, leave the user's pan/zoom alone.
  const [rfInstance, setRfInstance] = useState(null)
  const hasFittedOnce = useRef(false)
  useEffect(() => {
    if (!rfInstance) return
    if (hasFittedOnce.current) return
    if (nodes.length === 0) return
    // requestAnimationFrame so layout has settled (node measurements done).
    const frame = requestAnimationFrame(() => {
      rfInstance.fitView({ padding: 0.18, duration: 0 })
      hasFittedOnce.current = true
    })
    return () => cancelAnimationFrame(frame)
  }, [rfInstance, nodes.length])

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        onReconnect={handleReconnect}
        onPaneClick={() => { onNodeSelect(null); onEdgeSelect?.(null) }}
        onInit={setRfInstance}
        nodeTypes={nodeTypes}
        edgesUpdatable
        deleteKeyCode={['Backspace', 'Delete']}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.2}
        className="bg-gray-50"
      >
        <Background color="#e2e8f0" gap={20} />
        <Controls className="!bg-white !border-gray-200 !shadow-sm" />
      </ReactFlow>
      <div className="absolute bottom-4 left-4 z-10">
        <AddNodeMenu onAdd={handleAddNode} />
      </div>
      <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 pointer-events-none max-w-[680px]">
        <div className="px-3 py-1.5 rounded-full bg-white/90 border border-gray-200 text-[11px] text-gray-600 shadow-sm leading-relaxed">
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block"></span> source</span>
          {' · '}
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-blue-500 inline-block"></span> target</span>
          {' — drag green→blue to add an edge. Click an edge (or its label) to edit its predicate / label / order. Dashed orange = runtime-injected.'}
        </div>
      </div>
    </div>
  )
}

function truncate(s, n) {
  if (!s) return ''
  return s.length <= n ? s : s.slice(0, n - 1) + '…'
}
