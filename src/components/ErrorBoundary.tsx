'use client'

import { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  message: string
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info)
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-white">
          <div className="text-center max-w-md px-6">
            <p className="text-[#666] text-sm mb-2">予期しないエラーが発生しました</p>
            <p className="text-[#444] text-xs mb-6">{this.state.message}</p>
            <button
              onClick={() => this.setState({ hasError: false, message: '' })}
              className="px-4 py-2 bg-white text-black text-sm rounded hover:bg-[#eee] transition-colors"
            >
              再試行
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
