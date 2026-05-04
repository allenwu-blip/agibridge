/**
 * Version footer hook. Phase D A2.2 (DoD #7):
 *   Footer renders `agibridge {git_sha[:7]} · embodied-data {version}` from
 *   /api/v1/version at page load. F-1 transparency requirement.
 *
 * Backend: app/api/version.py:53-59. Cached for the page lifetime — fetched
 * once, never re-polled.
 */

import { useEffect, useState } from "react";
import { fetchVersion } from "./api";
import type { VersionResponse } from "./types";

export function useVersion(): VersionResponse | null {
  const [version, setVersion] = useState<VersionResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchVersion()
      .then((v) => {
        if (!cancelled) setVersion(v);
      })
      .catch(() => {
        // On failure leave footer empty rather than fabricate a version
        // string; F-1 transparency requires the real value or nothing.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return version;
}
