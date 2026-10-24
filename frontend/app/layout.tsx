import type { Metadata } from "next";

import { TopBar } from "@/components/TopBar";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Fantasy Helper",
  description: "Matchup odds and waiver rankings for an ESPN 9-category H2H league",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div className="shell">
            <TopBar />
            {children}
          </div>
        </Providers>
      </body>
    </html>
  );
}
