import { Icon } from '../components/Icon'

export type GridShift = { id: string; day: number; start: string; end: string; role: string; locked?: boolean; note?: string }
export type GridRow = { id: string; name: string; shifts: GridShift[]; hours: number; overtime?: boolean }

/**
 * The week's schedule as a grid: a row per person, a column per day, a bar per shift with its role and times.
 * Open shifts sit in their own row on top; a totals column shows each person's hours, with a mark (an icon and
 * a word, not a colour) when they pass the overtime threshold. Each shift is a button, so the grid works from
 * the keyboard.
 *
 * This is the display grid. Editing a shift (dragging an edge, the edit dialog, locking) is added with schedule
 * generation in phase 6; until then `onPick` is where a page hangs its own action. Below 1024 px the page
 * shows a read-only list by day instead, as the spec asks.
 */
export function ScheduleGrid({ days, open, rows, onPick }: { days: string[]; open: GridShift[]; rows: GridRow[]; onPick?: (shift: GridShift) => void }) {
  const cell = (shifts: GridShift[], day: number) =>
    shifts
      .filter((s) => s.day === day)
      .map((s) => (
        <button key={s.id} className={`ml-shift${s.locked ? ' locked' : ''}`} onClick={() => onPick?.(s)}>
          <span className="ml-shift-time">
            {s.start}–{s.end}
          </span>
          <span className="ml-shift-role">{s.role}</span>
          {s.locked && <span className="ml-shift-lock">Locked</span>}
        </button>
      ))
  return (
    <div className="table-wrap ml-grid-wrap">
      <table className="ml-grid">
        <thead>
          <tr>
            <th scope="col">Person</th>
            {days.map((d) => (
              <th key={d} scope="col">
                {d}
              </th>
            ))}
            <th scope="col" className="num">
              Hours
            </th>
          </tr>
        </thead>
        <tbody>
          <tr className="ml-grid-open">
            <th scope="row">Open shifts</th>
            {days.map((d, i) => (
              <td key={d}>{cell(open, i)}</td>
            ))}
            <td className="num">{open.length || ''}</td>
          </tr>
          {rows.map((row) => (
            <tr key={row.id}>
              <th scope="row">{row.name}</th>
              {days.map((d, i) => {
                const shifts = cell(row.shifts, i)
                return <td key={d}>{shifts.length ? shifts : <span className="muted small">off</span>}</td>
              })}
              <td className="num strong">
                {row.hours}
                {row.overtime && (
                  <span className="ml-overtime">
                    <Icon name="alert" size={14} /> Overtime
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
