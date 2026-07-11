import { Component, type ReactNode } from "react";
import { Link } from "react-router-dom";

type AppErrorBoundaryProps = {
  children: ReactNode;
};

type AppErrorBoundaryState = {
  error: Error | null;
};

export class AppErrorBoundary extends Component<
  AppErrorBoundaryProps,
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = {
    error: null,
  };

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
          <div className="max-w-md rounded-md border border-border bg-card p-6 shadow-sm">
            <h1 className="text-lg font-semibold">Portal UI error</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              The portal could not render this view. Refresh the page or return to
              the workflow.
            </p>
            <Link
              className="mt-4 inline-flex text-sm font-medium underline underline-offset-4"
              to="/workflow"
            >
              Go to workflow
            </Link>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
