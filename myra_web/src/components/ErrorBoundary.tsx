import React, { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Caught error:', error);
    if (info.componentStack) {
      console.error('[ErrorBoundary] Component stack:', info.componentStack);
    }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div className="bg-[#1a1c24] border border-red-500/20 rounded-xl p-4 flex flex-col items-center gap-2 text-center">
          <div className="text-[10px] text-red-400 font-mono">Widget failed to load</div>
          <div className="text-[9px] text-[#888] font-mono max-w-[300px] truncate">{this.state.error?.message}</div>
          <button
            onClick={this.handleRetry}
            className="text-[10px] font-mono text-cyan-400 hover:text-cyan-300 transition-colors underline"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
