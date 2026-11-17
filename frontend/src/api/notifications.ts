import { get, send } from './client'

export type Notice = { id: string; kind: string; title: string; body: string | null; link: string | null; created_at: string; read: boolean }
export type Notices = { unread: number; items: Notice[] }

export const fetchNotices = () => get<Notices>('/api/v1/notifications')
export const markNoticesRead = (ids?: string[]) => send<Notices>('POST', '/api/v1/notifications/read', { ids: ids ?? [] })
