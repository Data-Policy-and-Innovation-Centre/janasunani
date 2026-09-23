import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Mono, Inter } from "next/font/google";
import { SiteHeader } from "@/components/SiteHeader";
import "./globals.css";

// Three roles: Inter for body, Fraunces for headings and figures, Plex Mono
// for kickers and labels. Exposed as CSS variables and consumed by the
// --dpic-font-* tokens in globals.css.
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
  style: ["normal", "italic"],
  axes: ["SOFT", "WONK", "opsz"],
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  variable: "--font-plex-mono",
  display: "swap",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Janasunani — Grievance Redressal Demo",
  description:
    "DPIC demo: AI-assisted grievance intake, redaction, classification and routing.",
};

function Footer() {
  return (
    <footer className="mt-24 bg-maroon-full text-white">
      <div className="mx-auto max-w-[1240px] px-5 py-12 sm:px-7">
        <p className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-white/55">
          Data, Policy and Innovation Centre
        </p>
        <p className="mt-3 font-display text-2xl font-normal">
          University of Chicago &times; Government of Odisha
        </p>
        <div className="mt-8 grid gap-6 border-t border-white/15 pt-6 text-[12.5px] leading-relaxed text-white/65 sm:grid-cols-2">
          <p>
            Demo build. A &ldquo;mock result&rdquo; badge marks illustrative
            grievance responses.
          </p>
          <p>
            Supervisor metrics come from validated aggregate artifacts. No
            grievance text or citizen identifiers are served to this interface.
          </p>
        </div>
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
    <html
      lang="en"
      // The inline script below adds `js` to this element before React
      // hydrates, so the client className legitimately differs from the
      // server's. Without this, React reports a hydration mismatch on every
      // page load.
      suppressHydrationWarning
      className={`h-full ${inter.variable} ${fraunces.variable} ${plexMono.variable}`}
    >
      <body className="flex min-h-full flex-col font-sans">
        {/* Marks the document as scripted before first paint, which is what
            turns the entrance animations on. Without it the page renders in
            its final state rather than blank — see `.js .reveal` in
            globals.css. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `document.documentElement.classList.add('js')`,
          }}
        />
        <SiteHeader />
        <main className="mx-auto w-full max-w-[1240px] flex-1 px-5 sm:px-7">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
