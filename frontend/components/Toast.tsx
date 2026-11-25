"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useAppData } from "@/components/AppData";
import { useT } from "@/lib/i18n";

/** Green confirmation bar at the top of the content; disappears after a few seconds. */
export default function Toast() {
  const { toast, setToast } = useAppData();
  const { t } = useT();
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(t);
  }, [toast, setToast]);
  if (!toast) return null;
  const link = toast.link ?? { href: "/fabrica", label: t("Ver fila") };
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl bg-go-soft px-[18px] py-3.5 text-[15px] font-semibold text-[#0b3d20]">
      <span className="flex-[1_1_240px]">✓ {toast.msg}</span>
      <Link href={link.href} className="btn btn-sm min-h-9" onClick={() => setToast(null)}>{link.label}</Link>
      <button className="btn btn-sm min-h-9" aria-label={t("Fechar aviso")} onClick={() => setToast(null)}>✕</button>
    </div>
  );
}
