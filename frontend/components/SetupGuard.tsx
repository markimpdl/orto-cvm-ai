"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { api, type SetupState } from "@/lib/api";

/**
 * Sends a machine whose install is not finished to the wizard (/instalar). Asks GET /setup once per page load;
 * the owner's machine (`legacy`) and any API error never redirect: the children always render.
 */
export default function SetupGuard({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const asked = useRef(false);
  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    api<{ state: SetupState }>("/setup")
      .then(({ state }) => {
        if (state && state.done === false && !state.legacy && !window.location.pathname.startsWith("/instalar")) router.replace("/instalar");
      })
      .catch(() => { /* API down: never block the app */ });
  }, [path, router]);
  return <>{children}</>;
}
