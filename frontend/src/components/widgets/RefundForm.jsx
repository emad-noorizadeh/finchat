import { useState } from 'react'

function formatMoney(n) {
  const v = Number(String(n || 0).replace(/[$,]/g, ''))
  if (!Number.isFinite(v)) return '$0.00'
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function feeTypeLabel(t) {
  const ft = (t?.feeType || '').toUpperCase()
  if (ft === 'LATE_FEE') return 'Late fee'
  if (ft === 'CASH_ADVANCE_INTEREST') return 'Cash advance interest'
  if (ft === 'ANNUAL_FEE') return 'Annual fee'
  if (ft === 'FOREIGN_TRANSACTION') return 'Foreign transaction fee'
  return (t?.primaryDescription || 'Fee').replace(/\b\w/g, (c) => c.toUpperCase())
}

const CONDITION_ICON = {
  PASS: { icon: '✓', cls: 'text-emerald-600' },
  FAIL: { icon: '✕', cls: 'text-red-600' },
  NOT_APPLICABLE: { icon: '–', cls: 'text-gray-400' },
}

export default function RefundForm({ widget, onAction, status }) {
  const data = widget?.data || {}
  const fees = Array.isArray(data.refundable_transactions) ? data.refundable_transactions : []
  const account = data.account_details || {}
  const stage = data._stage
    || (data.decision ? 'completed' : (data.selected_activity_reference ? 'review' : 'select_fee'))

  const [localSelected, setLocalSelected] = useState(data.selected_activity_reference || '')
  const [busy, setBusy] = useState(false)
  const submitError = data.submit_error

  const selectedFee = fees.find((t) => t.activityReference === (data.selected_activity_reference || localSelected))

  const send = async (id, payload) => {
    setBusy(true)
    try {
      await onAction?.({ id }, widget, payload)
    } finally {
      setBusy(false)
    }
  }

  // --- Completed ---
  if (stage === 'completed') {
    const d = data.decision || {}
    const approved = d.refundDecision === 'APPROVED'
    const borderCls = approved ? 'border-emerald-300' : 'border-amber-300'
    const badgeCls  = approved ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'
    return (
      <div className={`bg-white rounded-xl border ${borderCls} p-5 shadow-sm`}>
        <div className="flex items-center gap-2 mb-3">
          <span className={`px-2 py-0.5 rounded text-[11px] font-semibold ${badgeCls}`}>
            {approved ? '✓ APPROVED' : 'Not approved'}
          </span>
          {selectedFee && (
            <span className="text-sm text-gray-700">{feeTypeLabel(selectedFee)} · {selectedFee.transactionAmount}</span>
          )}
        </div>

        <p className="text-sm text-gray-700 mb-3">{d.decisionReason}</p>

        {approved && (
          <div className="rounded-lg bg-emerald-50/60 border border-emerald-100 p-3 space-y-1.5 text-sm">
            <div className="flex justify-between"><span className="text-gray-500">Refund amount</span><span className="font-semibold text-gray-900">{formatMoney(d.refundAmount)}</span></div>
            {d.effectiveDate && <div className="flex justify-between"><span className="text-gray-500">Effective</span><span>{d.effectiveDate}</span></div>}
            {d.refundTrackingId && <div className="flex justify-between"><span className="text-gray-500">Tracking</span><span className="font-mono text-xs">{d.refundTrackingId}</span></div>}
            {d.postRefundBalance != null && <div className="flex justify-between"><span className="text-gray-500">New balance</span><span>{formatMoney(d.postRefundBalance)}</span></div>}
          </div>
        )}

        {Array.isArray(d.conditionsEvaluated) && d.conditionsEvaluated.length > 0 && (
          <details className="mt-3 text-xs">
            <summary className="cursor-pointer select-none text-gray-600 hover:text-gray-800">Conditions evaluated</summary>
            <ul className="mt-2 space-y-1">
              {d.conditionsEvaluated.map((c, i) => {
                const s = CONDITION_ICON[c.conditionResult] || { icon: '?', cls: 'text-gray-400' }
                return (
                  <li key={i} className="flex gap-2 items-start">
                    <span className={`mt-[1px] ${s.cls}`}>{s.icon}</span>
                    <div>
                      <div className="text-gray-800 font-medium">{c.conditionName}</div>
                      <div className="text-gray-500">{c.conditionDetail}</div>
                    </div>
                  </li>
                )
              })}
            </ul>
          </details>
        )}
      </div>
    )
  }

  // --- Review ---
  if (stage === 'review' && selectedFee) {
    return (
      <div className="bg-white rounded-xl border border-blue-200 p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Confirm refund request</h3>
        <div className="rounded-lg bg-blue-50/60 border border-blue-100 p-3 space-y-1.5 text-sm">
          <div className="flex justify-between"><span className="text-gray-500">Fee</span><span className="font-semibold text-gray-900">{feeTypeLabel(selectedFee)}</span></div>
          <div className="flex justify-between"><span className="text-gray-500">Amount</span><span>{selectedFee.transactionAmount}</span></div>
          <div className="flex justify-between"><span className="text-gray-500">Charged on</span><span>{selectedFee.originDate}</span></div>
          <div className="flex justify-between"><span className="text-gray-500">Account</span><span>{account.accountLabel || '—'}</span></div>
        </div>
        <p className="mt-3 text-[12px] text-gray-500">
          Approval is at the bank's discretion based on account standing, prior refund history,
          and fee type. This doesn't guarantee a refund.
        </p>
        {submitError && (
          <div className="mt-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{submitError}</div>
        )}
        <div className="flex items-center justify-end gap-2 mt-4">
          <button type="button" onClick={() => send('back')} disabled={busy}
            className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800">Back</button>
          <button
            type="button"
            onClick={() => send('submit', { activity_reference: selectedFee.activityReference })}
            disabled={busy}
            className="px-4 py-1.5 text-sm font-medium bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >{busy ? 'Submitting…' : 'Confirm & submit'}</button>
        </div>
      </div>
    )
  }

  // --- Select (default) ---
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <div className="flex items-start justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-800">{widget?.title || 'Request a fee refund'}</h3>
        {data.total_amount != null && (
          <span className="text-xs text-gray-500">Total eligible: <span className="font-medium text-gray-800">{formatMoney(data.total_amount)}</span></span>
        )}
      </div>

      {account.accountLabel && (
        <p className="text-xs text-gray-500 mb-3">{account.accountLabel}</p>
      )}

      {fees.length === 0 ? (
        <p className="text-sm text-gray-500 italic">No refundable fees right now.</p>
      ) : (
        <div className="space-y-2">
          {fees.map((t) => {
            const id = t.activityReference
            const checked = (localSelected || data.selected_activity_reference) === id
            return (
              <label
                key={id}
                className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-all ${
                  checked ? 'border-blue-500 bg-blue-50/40 shadow-sm' : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <input
                  type="radio"
                  name="refund-fee"
                  value={id}
                  checked={checked}
                  onChange={() => setLocalSelected(id)}
                  className="mt-1 cursor-pointer"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between gap-2">
                    <span className="text-sm font-medium text-gray-900">{feeTypeLabel(t)}</span>
                    <span className="text-sm font-semibold text-gray-900">{t.transactionAmount}</span>
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">
                    {t.primaryDescription && <span>{t.primaryDescription}</span>}
                    {t.originDate && <span> · {t.originDate}</span>}
                  </div>
                </div>
              </label>
            )
          })}
        </div>
      )}

      {submitError && (
        <div className="mt-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{submitError}</div>
      )}

      <div className="flex items-center justify-end gap-2 mt-4">
        <button type="button" onClick={() => send('cancel')} disabled={busy}
          className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
        <button
          type="button"
          onClick={() => send('select', { activity_reference: localSelected })}
          disabled={busy || !localSelected}
          className="px-4 py-1.5 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
        >{busy ? 'Loading…' : 'Continue'}</button>
      </div>
    </div>
  )
}
