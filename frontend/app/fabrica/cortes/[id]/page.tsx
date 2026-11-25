"use client";

import { use } from "react";
import CutEditor from "@/components/CutEditor";

/** Fábrica > Clips e cortes > one video's cut review (spec docs/superpowers/specs/2026-11-25-editor-de-cortes-design.md). */
export default function CutReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <CutEditor id={Number(id)} />;
}
