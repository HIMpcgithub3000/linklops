import "./globals.css";

export const metadata = {
  title: "LinkOps — Enterprise URL Shortener & Real-Time Analytics",
  description:
    "High-throughput, multi-tenant link shortening platform with PostgreSQL Row-Level Security, Redis thundering-herd protection, and instant sub-5ms edge redirects.",
  keywords: ["URL shortener", "LinkOps", "FastAPI", "Next.js", "Redis", "PostgreSQL", "RLS"],
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-[#08040F] text-gray-100 min-h-screen selection:bg-purple-500/30 selection:text-purple-200">
        {/* Background Ambient Glows */}
        <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
          <div className="absolute -top-40 left-1/2 -translate-x-1/2 w-[1000px] h-[550px] bg-purple-700/15 rounded-full blur-[140px]" />
          <div className="absolute top-1/3 -left-40 w-[600px] h-[600px] bg-indigo-700/10 rounded-full blur-[160px]" />
          <div className="absolute bottom-10 right-0 w-[500px] h-[500px] bg-fuchsia-700/10 rounded-full blur-[150px]" />
          <div className="absolute inset-0 bg-[radial-gradient(#ffffff08_1px,transparent_1px)] [background-size:24px_24px] opacity-40" />
        </div>

        <div className="relative z-10 flex flex-col min-h-screen">
          {children}
        </div>
      </body>
    </html>
  );
}
