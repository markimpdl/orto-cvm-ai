"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Plat = "mac" | "windows";
type ModelState = "installed" | "partial" | "missing" | "incompatible";
type Model = {
  id: string; name: string; platform: Plat | "both"; size: string; compatible: boolean; ready: boolean;
  state: ModelState; disk_bytes: number; engine?: string | null; note?: string | null; used_by?: string[];
};
type Role = {
  key: string; label: string; use: string; note: string | null; fallback: string | null; cloud: string | null;
  select: string | null; current: string | null; in_use: string | null; mac: Model[]; windows: Model[];
};
type Catalog = { platform: Plat; roles: Role[]; inventory: Model[] };

const PLAT_LABEL: Record<Plat, string> = { mac: "Mac (Apple Silicon)", windows: "Windows (placa NVIDIA)" };
const STATE: Record<ModelState, [string, string]> = {
  installed: ["instalado", "bg-go-soft text-go-text"],
  partial: ["incompleto", "bg-chip text-warn-text"],
  missing: ["falta baixar", "bg-chip text-warn-text"],
  incompatible: ["incompatível com este hardware", "bg-line-soft text-muted"],
};

function gb(bytes: number) {
  if (bytes <= 0) return "";
  const g = bytes / 1e9;
  return g >= 10 ? `${Math.round(g)} GB` : g >= 1 ? `${g.toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

/** One option per engine (LTX 2.5 and 2.3 are the same choice: the 2.3 pack only steps in when 2.5 is missing). */
function engineOptions(models: Model[]) {
  const out: { engine: string; names: string[]; ready: boolean }[] = [];
  for (const m of models) {
    if (!m.engine) continue;
    const o = out.find((x) => x.engine === m.engine);
    if (o) { o.names.push(m.name); o.ready ||= m.ready; } else out.push({ engine: m.engine, names: [m.name], ready: m.ready });
  }
  return out;
}

/** Configurações > Modelos: every job a local model does, the Mac and the Windows model for it, and the state of each
 *  model on this machine (installed, size on disk, incompatible hardware). Nothing is downloaded from here. */
export default function EngineModels() {
  const { t } = useT();
  const { setToast } = useAppData();
  const [cat, setCat] = useState<Catalog | null | undefined>(undefined);
  const [saving, setSaving] = useState("");
  const load = () => api<Catalog>("/settings/models").then(setCat).catch(() => setCat(null));
  useEffect(() => { load(); }, []);
  if (cat === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (cat === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  const here = cat.platform;
  const other: Plat = here === "mac" ? "windows" : "mac";

  async function pick(r: Role, engine: string) {
    if (!r.select) return;
    setSaving(r.key);
    const body = r.select === "image" ? { image_engine: engine } : { video: { [r.select.split(":")[1]]: engine } };
    try {
      await api("/settings/engines", { method: "PUT", body: JSON.stringify(body) });
      await load();
      setToast({ msg: t("Modelo trocado. Vale para as próximas etapas.") });
    } catch (e) {
      setToast({ msg: String(e).includes("409") ? t("Esse modelo ainda não foi baixado nesta máquina.") : t("Não consegui salvar. A API está ligada?") });
    } finally {
      setSaving("");
    }
  }

  const name = (r: Role, id: string | null) => [...r.mac, ...r.windows].find((m) => m.id === id)?.name;
  const total = cat.inventory.filter((m) => m.disk_bytes > 0).reduce((a, m) => a + m.disk_bytes, 0);
  const missing = cat.inventory.some((m) => m.state === "missing" || m.state === "partial");

  return (
    <div className="flex flex-col gap-[28px]">
      <section className="card flex flex-col p-5" aria-labelledby="h-roles">
        <div className="mb-3 flex flex-col gap-1">
          <h2 id="h-roles" className="h2 m-0">{t("O que cada modelo faz")}</h2>
          <p className="m-0 text-sm text-body">
            {t("Esta máquina é um {plat}. A lista de opções mostra também o modelo que faz o mesmo trabalho no {other}; ele só pode ser escolhido lá.", { plat: t(PLAT_LABEL[here]), other: t(PLAT_LABEL[other]) })}
          </p>
        </div>
        {cat.roles.map((r) => {
          const id = `role-${r.key}`;
          const mine = r[here];
          const theirs = r[other];
          const inUse = name(r, r.in_use);
          return (
            <div key={r.key} className="flex flex-wrap items-start gap-x-4 gap-y-2 border-t border-line-soft py-3.5">
              <span className="flex min-w-0 flex-[1_1_300px] flex-col gap-0.5">
                {r.select ? <label htmlFor={id} className="text-[15px] font-semibold">{t(r.label)}</label> : <span className="text-[15px] font-semibold">{t(r.label)}</span>}
                <span className="text-[13px] text-body">{t(r.use)}</span>
                {r.note && <span className="text-[13px] text-muted">{t(r.note)}</span>}
                {r.cloud && <span className="text-[13px] text-muted">{t("Também pode usar o {name} (pago, na nuvem).", { name: r.cloud })}</span>}
              </span>
              <span className="flex min-w-[260px] flex-[0_1_340px] flex-col gap-1">
                {r.select ? (
                  <select id={id} className="input font-normal" value={`${here}:${r.current ?? ""}`} disabled={saving === r.key}
                    onChange={(e) => pick(r, e.target.value.split(":")[1])}>
                    <optgroup label={t("{plat} — esta máquina", { plat: t(PLAT_LABEL[here]) })}>
                      {engineOptions(mine).map((o) => (
                        <option key={o.engine} value={`${here}:${o.engine}`} disabled={!o.ready && o.engine !== r.current}>
                          {o.names.join(" / ")}{!o.ready ? ` (${t("falta baixar")})` : ""}
                        </option>
                      ))}
                    </optgroup>
                    <optgroup label={t("{plat} — outra máquina", { plat: t(PLAT_LABEL[other]) })}>
                      {engineOptions(theirs).length
                        ? engineOptions(theirs).map((o) => <option key={o.engine} value={`${other}:${o.engine}`} disabled>{o.names.join(" / ")}</option>)
                        : <option value={`${other}:none`} disabled>{t("nenhum modelo pronto no código ainda")}</option>}
                    </optgroup>
                  </select>
                ) : (
                  <span className="text-[14px]">
                    <span className="text-muted">{t("Em uso:")}</span> {inUse ?? <span className="text-warn-text">{t("nenhum instalado")}</span>}
                    {mine.length > 1 && <span className="block text-[13px] text-muted">{t("Opções aqui: {names}", { names: mine.map((m) => m.name).join(", ") })}</span>}
                  </span>
                )}
                <span className="text-[13px] text-muted">
                  {theirs.length ? `${t(PLAT_LABEL[other])}: ${theirs.map((m) => m.name).join(", ")}`
                    : r.fallback && other === "windows" ? t(r.fallback) : `${t(PLAT_LABEL[other])}: ${t("sem modelo")}`}
                </span>
                {here === "windows" && !mine.length && r.fallback && <span className="text-[13px] text-warn-text">{t(r.fallback)}</span>}
              </span>
            </div>
          );
        })}
      </section>

      <section className="flex flex-col gap-3" aria-labelledby="h-inventory">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="h-inventory" className="h2 m-0">{t("Modelos nesta máquina")}</h2>
          {total > 0 && <span className="text-sm text-muted">{t("{size} no disco", { size: gb(total) })}</span>}
        </div>
        <div className="card overflow-hidden">
          {cat.inventory.map((m) => {
            const [label, cls] = STATE[m.state];
            return (
              <div key={m.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line-soft px-5 py-3.5 last:border-b-0">
                <span className="flex min-w-0 flex-[1_1_300px] flex-col gap-0.5">
                  <span className="text-[15px] font-semibold">{m.name}</span>
                  <span className="text-[13px] text-muted">{(m.used_by ?? []).map((u) => t(u)).join(" · ")}</span>
                  {m.note && <span className="text-[13px] text-muted">{t(m.note)}</span>}
                </span>
                <span className="text-[13px] text-muted">{m.platform === "both" ? t("Mac e Windows") : t(PLAT_LABEL[m.platform])}</span>
                <span className="min-w-[72px] text-right text-[13px] tabular-nums text-body" title={t("Tamanho esperado: {size}", { size: m.size })}>
                  {m.disk_bytes > 0 ? gb(m.disk_bytes) : m.size}
                </span>
                <span className={`pill ${cls}`}>{t(label)}</span>
              </div>
            );
          })}
        </div>
        <p className="m-0 text-[13px] text-muted">{t("O tamanho é o que ocupa no disco agora; com o sinal ~ é o tamanho do download.")}</p>
        {missing && (
          <p className="m-0 text-sm text-body">{t("Para baixar o que falta, rode em")} <code>backend/</code>: <code>uv run python -m cvm.models_registry download</code></p>
        )}
      </section>
    </div>
  );
}
