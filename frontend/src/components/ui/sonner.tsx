import { Toaster as SonnerToaster, toast as sonnerToast } from 'sonner'

/**
 * Potato-themed Sonner toaster.
 * Mounted once at the app root; call `toast.success(msg)` / `toast.error(msg)`
 * from anywhere. Replaces the ad-hoc Toast + useToastQueue combo.
 */
const Toaster = () => (
  <SonnerToaster
    position="bottom-right"
    theme="dark"
    richColors={false}
    closeButton
    toastOptions={{
      style: {
        background: 'rgba(24, 24, 27, 0.95)',
        backdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.12)',
        color: '#fafafa',
        borderRadius: '0.75rem',
        fontSize: '13px',
        boxShadow: '0 20px 50px rgba(0,0,0,0.6)',
      },
      classNames: {
        success: '!border-emerald-500/20',
        error: '!border-rose-500/20',
      },
    }}
  />
)

export { Toaster, sonnerToast as toast }