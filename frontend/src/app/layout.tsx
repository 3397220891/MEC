import type { Metadata } from "next";
import { Jost } from "next/font/google";
import "./globals.css";

const jost = Jost({ variable: "--font-jost", subsets: ["latin"], weight: ["400", "500", "600", "700"] });

export const metadata: Metadata = {
  title: "OrbitalGuard AI",
  description: "Autonomous satellite close-approach investigation and decision support (advisory only).",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${jost.variable} antialiased`}>{children}</body>
    </html>
  );
}
