import { Component, ReactNode } from "react";

interface Props { children: ReactNode }
interface State { hasError: boolean; error?: Error }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: any) {
    console.error("[ErrorBoundary]", error, info);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="min-h-screen flex items-center justify-center p-8">
        <div className="glass max-w-lg w-full p-6">
          <h2 className="text-xl font-bold gradient-text mb-2">页面出错了</h2>
          <p className="text-sm text-slate-600 mb-3">
            前端代码遇到未处理的异常。可以尝试刷新页面恢复。
          </p>
          <pre className="text-xs bg-red-50 border border-red-200 rounded p-3 overflow-x-auto text-red-700 font-mono max-h-48">
{this.state.error?.message || "Unknown error"}
{"\n\n"}
{this.state.error?.stack?.split("\n").slice(0, 5).join("\n")}
          </pre>
          <div className="flex gap-2 mt-4">
            <button className="btn-primary" onClick={() => location.reload()}>刷新页面</button>
            <button className="btn" onClick={() => this.setState({ hasError: false })}>忽略继续</button>
          </div>
        </div>
      </div>
    );
  }
}
