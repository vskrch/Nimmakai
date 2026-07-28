import { useState, useCallback, useRef } from 'react'

export type ToastItem = {
  id: string
  message: string
  type: 'ok' | 'err'
  duration?: number
}

export function useToastQueue() {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const idRef = useRef(0)

  const show = useCallback((message: string, type: 'ok' | 'err' = 'ok', duration = 5000) => {
    const id = `${++idRef.current}`
    setToasts(prev => [...prev, { id, message, type, duration }])
  }, [])

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  return { toasts, show, dismiss }
}
