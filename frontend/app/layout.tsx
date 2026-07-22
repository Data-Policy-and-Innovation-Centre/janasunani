import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Janasunani — Grievance Redressal Demo",
  description:
    "DPIC demo: AI-assisted grievance intake, redaction, classification and routing.",
};

function Header() {
  return (
    <header className="border-b border-hair bg-surface">
      {/* Slim partnership line */}
      <div className="mx-auto flex max-w-5xl items-center px-6 py-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-maroon">
          University of Chicago &amp; Government of Odisha
        </span>
      </div>
      {/* Nav bar */}
      <nav className="border-t border-hair">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <Link href="/" className="flex items-baseline gap-2">
            <span className="text-lg font-bold text-text-dark">Janasunani</span>
            <span className="text-xs text-text-secondary">
              Grievance Redressal
            </span>
          </Link>
          <div className="flex items-center gap-6 text-sm font-medium">
            <Link
              href="/"
              className="text-text-body transition-colors hover:text-maroon"
            >
              Submit
            </Link>
            <Link
              href="/history"
              className="text-text-body transition-colors hover:text-maroon"
            >
              History
            </Link>
          </div>
        </div>
      </nav>
    </header>
  );
}

function Footer() {
  return (
    <footer className="mt-auto bg-maroon-full text-white">
      <div className="mx-auto flex max-w-5xl flex-col gap-1 px-6 py-4 text-xs">
        <span className="font-semibold">
          University of Chicago &amp; Government of Odisha
        </span>
        <span className="text-white/70">
          Data, Policy and Innovation Centre — demo build. A &quot;mock
          result&quot; badge marks illustrative responses; unmarked results
          come from the live pipeline.
        </span>
      </div>
    </footer>
  );
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <body className="flex min-h-full flex-col font-sans">
        <Header />
        <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
