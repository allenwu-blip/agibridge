/**
 * Provider wiring per frontend/D4_rehydration_spec.md §4.5 + acceptance #2:
 *   <ClerkProvider> -> <QueryClientProvider> -> <BrowserRouter> -> <App />
 * Devtools mount only in DEV.
 *
 * Clerk React quickstart: https://clerk.com/docs/quickstarts/react
 * (accessed 2026-05-16) — ClerkProvider takes `publishableKey`.
 */

import React from "react";
import { createRoot } from "react-dom/client";
import { ClerkProvider } from "@clerk/clerk-react";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { makeQueryClient } from "./lib/queries";
import { initSentry } from "./lib/sentry";
import "./index.css";

initSentry();

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;
if (!publishableKey) {
  throw new Error(
    "VITE_CLERK_PUBLISHABLE_KEY is missing. DevOps wires it in Vercel env " +
      "per dispatches/D4_specs.md env contract.",
  );
}

const queryClient = makeQueryClient();

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root missing in index.html");

createRoot(root).render(
  <React.StrictMode>
    <ClerkProvider publishableKey={publishableKey} afterSignOutUrl="/">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
        {import.meta.env.DEV && (
          <ReactQueryDevtools initialIsOpen={false} />
        )}
      </QueryClientProvider>
    </ClerkProvider>
  </React.StrictMode>,
);
