/**
 * Router shell. Deletes + rewrites frontend/v0-spa:frontend/src/App.tsx
 * (single-session SPA, 240 lines) per frontend/D4_rehydration_spec.md §6.
 *
 * Routes:
 *   /            public marketing landing (Story #11)
 *   /sign-in/*   Clerk <SignIn> (Story #2) — clears query cache on mount (§4.4)
 *   /sign-up/*   Clerk <SignUp>  (Story #2)
 *   /dashboard   gated: <SignedIn> -> Dashboard; <SignedOut> -> RedirectToSignIn
 *
 * Clerk routing="path" pattern:
 * https://clerk.com/docs/components/authentication/sign-in (accessed
 * 2026-05-16) — the catch-all `/sign-in/*` route lets Clerk own its
 * sub-routes (factor-two, SSO callback, etc.).
 */

import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import {
  RedirectToSignIn,
  SignedIn,
  SignedOut,
  SignIn,
  SignUp,
  useAuth,
  useUser,
} from "@clerk/clerk-react";
import { useQueryClient } from "@tanstack/react-query";
import { Landing } from "./pages/Landing";
import { Dashboard } from "./pages/Dashboard";
import { AuthErrorBoundary } from "./components/AuthErrorBoundary";
import { clearJobsCache } from "./lib/queries";
import { setSentryUser } from "./lib/sentry";

function AuthScreen({ kind }: { kind: "in" | "up" }) {
  const qc = useQueryClient();
  // §4.4: clearing the cache on the sign-in screen prevents the next
  // signed-in user from seeing the previous tenant's cached jobs.
  useEffect(() => {
    clearJobsCache(qc);
  }, [qc]);

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      {kind === "in" ? (
        <SignIn
          routing="path"
          path="/sign-in"
          signUpUrl="/sign-up"
          forceRedirectUrl="/dashboard"
        />
      ) : (
        <SignUp
          routing="path"
          path="/sign-up"
          signInUrl="/sign-in"
          forceRedirectUrl="/dashboard"
        />
      )}
    </main>
  );
}

/** Tag Sentry with the opaque Clerk identity once loaded (dispatch §5). */
function SentryIdentity() {
  const { user } = useUser();
  const { orgId } = useAuth();
  useEffect(() => {
    if (user) setSentryUser(user.id, orgId ?? null);
  }, [user, orgId]);
  return null;
}

export function App() {
  return (
    <>
      <SignedIn>
        <SentryIdentity />
      </SignedIn>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/sign-in/*" element={<AuthScreen kind="in" />} />
        <Route path="/sign-up/*" element={<AuthScreen kind="up" />} />
        <Route
          path="/dashboard"
          element={
            <>
              <SignedIn>
                <AuthErrorBoundary>
                  <Dashboard />
                </AuthErrorBoundary>
              </SignedIn>
              <SignedOut>
                <RedirectToSignIn />
              </SignedOut>
            </>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
