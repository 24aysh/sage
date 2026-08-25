import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sage",
  description:
    "Give Sage a GitHub issue and get back a reviewable draft pull request.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-background text-foreground">{children}</body>
    </html>
  );
}
