import { create } from 'zustand'
import client from '../api/client'

const useChatStore = create((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  loading: false,
  channel: localStorage.getItem('chat_channel') || 'chat',

  setChannel: (channel) => {
    localStorage.setItem('chat_channel', channel)
    set({ channel })
  },

  // Reflect a loaded session's channel without persisting as the user's preference
  adoptSessionChannel: (channel) => {
    set({ channel })
  },

  // Restore the user's preferred default (used when starting a new chat)
  restorePreferredChannel: () => {
    const pref = localStorage.getItem('chat_channel') || 'chat'
    set({ channel: pref })
  },

  fetchSessions: async (userId) => {
    if (!userId) return
    try {
      const res = await client.get('/chat/sessions', { params: { user_id: userId } })
      set({ sessions: res.data })
    } catch (err) {
      console.error('Failed to fetch sessions:', err)
    }
  },

  createSession: async (userId) => {
    const res = await client.post('/chat/sessions', { user_id: userId })
    const sessionId = res.data.session_id
    set({ activeSessionId: sessionId, messages: [] })
    localStorage.setItem(`activeSession_${userId}`, sessionId)
    await get().fetchSessions(userId)
    return sessionId
  },

  selectSession: async (sessionId, userId) => {
    set({ activeSessionId: sessionId, loading: true })
    localStorage.setItem(`activeSession_${userId}`, sessionId)
    try {
      const res = await client.get(`/chat/sessions/${sessionId}/messages`)
      const msgs = res.data.map((m, i) => ({ ...m, id: m.id || i }))
      set({ messages: msgs, loading: false })
      // Adopt the session's channel (first message wins) — pinned for this session
      const firstWithChannel = msgs.find((m) => m.channel)
      if (firstWithChannel) {
        set({ channel: firstWithChannel.channel })
      }
    } catch {
      // Session not found (DB reset) — clear stale reference
      set({ activeSessionId: null, messages: [], loading: false })
      localStorage.removeItem(`activeSession_${userId}`)
    }
  },

  deleteSession: async (sessionId, userId) => {
    await client.delete(`/chat/sessions/${sessionId}`)
    const { activeSessionId } = get()
    if (activeSessionId === sessionId) {
      set({ activeSessionId: null, messages: [] })
      localStorage.removeItem(`activeSession_${userId}`)
    }
    await get().fetchSessions(userId)
  },

  newChat: (userId) => {
    const pref = localStorage.getItem('chat_channel') || 'chat'
    set({ activeSessionId: null, messages: [], channel: pref })
    localStorage.removeItem(`activeSession_${userId}`)
  },

  addMessage: (msg) => {
    set((s) => ({ messages: [...s.messages, msg] }))
  },

  removeMessage: (msgId) => {
    set((s) => ({ messages: s.messages.filter((m) => m.id !== msgId) }))
  },

  updateWidgetInMessages: (instanceId, widgetData) => {
    set((s) => ({
      messages: s.messages.map((m) => {
        if (m.message_type === 'widget' && (m.content === instanceId || m.widget?.instance_id === instanceId)) {
          return { ...m, widget: widgetData }
        }
        return m
      }),
    }))
  },

  updateLastAssistantMessage: (content) => {
    set((s) => {
      const msgs = [...s.messages]
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role === 'assistant') {
          msgs[i] = { ...msgs[i], content }
          break
        }
      }
      return { messages: msgs }
    })
  },

  restoreSession: async (userId) => {
    const saved = localStorage.getItem(`activeSession_${userId}`)
    if (!saved) return

    // Verify session exists in the sessions list
    try {
      const res = await client.get('/chat/sessions', { params: { user_id: userId } })
      const exists = res.data.some((s) => s.id === saved)
      if (exists) {
        get().selectSession(saved, userId)
      } else {
        // Stale session — clear
        localStorage.removeItem(`activeSession_${userId}`)
        set({ activeSessionId: null, messages: [] })
      }
    } catch {
      localStorage.removeItem(`activeSession_${userId}`)
    }
  },
}))

export default useChatStore
