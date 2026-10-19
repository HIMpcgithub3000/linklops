import "./globals.css";

export const metadata = {
  title: "LinkOps — Enterprise URL Shortener & Real-Time Analytics",
  description:
    "High-throughput, multi-tenant link shortening platform with PostgreSQL Row-Level Security, Redis thundering-herd protection, and instant sub-5ms edge redirects.",
  keywords: ["URL shortener", "LinkOps", "FastAPI", "Next.js", "Redis", "PostgreSQL", "RLS"],
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="light">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-slate-50 text-slate-900 min-h-screen selection:bg-blue-100 selection:text-blue-900 antialiased">
        {/* Subtle Background Pattern */}
        <div className="fixed inset-0 pointer-events-none z-0">
          <div className="absolute inset-0 bg-dot-pattern opacity-60" />
          <div className="absolute top-0 inset-x-0 h-96 bg-gradient-to-b from-blue-50/40 via-transparent to-transparent pointer-events-none" />
        </div>

        <div className="relative z-10 flex flex-col min-h-screen">
          {children}
        </div>
      </body>
    </html>
  );
}
