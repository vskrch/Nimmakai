import React, { type ReactNode } from 'react'
import { Button } from './ui'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
  onReset?: () => void
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[Potato ErrorBoundary]', error, info.componentStack)
  }

  private handleReset = () => {
    this.props.onReset?.()
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div className="min-h-[60vh] flex items-center justify-center p-6">
          <div className="max-w-md w-full bg-zinc-900/80 border border-rose-500/20 rounded-2xl p-6 text-center shadow-[0_20px_60px_rgba(0,0,0,0.5)]">
            <div className="w-12 h-12 bg-rose-500/10 rounded-full flex items-center justify-center mx-auto mb-4 border border-rose-500/20">
              <AlertTriangle className="w-6 h-6 text-rose-400" />
            </div>
            <h2 className="text-base font-bold text-white mb-2">Something went wrong</h2>
            <p className="text-xs text-zinc-400 mb-4">
              A runtime error crashed this view. Try refreshing the page or reset the view.
            </p>
            {this.state.error && (
              <div className="bg-zinc-950 rounded-xl p-3 mb-4 text-left">
                <code className="text-[11px] text-rose-300 font-mono break-words">
                  {this.state.error.message}
                </code>
              </div>
            )}
            <div className="flex justify-center gap-3">
              <Button variant="default" onClick={this.handleReset}>
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Reset View</span>
              </Button>
              <Button variant="primary" onClick={() => window.location.reload()}>
                Reload Page
              </Button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
