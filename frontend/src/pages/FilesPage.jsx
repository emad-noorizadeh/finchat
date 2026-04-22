import { useEffect, useRef, useState, Fragment } from 'react'
import useFileStore from '../store/fileStore'
import client from '../api/client'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const STATUS_STYLES = {
  processing: 'bg-yellow-100 text-yellow-800',
  ready: 'bg-green-100 text-green-800',
  error: 'bg-red-100 text-red-800',
}

const SYSTEM_KNOWLEDGE_ID = 'system'

export default function FilesPage() {
  const { files, loading, uploading, fetchFiles, uploadFile, deleteFile } = useFileStore()
  const fileInputRef = useRef(null)
  const [expandedId, setExpandedId] = useState(null)
  const [fileDetail, setFileDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [dialog, setDialog] = useState(null) // { type: 'content'|'chunks', data, title }
  const [dialogMaximized, setDialogMaximized] = useState(false)

  useEffect(() => {
    fetchFiles(SYSTEM_KNOWLEDGE_ID)
  }, [fetchFiles])

  const handleUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      await uploadFile(SYSTEM_KNOWLEDGE_ID, file)
    } catch { /* */ }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleDelete = async (fileId) => {
    if (!confirm('Delete this knowledge file? This cannot be undone.')) return
    try {
      await deleteFile(fileId, SYSTEM_KNOWLEDGE_ID)
      if (expandedId === fileId) { setExpandedId(null); setFileDetail(null) }
    } catch { /* */ }
  }

  const handleExpand = async (fileId) => {
    if (expandedId === fileId) { setExpandedId(null); setFileDetail(null); return }
    setExpandedId(fileId)
    setLoadingDetail(true)
    try {
      const res = await client.get(`/files/${fileId}`)
      setFileDetail(res.data)
    } catch { setFileDetail(null) }
    setLoadingDetail(false)
  }

  const viewContent = async (fileId, filename) => {
    try {
      const res = await client.get(`/files/${fileId}/content`)
      setDialog({ type: 'content', data: res.data.content, title: filename })
    } catch { setDialog({ type: 'content', data: 'Failed to load content', title: filename }) }
  }

  const viewChunks = async (fileId, filename) => {
    try {
      const res = await client.get(`/files/${fileId}/chunks`)
      setDialog({ type: 'chunks', data: res.data.chunks, title: `${filename} — ${res.data.chunk_count} chunks` })
    } catch { setDialog({ type: 'chunks', data: [], title: filename }) }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Knowledge</h1>
          <p className="text-gray-500 text-sm mt-1">Upload markdown (.md) files to build your knowledge base</p>
        </div>
        <div>
          <input ref={fileInputRef} type="file" onChange={handleUpload} accept=".md" className="hidden" />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
          >
            {uploading ? 'Uploading...' : 'Upload File'}
          </button>
        </div>
      </div>

      {loading ? (
        <p className="text-gray-400">Loading...</p>
      ) : files.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg mb-2">No knowledge files yet</p>
          <p className="text-sm">Upload markdown (.md) files to enable knowledge search in chat</p>
        </div>
      ) : (
        <div className="border border-gray-200 rounded-xl overflow-visible">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-left text-xs uppercase tracking-wider">
              <tr>
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-5 py-3 font-medium w-24">Chunks</th>
                <th className="px-5 py-3 font-medium w-24">Status</th>
                <th className="px-5 py-3 font-medium w-32">Uploaded</th>
                <th className="px-5 py-3 font-medium w-20"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {files.map((f) => (
                <Fragment key={f.id}>
                  <tr className="hover:bg-gray-50 cursor-pointer" onClick={() => handleExpand(f.id)}>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <svg
                          className={`w-4 h-4 text-gray-400 transition-transform ${expandedId === f.id ? 'rotate-90' : ''}`}
                          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                        </svg>
                        <span className="font-medium text-gray-800">{f.filename}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-gray-500">{f.chunk_count}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${STATUS_STYLES[f.status] || ''}`}>
                        {f.status}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-gray-500">{new Date(f.created_at).toLocaleDateString()}</td>
                    <td className="px-5 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                      <button onClick={() => handleDelete(f.id)} className="text-red-500 hover:text-red-700 text-xs cursor-pointer">
                        Delete
                      </button>
                    </td>
                  </tr>

                  {expandedId === f.id && (
                    <tr>
                      <td colSpan={5} className="bg-gray-50 px-5 py-4">
                        {loadingDetail ? (
                          <p className="text-gray-400 text-sm">Loading...</p>
                        ) : fileDetail ? (
                          <div className="space-y-4">
                            <div className="grid grid-cols-4 gap-4 text-sm">
                              <div>
                                <span className="text-gray-500 text-xs">File ID</span>
                                <p className="font-mono text-xs text-gray-600 mt-0.5">{fileDetail.id?.slice(0, 8)}...</p>
                              </div>
                              <div>
                                <span className="text-gray-500 text-xs">Collection</span>
                                <p className="font-mono text-xs text-gray-600 mt-0.5">{fileDetail.collection_name}</p>
                              </div>
                              <div>
                                <span className="text-gray-500 text-xs">Splitting</span>
                                <p className="text-gray-600 text-xs mt-0.5">{fileDetail.splitting_method}</p>
                              </div>
                              <div>
                                <span className="text-gray-500 text-xs">Indexed</span>
                                <p className="text-gray-600 text-xs mt-0.5">{fileDetail.chunk_count} chunks</p>
                              </div>
                            </div>
                            <div className="flex gap-2">
                              <button
                                onClick={() => viewContent(f.id, f.filename)}
                                className="px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-700 hover:bg-gray-50 cursor-pointer"
                              >
                                View Content
                              </button>
                              <button
                                onClick={() => viewChunks(f.id, f.filename)}
                                className="px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-700 hover:bg-gray-50 cursor-pointer"
                              >
                                View Chunks
                              </button>
                            </div>
                            {fileDetail.error_message && (
                              <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                                {fileDetail.error_message}
                              </div>
                            )}
                          </div>
                        ) : (
                          <p className="text-gray-400 text-sm">No details available</p>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Dialog for viewing content / chunks */}
      {dialog && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => { setDialog(null); setDialogMaximized(false) }}>
          <div
            className={`bg-white flex flex-col shadow-xl transition-all duration-200 ${
              dialogMaximized
                ? 'w-full h-full rounded-none'
                : 'w-full max-w-3xl max-h-[80vh] rounded-2xl'
            }`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between flex-shrink-0">
              <h2 className="text-lg font-semibold text-gray-900">{dialog.title}</h2>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setDialogMaximized(!dialogMaximized)}
                  className="text-gray-400 hover:text-gray-600 cursor-pointer p-1"
                  title={dialogMaximized ? 'Restore' : 'Maximize'}
                >
                  {dialogMaximized ? (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 9V4.5M9 9H4.5M9 9 3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5 5.25 5.25" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" />
                    </svg>
                  )}
                </button>
                <button onClick={() => { setDialog(null); setDialogMaximized(false) }} className="text-gray-400 hover:text-gray-600 cursor-pointer text-xl">×</button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              {dialog.type === 'content' ? (
                <div className="prose prose-sm max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{dialog.data}</ReactMarkdown>
                </div>
              ) : dialog.type === 'chunks' ? (
                <div className="space-y-4">
                  {dialog.data.length === 0 ? (
                    <p className="text-gray-400">No chunks found</p>
                  ) : (
                    dialog.data.map((chunk, i) => (
                      <div key={i} className="border border-gray-200 rounded-lg overflow-hidden">
                        <div className="px-4 py-2 bg-gray-50 flex items-center justify-between text-xs text-gray-500">
                          <div className="flex items-center gap-3">
                            <span className="font-semibold text-gray-700">Chunk {chunk.index}</span>
                            {chunk.section && <span>Section: {chunk.section}</span>}
                            {chunk.is_whole_doc && <span className="px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">whole doc</span>}
                          </div>
                          <span>{chunk.char_count} chars</span>
                        </div>
                        <div className="px-4 py-3 text-sm text-gray-700 whitespace-pre-wrap font-mono bg-white">
                          {chunk.content}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
