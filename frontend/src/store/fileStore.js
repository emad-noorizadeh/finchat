import { create } from 'zustand'
import client from '../api/client'

const useFileStore = create((set) => ({
  files: [],
  uploading: false,
  loading: false,

  fetchFiles: async (userId) => {
    set({ loading: true })
    try {
      const res = await client.get('/files', { params: { user_id: userId } })
      set({ files: res.data, loading: false })
    } catch (err) {
      console.error('Failed to fetch files:', err)
      set({ loading: false })
    }
  },

  uploadFile: async (userId, file, splittingMethod = 'recursive') => {
    set({ uploading: true })
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('user_id', userId)
      formData.append('splitting_method', splittingMethod)

      const res = await client.post('/files/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      // Refresh file list after upload
      const listRes = await client.get('/files', { params: { user_id: userId } })
      set({ files: listRes.data, uploading: false })
      return res.data
    } catch (err) {
      console.error('Upload failed:', err)
      set({ uploading: false })
      throw err
    }
  },

  deleteFile: async (fileId, userId) => {
    try {
      await client.delete(`/files/${fileId}`)
      const res = await client.get('/files', { params: { user_id: userId } })
      set({ files: res.data })
    } catch (err) {
      console.error('Delete failed:', err)
      throw err
    }
  },
}))

export default useFileStore
