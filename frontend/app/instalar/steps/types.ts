import type { SetupInfo, SetupStep } from "@/lib/api";

export const STEPS: SetupStep[] = ["system", "language", "ai", "features", "models", "engines", "channel", "done"];

/** What the wizard page hands each step. */
export type Wizard = {
  info: SetupInfo;
  choices: Record<string, unknown>;
  /** Merges choices into Setting "setup" (and keeps the current step). */
  save: (choices: Record<string, unknown>) => Promise<void>;
  /** Re-reads GET /setup (blockers follow the saved feature choice). */
  reload: () => Promise<SetupInfo | null>;
  next: () => void;
  back: () => void;
  go: (s: SetupStep) => void;
};

/** Install features (requirements.FEATURES keys) saved in choices.features. */
export const chosenFeatures = (w: Wizard): string[] => (Array.isArray(w.choices.features) ? (w.choices.features as string[]) : ["base"]);
