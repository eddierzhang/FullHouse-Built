import { Icon } from './Icon'

export type ToastState = { title: string; subtitle?: string } | null

export function Toast({ toast }: { toast: ToastState }) {
  if (!toast) return null
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      <div className="toast">
        <Icon name="check" size={16} />
        <div>
          <div className="strong">{toast.title}</div>
          {toast.subtitle && <div className="toast-subtitle">{toast.subtitle}</div>}
        </div>
      </div>
    </div>
  )
}
