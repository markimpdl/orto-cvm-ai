import type { Metadata } from "next";
import localFont from "next/font/local";
import { AppDataProvider } from "@/components/AppData";
import { LangProvider } from "@/lib/i18n";
import SetupGuard from "@/components/SetupGuard";
import Sidebar from "@/components/Sidebar";
import Toast from "@/components/Toast";
import "./globals.css";

// Shipped with the app (variable latin fonts, SIL OFL, app/fonts/OFL-*.txt): the Google Fonts loader fetched them at the first
// compile, which breaks offline installs and, with Turbopack, whenever Google answers with "l/font?kit=…&skey=…" URLs.
const saira = localFont({ src: "./fonts/Saira-latin.woff2", weight: "100 900", variable: "--font-saira" });
const figtree = localFont({ src: "./fonts/Figtree-latin.woff2", weight: "300 900", variable: "--font-figtree" });

export const metadata: Metadata = {
  title: "Orto CVM",
  description: "Fábrica local de conteúdo em vídeo",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR" className={`${saira.variable} ${figtree.variable}`}>
      <body className="font-sans antialiased">
        <LangProvider>
        <AppDataProvider>
          <div className="flex min-h-screen flex-wrap">
            <Sidebar />
            <main className="box-border min-w-0 flex-[999_1_560px] px-[clamp(16px,3.5vw,48px)] pt-9 pb-16">
              <div className="mx-auto flex max-w-[1200px] flex-col gap-7">
                <Toast />
                <SetupGuard>{children}</SetupGuard>
              </div>
            </main>
          </div>
        </AppDataProvider>
        </LangProvider>
      </body>
    </html>
  );
}
