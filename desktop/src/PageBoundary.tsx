import React from "react";

interface State {
  failed: boolean;
  message: string;
}

/**
 * Keeps a render error inside one page. Without this, one missing field blanks
 * the whole Builder shell and leaves no way to switch back to another page.
 */
export class PageBoundary extends React.Component<
  { children: React.ReactNode; title: string },
  State
> {
  state: State = { failed: false, message: "" };

  static getDerivedStateFromError(error: unknown): State {
    return { failed: true, message: String(error) };
  }

  componentDidCatch(error: unknown) {
    console.error("页面渲染失败", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <section className="sm-panel" role="alert">
        <h2>{this.props.title}暂时无法显示</h2>
        <p className="sm-help">
          其他页面仍可使用。可切换到别的页面后返回重试；若持续出现，请保留这条信息反馈。
        </p>
        <code className="sm-path">{this.state.message}</code>
      </section>
    );
  }
}
