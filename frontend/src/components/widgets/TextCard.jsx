import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import WidgetActions from './WidgetActions'

export default function TextCard({ widget, onAction, mode = 'standalone' }) {
  const { data, title, actions } = widget

  if (mode === 'composite') {
    return (
      <div className="text-sm">
        {title && <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">{title}</h4>}
        <div className="prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-ol:my-1">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {data?.content || ''}
          </ReactMarkdown>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      {title && <h3 className="text-sm font-semibold text-gray-800 mb-2">{title}</h3>}
      <div className="prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-ol:my-1">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {data?.content || ''}
        </ReactMarkdown>
      </div>
      {actions?.length > 0 && (
        <WidgetActions actions={actions} widget={widget} onAction={onAction} />
      )}
    </div>
  )
}
