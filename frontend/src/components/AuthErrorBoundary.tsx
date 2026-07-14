/**
 * Class ErrorBoundary that catches a thrown `UnauthenticatedError` (401, long-
 * idle tab) and renders Clerk's <RedirectToSignIn/> with no broken-chrome
 * flash. Per frontend/D4_rehydration_spec.md §5.3 (auth-expired branch).
 *
 * Class component is required: getDerivedStateFromError has no hook
 * equivalent (https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary,
 * accessed 2026-05-16). Non-auth errors re-throw so Sentry's React error
 * boundary still captures them.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { RedirectToSignIn } from "@clerk/clerk-react";
import { UnauthenticatedError } from "../lib/api";

interface State {
  unauth: boolean;
  fatal: Error | null;
}

export class AuthErrorBoundary extends Component<
  { children: ReactNode },
  State
> {
  state: State = { unauth: false, fatal: null };

  static getDerivedStateFromError(error: unknown): State {
    if (error instanceof UnauthenticatedError) {
      return { unauth: true, fatal: null };
    }
    return { unauth: false, fatal: error as Error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (!(error instanceof UnauthenticatedError)) {
      // Let Sentry's global handler see non-auth render errors.
      // eslint-disable-next-line no-console
      console.error("Dashboard render error:", error, info.componentStack);
    }
  }

  render() {
    if (this.state.unauth) return <RedirectToSignIn />;
    if (this.state.fatal) {
      return (
        <section role="alert" className="p-8">
          <h2 className="text-lg font-semibold">Something went wrong.</h2>
          <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
            Reload the page. If it keeps happening, contact support.
          </p>
        </section>
      );
    }
    return this.props.children;
  }
}
