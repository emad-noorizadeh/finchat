import { useEffect, useState, useCallback } from 'react'
import client from '../api/client'
import useAuthStore from '../store/authStore'
import useChatStore from '../store/chatStore'
import ConversationList from '../components/chat/ConversationList'
import ChatThread from '../components/chat/ChatThread'

// New-chat quick actions.
//
// Each entry is either:
//   - {label, action_id}  → POST /quick_action — runs the data tool server-side
//                           and renders the widget WITHOUT any LLM call.
//   - {label, message}    → POST /messages — sends the text through the normal
//                           Planner path (for interactive flows like transfer
//                           where the LLM is still needed to route).
const SUGGESTIONS = [
  { label: 'What are my recent transactions?', action_id: 'recent_transactions' },
  { label: 'Show me my account balances',      action_id: 'account_balances' },
  { label: 'See credit score',                 action_id: 'credit_score' },
  { label: 'Transfer $200 to savings',         message: 'Transfer $200 to savings' },
]

export default function ChatPage() {
  const profile = useAuthStore((s) => s.profile)
  const {
    sessions, activeSessionId, messages, channel,
    fetchSessions, createSession, selectSession, deleteSession,
    newChat, addMessage, removeMessage, updateLastAssistantMessage, updateWidgetInMessages, restoreSession,
  } = useChatStore()

  const [isLoading, setIsLoading] = useState(false)
  const [thinkingMessage, setThinkingMessage] = useState('')
  const [toolExecutions, setToolExecutions] = useState(new Map())
  const [streamingMessageId, setStreamingMessageId] = useState(null)

  const userId = profile?.login_id

  useEffect(() => {
    if (userId) {
      fetchSessions(userId)
      restoreSession(userId)
    }
  }, [userId, fetchSessions, restoreSession])

  const handleSelectSession = useCallback((sessionId) => {
    selectSession(sessionId, userId)
  }, [selectSession, userId])

  const handleDeleteSession = useCallback((sessionId) => {
    deleteSession(sessionId, userId)
  }, [deleteSession, userId])

  const handleNewChat = useCallback(() => {
    newChat(userId)
  }, [newChat, userId])

  const handleSend = useCallback(async (text) => {
    if (!text || isLoading || !userId) return

    let sessionId = activeSessionId

    // Create session on first message
    if (!sessionId) {
      sessionId = await createSession(userId)
    }

    // Add user message locally
    addMessage({ id: Date.now(), role: 'user', content: text, channel })

    // Add empty assistant message placeholder
    const assistantId = Date.now() + 1
    addMessage({ id: assistantId, role: 'assistant', content: '', channel })

    setIsLoading(true)
    setStreamingMessageId(assistantId)
    setThinkingMessage('')
    setToolExecutions(new Map())

    let accumulatedContent = ''

    try {
      const response = await fetch(`/api/chat/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text, user_id: userId, channel }),
      })

      // Handle stale session (DB was reset)
      if (response.status === 404) {
        // Clear stale session, create new one, retry
        newChat(userId)
        const newSid = await createSession(userId)
        updateLastAssistantMessage('Session expired. Please try again.')
        setIsLoading(false)
        setStreamingMessageId(null)
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.trim().startsWith('data: ')) continue
          const jsonStr = line.trim().slice(6)
          if (!jsonStr || jsonStr === '[DONE]') continue

          try {
            const event = JSON.parse(jsonStr)

            switch (event.type) {
              case 'thinking':
                setThinkingMessage(event.content || '')
                break

              case 'tool_start':
                setToolExecutions((prev) => {
                  const next = new Map(prev)
                  next.set(event.tool, {
                    status: 'running',
                    // Backend (BaseTool.activity_description) is the source
                    // of truth for the human-readable label. Fall back to
                    // the tool name if the backend didn't supply one.
                    label: event.content || `Running ${event.tool}...`,
                    args: event.tool_args || null,
                  })
                  return next
                })
                break

              case 'tool_complete':
                setToolExecutions((prev) => {
                  const next = new Map(prev)
                  const existing = next.get(event.tool) || {}
                  next.set(event.tool, {
                    ...existing,
                    status: 'complete',
                    preview: event.result_preview,
                  })
                  // Remove after 1.5s so users see the ✓ briefly.
                  setTimeout(() => {
                    setToolExecutions((p) => {
                      const n = new Map(p)
                      n.delete(event.tool)
                      return n
                    })
                  }, 1500)
                  return next
                })
                break

              case 'response_chunk':
                accumulatedContent += event.content
                updateLastAssistantMessage(accumulatedContent)
                setThinkingMessage('')
                break

              case 'response':
                if (event.content) {
                  updateLastAssistantMessage(event.content)
                }
                break

              case 'widget':
                if (!accumulatedContent) removeMessage(assistantId)
                addMessage({
                  id: Date.now() + Math.random(),
                  role: 'assistant',
                  message_type: 'widget',
                  content: event.data.instance_id || '',
                  widget: event.data,
                })
                break

              case 'interrupt':
                if (!accumulatedContent) removeMessage(assistantId)
                // Show as confirmation widget
                addMessage({
                  id: Date.now() + Math.random(),
                  role: 'assistant',
                  message_type: 'widget',
                  widget: {
                    widget: 'confirmation_request',
                    title: event.data?.title || 'Confirmation Required',
                    data: event.data,
                    actions: [
                      { id: 'confirm', label: 'Confirm', style: 'primary', type: 'resume' },
                      { id: 'cancel', label: 'Cancel', style: 'danger', type: 'resume' },
                    ],
                    metadata: {},
                  },
                  content: JSON.stringify(event.data),
                })
                break

              case 'error':
                updateLastAssistantMessage(`Error: ${event.error}`)
                break

              case 'done':
                break
            }
          } catch {
            // Skip malformed lines
          }
        }
      }
    } catch (err) {
      updateLastAssistantMessage(`Error: ${err.message}`)
    }

    // Clean up empty placeholder if no text was streamed
    if (!accumulatedContent) {
      removeMessage(assistantId)
    }

    setIsLoading(false)
    setStreamingMessageId(null)  // Finalize — triggers markdown rendering
    setThinkingMessage('')
    setToolExecutions(new Map())
    fetchSessions(userId) // Refresh session list (title may have updated)
  }, [isLoading, userId, activeSessionId, channel, createSession, addMessage, removeMessage, updateLastAssistantMessage, fetchSessions, newChat])

  // Quick-action handler — POST /quick_action, stream back the single widget
  // event. NO LLM calls. The server pre-persists the user message so history
  // renders the clicked action text natively.
  const handleQuickAction = useCallback(async (suggestion) => {
    if (isLoading || !userId) return

    // Legacy string form or {message, ...} without action_id → route through
    // the normal Planner path.
    const actionId = suggestion && typeof suggestion === 'object' ? suggestion.action_id : null
    const labelText = typeof suggestion === 'string' ? suggestion : (suggestion?.label || suggestion?.message || '')
    if (!actionId) {
      return handleSend(typeof suggestion === 'string' ? suggestion : (suggestion.message || suggestion.label))
    }

    let sessionId = activeSessionId
    if (!sessionId) sessionId = await createSession(userId)

    // Add the user message locally (server persists too — this is purely for
    // immediate UI feedback while the widget is being built).
    addMessage({ id: Date.now(), role: 'user', content: labelText, channel })
    setIsLoading(true)

    try {
      const response = await fetch(`/api/chat/sessions/${sessionId}/quick_action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, action_id: actionId, channel }),
      })

      if (response.status === 404) {
        newChat(userId)
        await createSession(userId)
        setIsLoading(false)
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.trim().startsWith('data: ')) continue
          const jsonStr = line.trim().slice(6)
          if (!jsonStr || jsonStr === '[DONE]') continue
          try {
            const event = JSON.parse(jsonStr)
            if (event.type === 'widget' && event.data) {
              addMessage({
                id: Date.now() + Math.random(),
                role: 'assistant',
                message_type: 'widget',
                content: event.data.instance_id || '',
                widget: event.data,
              })
            } else if (event.type === 'error') {
              addMessage({ id: Date.now() + 2, role: 'assistant', content: `Error: ${event.error}`, channel })
            }
          } catch { /* skip malformed */ }
        }
      }
    } catch (err) {
      addMessage({ id: Date.now() + 3, role: 'assistant', content: `Error: ${err.message}`, channel })
    }

    setIsLoading(false)
    fetchSessions(userId)  // Pick up the auto-titled session
  }, [isLoading, userId, activeSessionId, channel, createSession, addMessage, handleSend, newChat, fetchSessions])

  const handleWidgetAction = useCallback(async (action, widget, payload = {}) => {
    const actionType = action.type || action.id
    const instanceId = widget?.instance_id

    if (actionType === 'dismiss' && instanceId) {
      try {
        const res = await client.post(`/widgets/${instanceId}/action`, { action_id: 'dismiss', payload })
        updateWidgetInMessages(instanceId, res.data)
      } catch { /* ignore */ }
      return
    }

    if (actionType === 'resume') {
      const confirmed = action.id === 'confirm'
      const sid = activeSessionId
      if (!sid) return
      await fetch(`/api/chat/sessions/${sid}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'resume',
          user_id: userId,
          data: { confirmed, widget_instance_id: instanceId },
          channel,
        }),
      })
      if (instanceId) {
        updateWidgetInMessages(instanceId, {
          ...widget,
          status: confirmed ? 'completed' : 'dismissed',
        })
      }
      return
    }

    if ((actionType === 'paginate' || actionType === 'load_more') && instanceId) {
      try {
        const res = await client.post(`/widgets/${instanceId}/action`, { action_id: 'load_more', payload })
        updateWidgetInMessages(instanceId, res.data)
      } catch { /* ignore */ }
      return
    }

    // Generic simple action — forwards the widget's payload so action
    // handlers (e.g. transfer_form validate/submit) can read user-edited
    // form state.
    if (!instanceId) {
      console.error('[widget-action] no instance_id on widget', { actionType, widget })
      return
    }
    try {
      const res = await client.post(`/widgets/${instanceId}/action`, { action_id: actionType, payload })
      updateWidgetInMessages(instanceId, res.data)
    } catch (err) {
      const detail = err?.response?.data?.detail
      const status = err?.response?.status
      const msg = detail
        ? `${detail}${status ? ` (HTTP ${status})` : ''}`
        : `${err?.message || 'Action failed'}${status ? ` (HTTP ${status})` : ''}`
      console.error('[widget-action] post failed', {
        instanceId, actionType, status, detail, err,
      })
      updateWidgetInMessages(instanceId, {
        ...widget,
        data: { ...(widget?.data || {}), submit_error: msg },
      })
    }
  }, [activeSessionId, userId, channel, updateWidgetInMessages])

  return (
    <div className="flex h-screen">
      <ConversationList
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={handleSelectSession}
        onNew={handleNewChat}
        onDelete={handleDeleteSession}
      />
      <div className="flex-1">
        <ChatThread
          messages={messages}
          isLoading={isLoading}
          thinkingMessage={thinkingMessage}
          toolExecutions={toolExecutions}
          streamingMessageId={streamingMessageId}
          onSend={handleSend}
          onQuickAction={handleQuickAction}
          onWidgetAction={handleWidgetAction}
          userName={profile?.name}
          suggestions={SUGGESTIONS}
        />
      </div>
    </div>
  )
}
