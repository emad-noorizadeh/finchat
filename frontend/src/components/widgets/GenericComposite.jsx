import WidgetRenderer from './WidgetRenderer'

/**
 * Vertical stack of up to 3 composable widget sections.
 * Each section: { widget_type: string, data: any }
 *
 * Sections render via WidgetRenderer with mode="composite" so widget components
 * drop their outer card frame and lay out compactly.
 */
export default function GenericComposite({ widget, onAction }) {
  const { data, title } = widget
  const sections = (data?.sections || []).slice(0, 3)

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      {title && (
        <div className="px-5 py-3 border-b border-gray-100 bg-gray-50">
          <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
        </div>
      )}
      <div className="divide-y divide-gray-100">
        {sections.map((s, i) => (
          <div key={i} className="px-5 py-4">
            <WidgetRenderer
              widget={{ widget: s.widget_type, data: s.data, status: 'pending' }}
              onAction={onAction}
              mode="composite"
            />
          </div>
        ))}
      </div>
    </div>
  )
}
