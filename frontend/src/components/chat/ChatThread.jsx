import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import WidgetRenderer from '../widgets/WidgetRenderer'
import useChatStore from '../../store/chatStore'

// Shared grid used both in the welcome state and the mid-chat popover.
// Each suggestion is either a string or {label, action_id?, message?}.
function SuggestionsGrid({ suggestions, onQuickAction, onSend, onPick, compact }) {
  if (!suggestions?.length) return null
  const wrapCls = compact
    ? 'grid grid-cols-2 gap-2 w-full'
    : 'grid grid-cols-2 gap-3 w-full max-w-lg'
  const btnCls = compact
    ? 'text-left px-3 py-2 rounded-lg border border-gray-200 text-xs text-gray-700 hover:bg-gray-50 hover:border-gray-300 transition-colors cursor-pointer'
    : 'text-left px-4 py-3 rounded-xl border border-gray-200 text-sm text-gray-600 hover:bg-gray-50 hover:border-gray-300 transition-colors cursor-pointer'
  return (
    <div className={wrapCls}>
      {suggestions.map((s, i) => {
        const label = typeof s === 'string' ? s : s.label
        const key = typeof s === 'string' ? s : (s.action_id || s.label || i)
        const click = () => {
          onPick?.()  // optional hook, e.g. close the popover
          if (typeof s === 'object' && s.action_id && onQuickAction) {
            onQuickAction(s)
          } else {
            onSend(typeof s === 'string' ? s : (s.message || s.label))
          }
        }
        return (
          <button key={key} onClick={click} className={btnCls}>
            {label}
          </button>
        )
      })}
    </div>
  )
}

// Popover version — renders as a floating panel anchored to its parent.
// Parent must be `position: relative`. Backdrop click closes.
function QuickActionsPopover({ suggestions, onQuickAction, onSend, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <>
      {/* Click-outside backdrop */}
      <div
        className="fixed inset-0 z-30 bg-transparent"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="menu"
        className="absolute bottom-full left-0 z-40 mb-2 w-80 rounded-xl border border-gray-200 bg-white p-3 shadow-lg"
      >
        <div className="flex items-baseline justify-between mb-2 px-1">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Quick actions
          </span>
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-gray-400 hover:text-gray-600 cursor-pointer"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <SuggestionsGrid
          suggestions={suggestions}
          onQuickAction={onQuickAction}
          onSend={onSend}
          onPick={onClose}
          compact
        />
      </div>
    </>
  )
}

function ChannelToggle({ channel, setChannel, locked }) {
  const base = 'px-2.5 py-1 rounded-full transition-colors'
  const active = locked ? 'bg-blue-50 text-blue-700' : 'bg-blue-100 text-blue-800'
  const idle = locked ? 'text-gray-300' : 'text-gray-500 hover:text-gray-700 cursor-pointer'
  const activeCursor = locked ? 'cursor-not-allowed' : 'cursor-pointer'
  const title = locked ? 'Channel is fixed for this chat. Start a new chat to switch.' : undefined
  return (
    <div
      className="flex items-center gap-0.5 bg-white border border-gray-200 rounded-full p-0.5 flex-shrink-0 text-xs font-medium self-center"
      title={title}
    >
      <button
        type="button"
        onClick={locked ? undefined : () => setChannel('chat')}
        disabled={locked}
        className={`${base} ${channel === 'chat' ? `${active} ${activeCursor}` : idle}`}
      >
        Chat
      </button>
      <button
        type="button"
        onClick={locked ? undefined : () => setChannel('voice')}
        disabled={locked}
        className={`${base} ${channel === 'voice' ? `${active} ${activeCursor}` : idle}`}
      >
        Voice
      </button>
    </div>
  )
}

export default function ChatThread({
  messages,
  isLoading,
  thinkingMessage,
  toolExecutions,
  onSend,
  onQuickAction,
  onWidgetAction,
  userName,
  suggestions,
  streamingMessageId,
}) {
  const [input, setInput] = useState('')
  const [quickActionsOpen, setQuickActionsOpen] = useState(false)
  const messagesEndRef = useRef(null)
  const textareaRef = useRef(null)
  const channel = useChatStore((s) => s.channel)
  const setChannel = useChatStore((s) => s.setChannel)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinkingMessage])

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px'
    }
  }, [input])

  const handleSend = (text) => {
    const msg = text || input.trim()
    if (!msg || isLoading) return
    onSend(msg)
    setInput('')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const hasMessages = messages.length > 0

  return (
    <div className="flex flex-col h-full bg-white">
      {hasMessages ? (
        <>
          <div className="flex-1 overflow-y-auto">
            <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
              {messages.map((msg) => {
                // Widget messages render as components, not chat bubbles
                if (msg.widget || msg.message_type === 'widget') {
                  const widgetData = msg.widget || (() => {
                    try { return JSON.parse(msg.content) } catch { return null }
                  })()
                  if (widgetData) {
                    return (
                      <div
                        key={msg.id}
                        className="w-full"
                        data-role={msg.role || 'assistant'}
                        data-message-type="widget"
                      >
                        <WidgetRenderer widget={widgetData} onAction={onWidgetAction} />
                      </div>
                    )
                  }
                }

                return (
                  <div
                    key={msg.id}
                    className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    data-role={msg.role}
                    data-message-type={msg.message_type || 'text'}
                  >
                    <div className={`max-w-[80%] px-4 py-3 rounded-2xl text-[15px] leading-relaxed ${
                      msg.role === 'user'
                        ? 'bg-blue-600 text-white rounded-br-md'
                        : 'bg-gray-100 text-gray-800 rounded-bl-md'
                    }`}>
                      {msg.role === 'user' ? (
                        <p className="whitespace-pre-wrap">{msg.content}</p>
                      ) : streamingMessageId === msg.id || msg.channel === 'voice' ? (
                        <p className="whitespace-pre-wrap">{msg.content}</p>
                      ) : (
                        <div className="prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-headings:my-2 prose-pre:my-2 prose-code:text-sm">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              a: ({ node, ...props }) => (
                                <a {...props} target="_blank" rel="noopener noreferrer" />
                              ),
                            }}
                          >
                            {msg.content}
                          </ReactMarkdown>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}

              {/* Tool execution indicators — label comes from the backend
                  (BaseTool.activity_description). Fall back to the raw tool
                  name if the backend didn't ship a label (e.g., legacy events). */}
              {toolExecutions && toolExecutions.size > 0 && (
                <div className="flex justify-start">
                  <div className="bg-gray-50 px-4 py-2 rounded-2xl rounded-bl-md space-y-1">
                    {Array.from(toolExecutions.entries()).map(([name, info]) => (
                      <div key={name} className="flex items-center gap-2 text-xs text-gray-500">
                        {info.status === 'running' ? (
                          <span className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
                        ) : (
                          <span className="text-green-500">✓</span>
                        )}
                        <span>{info.label || `Running ${name}...`}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Thinking indicator */}
              {isLoading && thinkingMessage && !messages[messages.length - 1]?.content?.includes && (
                <div className="flex justify-start">
                  <div className="bg-gray-50 px-4 py-2 rounded-2xl rounded-bl-md">
                    <div className="flex items-center gap-2 text-sm text-gray-500">
                      <span className="w-3 h-3 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                      <span>{thinkingMessage}</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Loading dots when no thinking message */}
              {isLoading && !thinkingMessage && !(messages[messages.length - 1]?.role === 'assistant' && messages[messages.length - 1]?.content) && (
                <div className="flex justify-start">
                  <div className="bg-gray-100 px-4 py-3 rounded-2xl rounded-bl-md">
                    <div className="flex items-center gap-1.5">
                      <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
                      <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
                      <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                    </div>
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Pinned input */}
          <div className="border-t border-gray-200 bg-white">
            <div className="max-w-3xl mx-auto px-4 py-3 relative">
              {quickActionsOpen && suggestions && (
                <QuickActionsPopover
                  suggestions={suggestions}
                  onQuickAction={onQuickAction}
                  onSend={handleSend}
                  onClose={() => setQuickActionsOpen(false)}
                />
              )}
              <div className="flex items-end gap-2 bg-gray-50 border border-gray-300 rounded-2xl px-4 py-2 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500 transition-all">
                <ChannelToggle channel={channel} setChannel={setChannel} locked={hasMessages} />
                {/* Quick-actions trigger — lightning-bolt icon. Opens a popover
                    above the composer so users can invoke canned data fetches
                    mid-chat without typing. */}
                {suggestions?.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setQuickActionsOpen(v => !v)}
                    title="Quick actions"
                    aria-label="Quick actions"
                    aria-expanded={quickActionsOpen}
                    className={`flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-200 transition-colors cursor-pointer self-center ${quickActionsOpen ? 'bg-gray-200' : ''}`}
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                      <path fillRule="evenodd" d="M14.615 1.595a.75.75 0 01.359.852L12.982 9.75h7.268a.75.75 0 01.548 1.262l-10.5 11.25a.75.75 0 01-1.272-.71l1.992-7.302H3.75a.75.75 0 01-.548-1.262l10.5-11.25a.75.75 0 01.913-.143z" clipRule="evenodd" />
                    </svg>
                  </button>
                )}
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={channel === 'voice' ? 'Speak or type...' : 'Type a message...'}
                  rows={1}
                  className="flex-1 bg-transparent resize-none outline-none text-[15px] text-gray-800 placeholder-gray-400 py-1.5 max-h-[200px]"
                />
                <button
                  onClick={() => handleSend()}
                  disabled={!input.trim() || isLoading}
                  className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors cursor-pointer"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                    <path d="M3.478 2.404a.75.75 0 0 0-.926.941l2.432 7.905H13.5a.75.75 0 0 1 0 1.5H4.984l-2.432 7.905a.75.75 0 0 0 .926.94 60.519 60.519 0 0 0 18.445-8.986.75.75 0 0 0 0-1.218A60.517 60.517 0 0 0 3.478 2.404Z" />
                  </svg>
                </button>
              </div>
              <p className="text-xs text-gray-400 text-center mt-2">AI can make mistakes. Verify important information.</p>
            </div>
          </div>
        </>
      ) : (
        /* Welcome state */
        <div className="flex-1 flex flex-col items-center justify-center px-4">
          <h1 className="text-3xl font-semibold text-gray-800 mb-2">
            {userName ? `Hi, ${userName}` : 'Welcome'}
          </h1>
          <p className="text-gray-400 text-lg mb-8">How can I help you today?</p>

          <div className="w-full max-w-3xl mb-5">
            <div className="flex items-end gap-2 bg-gray-50 border border-gray-300 rounded-2xl px-4 py-2 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500 transition-all">
              <ChannelToggle channel={channel} setChannel={setChannel} />
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={channel === 'voice' ? 'Speak or type...' : 'Type a message...'}
                rows={1}
                className="flex-1 bg-transparent resize-none outline-none text-[15px] text-gray-800 placeholder-gray-400 py-1.5 max-h-[200px]"
              />
              <button
                onClick={() => handleSend()}
                disabled={!input.trim() || isLoading}
                className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors cursor-pointer"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                  <path d="M3.478 2.404a.75.75 0 0 0-.926.941l2.432 7.905H13.5a.75.75 0 0 1 0 1.5H4.984l-2.432 7.905a.75.75 0 0 0 .926.94 60.519 60.519 0 0 0 18.445-8.986.75.75 0 0 0 0-1.218A60.517 60.517 0 0 0 3.478 2.404Z" />
                </svg>
              </button>
            </div>
          </div>

          <SuggestionsGrid
            suggestions={suggestions}
            onQuickAction={onQuickAction}
            onSend={handleSend}
          />

          <p className="text-xs text-gray-400 text-center mt-6">AI can make mistakes. Verify important information.</p>
        </div>
      )}
    </div>
  )
}

