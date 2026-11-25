/** Inline stroke icons (no emoji, per the brand guide). */
const base = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2.2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

export const IconPlus = () => <svg {...base}><path d="M12 5v14M5 12h14" /></svg>;
export const IconBack = () => <svg {...base}><path d="M15 18l-6-6 6-6" /></svg>;
export const IconRefresh = () => <svg {...base}><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></svg>;
export const IconPause = () => <svg {...base} fill="currentColor" stroke="none"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /></svg>;
export const IconPlay = () => <svg {...base} fill="currentColor" stroke="none"><path d="M8 5v14l11-7z" /></svg>;
export const IconCopy = () => <svg {...base}><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>;
export const IconExternal = () => <svg {...base}><path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" /></svg>;
