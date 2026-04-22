import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import client from '../api/client'

// Auth state persisted to localStorage so a full reload (or the "hard
// reload" we often ask users to do during debugging) doesn't drop the
// selected profile. Zustand's persist middleware rehydrates synchronously
// on module import, so the first render already has the profile.
const useAuthStore = create(
  persist(
    (set) => ({
      profile: null,
      token: null,
      profiles: [],
      loading: false,

      fetchProfiles: async () => {
        set({ loading: true })
        try {
          const res = await client.get('/profiles')
          set({ profiles: res.data, loading: false })
        } catch (err) {
          console.error('Failed to fetch profiles:', err)
          set({ loading: false })
        }
      },

      login: async (profileId) => {
        try {
          const res = await client.post(`/login/${profileId}`)
          set({ profile: res.data.profile, token: res.data.token })
          return true
        } catch (err) {
          console.error('Login failed:', err)
          return false
        }
      },

      logout: () => set({ profile: null, token: null }),
    }),
    {
      name: 'finchat-auth',
      storage: createJSONStorage(() => localStorage),
      // Only persist the selected profile + token; the available-profiles
      // list is cheap to re-fetch and loading is per-render state.
      partialize: (state) => ({ profile: state.profile, token: state.token }),
    }
  )
)

export default useAuthStore
