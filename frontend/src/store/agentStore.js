import { create } from 'zustand'
import client from '../api/client'

const useAgentStore = create((set, get) => ({
  agents: [],
  loading: false,

  fetchAgents: async (search = '') => {
    set({ loading: true })
    try {
      const params = search ? { search } : {}
      const res = await client.get('/agents', { params })
      set({ agents: res.data, loading: false })
    } catch (err) {
      console.error('Failed to fetch agents:', err)
      set({ loading: false })
    }
  },

  createAgent: async (data) => {
    const res = await client.post('/agents', data)
    await get().fetchAgents()
    return res.data
  },

  updateAgent: async (id, data) => {
    const res = await client.put(`/agents/${id}`, data)
    await get().fetchAgents()
    return res.data
  },

  deleteAgent: async (id) => {
    await client.delete(`/agents/${id}`)
    await get().fetchAgents()
  },

  deployAgent: async (id) => {
    const res = await client.post(`/agents/${id}/deploy`)
    await get().fetchAgents()
    return res.data
  },

  disableAgent: async (id) => {
    const res = await client.post(`/agents/${id}/disable`)
    await get().fetchAgents()
    return res.data
  },

  getAgentDetail: async (name) => {
    const res = await client.get(`/agents/${name}`)
    return res.data
  },
}))

export default useAgentStore
