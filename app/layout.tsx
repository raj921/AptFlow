import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hyatus Ops · autonomous apartment operations",
  description: "A clear control room for guest, maintenance, and housekeeping work.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
