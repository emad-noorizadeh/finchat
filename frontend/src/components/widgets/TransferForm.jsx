import { useState } from 'react'

function matchHint(hint, options) {
  if (!hint || !Array.isArray(options) || options.length === 0) return null
  const h = String(hint).toLowerCase()
  const last4 = (h.match(/\d{3,}/) || [''])[0]
  for (const o of options) {
    const label = (o.accountLabel || '').toLowerCase()
    const variant = (o.offeringVariant || '').toUpperCase()
    if (last4 && label.includes(last4)) return o
    if (label.includes(h)) return o
    if ((h.includes('check') || h === 'checking') && variant === 'CK') return o
    if ((h.includes('saving') || h === 'savings') && variant === 'SV') return o
    if (h.includes('money market') && variant === 'MA') return o
    if (h.includes('credit') && variant === 'CC') return o
  }
  return null
}

function formatMoney(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '$0.00'
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

// Disclaimer / warning codes the mock bank service (and the real one it
// mirrors) emits. Friendly labels + severity drive the review UI.
const DISCLAIMER_CATALOG = {
  SUFFICIENT_FUNDS: { tone: 'info', label: 'Sufficient funds available' },
  INSUFFICIENT_FUNDS: { tone: 'warn', label: 'Balance may not cover this transfer — an overdraft fee could apply' },
  AMOUNT_EXCEEDS_LIMIT: { tone: 'warn', label: 'Amount exceeds the per-transfer limit' },
  CUT_OFF_TIME: { tone: 'info', label: 'Past the bank cut-off — transfer will continue on the next business day' },
  IMMEDIATE_BANK: { tone: 'info', label: 'Processed immediately between your accounts' },
  TRANSACTION_POSTING_DATE_EST: { tone: 'info', label: 'Posting date is an estimate — subject to bank processing' },
  DUPLICATE_TRANSFER: { tone: 'warn', label: 'Looks like a duplicate — similar transfer made recently' },
  SCHEDULED_MAINTENANCE: { tone: 'warn', label: 'The bank is under maintenance — transfers may be delayed' },
}

function humanizeCode(code) {
  if (typeof code !== 'string') return String(code)
  return code
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/^./, (c) => c.toUpperCase())
}

function classifyDisclaimer(raw) {
  if (!raw) return null
  // Already human text (from a future backend enhancement) — pass through.
  if (typeof raw === 'string' && /\s/.test(raw) && raw.length > 20) {
    return { tone: 'info', label: raw }
  }
  const entry = DISCLAIMER_CATALOG[raw]
  return entry || { tone: 'info', label: humanizeCode(raw) }
}

export default function TransferForm({ widget, onAction, status }) {
  const data = widget?.data || {}

  // Determine current UI stage:
  //   form       — editable form, Transfer button runs validate
  //   review     — user confirms details before the money moves
  //   completed  — final success card
  const stage = data.confirmation_id
    ? 'completed'
    : (data._stage || (data.validation_result?._validation_id ? 'review' : 'form'))

  const seedFrom = data.from_account || matchHint(data.from_account_hint, data.source_options)
  const seedTo = data.to_account || matchHint(data.to_account_hint, data.target_options)

  const [amount, setAmount] = useState(data.amount ?? '')
  const [fromId, setFromId] = useState(
    seedFrom?.accountTempId || seedFrom?.accountReferenceId || ''
  )
  const [toId, setToId] = useState(
    seedTo?.accountTempId || seedTo?.accountReferenceId || ''
  )
  const [busy, setBusy] = useState(false)

  const sourceOptions = Array.isArray(data.source_options) ? data.source_options : []
  const targetOptions = Array.isArray(data.target_options) ? data.target_options : []
  const submitError = data.submit_error

  const lookupOption = (opts, id) =>
    opts.find((o) => (o.accountTempId || o.accountReferenceId) === id)

  const fromAcct = lookupOption(sourceOptions, fromId) || data.from_account
  const toAcct = lookupOption(targetOptions, toId) || data.to_account

  const send = async (id, payload) => {
    setBusy(true)
    try {
      await onAction?.({ id }, widget, payload)
    } finally {
      setBusy(false)
    }
  }

  // --- Completed stage ---
  if (stage === 'completed') {
    return (
      <div className="bg-white rounded-xl border border-emerald-300 p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-emerald-600 text-xl">✓</span>
          <h3 className="text-sm font-semibold text-gray-800">Transfer complete</h3>
        </div>
        <div className="text-sm text-gray-700 space-y-1">
          <div className="flex justify-between">
            <span className="text-gray-500">Amount</span>
            <span className="font-medium">{formatMoney(data.amount)}</span>
          </div>
          {data.from_account?.accountLabel && (
            <div className="flex justify-between">
              <span className="text-gray-500">From</span>
              <span>{data.from_account.accountLabel}</span>
            </div>
          )}
          {data.to_account?.accountLabel && (
            <div className="flex justify-between">
              <span className="text-gray-500">To</span>
              <span>{data.to_account.accountLabel}</span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-gray-500">Confirmation</span>
            <span className="font-mono text-xs">{data.confirmation_id}</span>
          </div>
          {data.effective_date && (
            <div className="flex justify-between">
              <span className="text-gray-500">Effective</span>
              <span>{data.effective_date}</span>
            </div>
          )}
        </div>
      </div>
    )
  }

  // --- Review stage ---
  if (stage === 'review') {
    const rawDisclaimers = [
      ...(data.validation_result?.disclaimers || []),
      ...(data.validation_result?._disclaimers || []),
      ...(data.validation_result?.review?.warnings || []),
      ...(data.validation_result?._warnings || []),
    ]
    const items = Array.from(new Set(rawDisclaimers.filter(Boolean)))
      .map(classifyDisclaimer)
      .filter(Boolean)
    const warnItems = items.filter((x) => x.tone === 'warn')
    const infoItems = items.filter((x) => x.tone !== 'warn')
    const postingDate = data.validation_result?.review?.transactionDate

    return (
      <div className="bg-white rounded-xl border border-blue-200 p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Review transfer</h3>

        <div className="rounded-lg bg-blue-50/60 border border-blue-100 p-3 space-y-1.5 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-500">Amount</span>
            <span className="font-semibold text-gray-900">{formatMoney(data.amount)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">From</span>
            <span className="text-gray-900">{data.from_account?.accountLabel || '—'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">To</span>
            <span className="text-gray-900">{data.to_account?.accountLabel || '—'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">When</span>
            <span className="text-gray-900">Immediate</span>
          </div>
          {postingDate && (
            <div className="flex justify-between">
              <span className="text-gray-500">Posting date</span>
              <span className="text-gray-900">{postingDate}</span>
            </div>
          )}
        </div>

        {warnItems.length > 0 && (
          <ul className="mt-3 text-xs bg-amber-50 border border-amber-200 rounded px-3 py-2 space-y-1">
            {warnItems.map((x, i) => (
              <li key={`w${i}`} className="flex items-start gap-1.5 text-amber-800">
                <span className="mt-[1px]">⚠</span>
                <span>{x.label}</span>
              </li>
            ))}
          </ul>
        )}
        {infoItems.length > 0 && (
          <ul className="mt-2 text-xs text-gray-600 space-y-0.5">
            {infoItems.map((x, i) => (
              <li key={`i${i}`} className="flex items-start gap-1.5">
                <span className="text-gray-400 mt-[1px]">·</span>
                <span>{x.label}</span>
              </li>
            ))}
          </ul>
        )}

        {submitError && (
          <div className="mt-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
            {submitError}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 mt-4">
          <button
            type="button"
            onClick={() => send('back')}
            className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800"
            disabled={busy}
          >
            Back
          </button>
          <button
            type="button"
            onClick={() => send('submit', { validation_id: data.validation_result?._validation_id })}
            disabled={busy}
            className="px-4 py-1.5 text-sm font-medium bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            {busy ? 'Sending…' : 'Confirm & transfer'}
          </button>
        </div>
      </div>
    )
  }

  // --- Form stage (default) ---
  // For Zelle, source≠target sameness check doesn't apply (different ID spaces).
  const isZelle = data.transfer_type === 'zelle'
  const canValidate = amount && fromId && toId && (isZelle || fromId !== toId) && Number(amount) > 0
  const notice = data.notice
  const targetLabel = isZelle ? 'To (Zelle contact)' : 'To'

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-gray-800 mb-3">{widget?.title || 'Confirm transfer'}</h3>

      {notice && (
        <div className="mb-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2">
          {notice}
        </div>
      )}

      <div className="space-y-3">
        <label className="block">
          <span className="text-xs text-gray-600">Amount</span>
          <input
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="0.00"
            className="mt-0.5 w-full px-3 py-2 text-sm border border-gray-200 rounded focus:outline-none focus:ring-2 focus:ring-blue-200"
            disabled={busy}
          />
        </label>

        <label className="block">
          <span className="text-xs text-gray-600">From</span>
          <select
            value={fromId}
            onChange={(e) => setFromId(e.target.value)}
            className="mt-0.5 w-full px-3 py-2 text-sm border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
            disabled={busy}
          >
            <option value="">Select account…</option>
            {sourceOptions.map((o) => {
              const id = o.accountTempId || o.accountReferenceId
              const bal = o.availableBalance ?? o.currentBalInfo?.amt ?? o.balance
              return (
                <option key={id} value={id}>
                  {o.accountLabel || o.displayName}{bal != null ? ` — $${bal}` : ''}
                </option>
              )
            })}
          </select>
        </label>

        <label className="block">
          <span className="text-xs text-gray-600">{targetLabel}</span>
          <select
            value={toId}
            onChange={(e) => setToId(e.target.value)}
            className="mt-0.5 w-full px-3 py-2 text-sm border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
            disabled={busy || targetOptions.length === 0}
          >
            <option value="">{targetOptions.length === 0 ? (isZelle ? 'No Zelle contacts' : 'No eligible accounts') : 'Select…'}</option>
            {targetOptions.map((o) => {
              const id = o.accountTempId || o.accountReferenceId
              const bal = o.availableBalance ?? o.currentBalInfo?.amt ?? o.balance
              const subtitle = o.payee_alias || ''
              return (
                <option key={id} value={id}>
                  {o.accountLabel || o.displayName}{bal != null ? ` — $${bal}` : ''}{subtitle ? ` · ${subtitle}` : ''}
                </option>
              )
            })}
          </select>
        </label>

        {fromId && toId && fromId === toId && (
          <div className="text-xs text-red-600">Source and destination must be different accounts.</div>
        )}

        {submitError && (
          <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1.5">
            {submitError}
          </div>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 mt-4">
        <button
          type="button"
          onClick={() => send('cancel')}
          className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800"
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => send('validate', {
            amount: Number(amount),
            from_account: fromAcct,
            to_account: toAcct,
          })}
          disabled={busy || !canValidate}
          className="px-4 py-1.5 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {busy ? 'Reviewing…' : 'Continue'}
        </button>
      </div>
    </div>
  )
}
