import { type ChangeEvent, useState } from 'react'
import { type ImportResult, importInventory } from '../api/client'
import { qty } from '../lib/format'
import { announceDataChanged } from '../lib/useLoad'

/** Largest file read in the browser; the server refuses more anyway. */
const MAX_BYTES = 1_000_000

const TEMPLATE = [
  'Item,Unit,On hand,Reorder at,Reorder quantity,Unit cost,Supplier,Pack,Units per pack',
  'Tomatoes,lb,40,15,30,2.90,Green Valley Farms,,',
  'Eggs,each,90,60,180,0.22,Harbor Dairy,case,180',
  'Olive oil,L,8,4,12,12.00,Sysco Foods,,',
].join('\n')

const STATUS: Record<string, { label: string; badge: string }> = {
  new: { label: 'New', badge: 'badge-success' },
  update: { label: 'Update', badge: 'badge-info' },
  skip: { label: 'Skip', badge: '' },
  error: { label: 'Problem', badge: 'badge-danger' },
}

/**
 * Bring stock in from a spreadsheet: pick a CSV, see exactly what will
 * happen to each row, then import. Nothing is saved until the preview is
 * clean -- a file with a problem row is imported whole or not at all.
 */
export function ImportInventory({ onImported }: { onImported: (result: ImportResult) => void }) {
  const [fileName, setFileName] = useState<string | null>(null)
  const [csv, setCsv] = useState<string | null>(null)
  const [updateExisting, setUpdateExisting] = useState(false)
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const check = async (text: string, update: boolean) => {
    setBusy(true)
    setError(null)
    try {
      setPreview(await importInventory(text, { dryRun: true, updateExisting: update }))
    } catch (err) {
      setPreview(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = '' // choosing the same file again should still re-read it
    if (!file) return
    setFileName(file.name)
    setPreview(null)
    if (file.size > MAX_BYTES) {
      setCsv(null)
      return setError('That file is over 1 MB. Split it into smaller files and import each.')
    }
    if (/\.xlsx?$/i.test(file.name)) {
      setCsv(null)
      return setError('That’s an Excel file. In Excel choose File › Save As › CSV, then pick the .csv file here.')
    }
    const text = await file.text()
    setCsv(text)
    await check(text, updateExisting)
  }

  const run = async () => {
    if (!csv) return
    setBusy(true)
    setError(null)
    try {
      const result = await importInventory(csv, { dryRun: false, updateExisting })
      if (result.saved) {
        announceDataChanged()
        onImported(result)
        setPreview(null)
        setCsv(null)
        setFileName(null)
      } else {
        setPreview(result) // something changed since the preview: show what's wrong now
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const importing = preview ? preview.new + preview.updated : 0

  return (
    <div className="stack-sm import">
      <p className="muted small">
        Save your stock spreadsheet as a CSV file. Columns are matched by name: Item, Unit, On hand, Reorder at, Unit
        cost, Supplier, and optionally Pack and Units per pack.{' '}
        <a href={`data:text/csv;charset=utf-8,${encodeURIComponent(TEMPLATE)}`} download="fullhouse-inventory-template.csv">
          Download a template
        </a>
        .
      </p>

      <div className="row wrap">
        <label className="btn btn-secondary file-button">
          {fileName ? 'Choose another file' : 'Choose a CSV file'}
          <input type="file" accept=".csv,text/csv" className="visually-hidden" aria-label="Spreadsheet (CSV)" onChange={onFile} />
        </label>
        {fileName && <span className="muted small">{fileName}</span>}
      </div>

      <label className="row small">
        <input
          type="checkbox"
          checked={updateExisting}
          onChange={(e) => {
            setUpdateExisting(e.target.checked)
            if (csv) void check(csv, e.target.checked)
          }}
        />
        Update items I already track (their details, and stock as a count)
      </label>

      {error && (
        <div className="auth-error" role="alert">
          {error}
        </div>
      )}
      {busy && !preview && <div className="muted small" role="status">Checking the file…</div>}

      {preview && (
        <div className="stack-sm" aria-label="Import preview">
          <div className="callout callout-info" role="status">
            <div className="callout-grow">
              <div className="callout-title">
                {preview.errors
                  ? `${preview.errors} ${preview.errors === 1 ? 'row needs' : 'rows need'} fixing before anything is imported`
                  : `Ready to import ${importing} ${importing === 1 ? 'item' : 'items'}`}
              </div>
              <div className="callout-body">
                {preview.new} new · {preview.updated} to update · {preview.skipped} already tracked (skipped)
                {preview.new_suppliers.length > 0 && ` · new suppliers: ${preview.new_suppliers.join(', ')}`}
              </div>
            </div>
          </div>

          <div className="table-wrap import-table">
            <table className="table table-cards">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Line</th>
                  <th>Unit</th>
                  <th className="num">On hand</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => (
                  <tr key={row.line} className={row.status === 'error' ? 'row-error' : undefined}>
                    <td data-label="Item">{row.name ?? <span className="faint">(no name)</span>}</td>
                    <td data-label="Line">{row.line}</td>
                    <td data-label="Unit">{row.unit ?? '—'}</td>
                    <td data-label="On hand" className="num">
                      {row.quantity_on_hand == null ? '—' : qty(row.quantity_on_hand)}
                    </td>
                    <td data-label="Result">
                      <span className={`badge ${STATUS[row.status].badge}`}>{STATUS[row.status].label}</span>
                      {row.messages.length > 0 && <div className="small import-message">{row.messages.join(' ')}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="row">
            <button className="btn btn-primary" disabled={busy || preview.errors > 0 || importing === 0} onClick={run}>
              {busy ? 'Importing…' : preview.errors ? 'Fix the file to import' : `Import ${importing} ${importing === 1 ? 'item' : 'items'}`}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
