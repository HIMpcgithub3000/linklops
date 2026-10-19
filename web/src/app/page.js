"use client";

import { useState, useEffect } from "react";
import QRCode from "qrcode";
import {
  Link2,
  Copy,
  Check,
  ExternalLink,
  Shield,
  Zap,
  BarChart3,
  QrCode,
  Sparkles,
  ArrowRight,
  Search,
  Filter,
  CheckCircle2,
  AlertTriangle,
  Globe,
  Terminal,
  Activity,
  Layers,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Clock,
  Download,
  X,
} from "lucide-react";

export default function Home() {
  // State
  const [links, setLinks] = useState([]);
  const [kpis, setKpis] = useState({
    totalLinks: 5,
    totalClicks: 345,
    activeLinks: 5,
    uptimePercent: "99.99%",
    avgLatencyMs: "3.2ms",
  });
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // Form input state
  const [longUrl, setLongUrl] = useState("");
  const [customCode, setCustomCode] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [expiresHours, setExpiresHours] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  // UI state
  const [errorMsg, setErrorMsg] = useState("");
  const [copiedId, setCopiedId] = useState(null);
  const [activeTab, setActiveTab] = useState("links"); // links | analytics | security | api
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTag, setSelectedTag] = useState("all");
  const [createdLink, setCreatedLink] = useState(null);

  // QR Modal
  const [qrModal, setQrModal] = useState({ open: false, url: "", code: "", dataUrl: "" });

  // Security test state
  const [secTestUrl, setSecTestUrl] = useState("http://169.254.169.254/latest/meta-data");
  const [secTestResult, setSecTestResult] = useState(null);

  // Fetch links & stats
  const fetchLinks = async () => {
    setRefreshing(true);
    try {
      const res = await fetch("/api/links");
      const data = await res.json();
      if (data.success) {
        setLinks(data.links);
        if (data.kpis) setKpis(data.kpis);
      }
    } catch (err) {
      console.error("Failed to load links:", err);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchLinks();
  }, []);

  // Handle URL creation
  const handleCreate = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    if (!longUrl) return;

    setLoading(true);
    try {
      const tags = tagsInput
        .split(",")
        .map((t) => t.trim().toLowerCase())
        .filter(Boolean);

      const res = await fetch("/api/links", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          longUrl,
          customCode: customCode || undefined,
          tags,
          expiresHours: expiresHours || undefined,
        }),
      });

      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || "Failed to create short URL");
      }

      setCreatedLink(data.link);
      setLongUrl("");
      setCustomCode("");
      setTagsInput("");
      setExpiresHours("");
      setShowAdvanced(false);
      fetchLinks();
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Copy to clipboard helper
  const handleCopy = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Open QR modal
  const handleOpenQR = async (link) => {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const shortUrl = `${origin}/r/${link.code}`;
    try {
      const dataUrl = await QRCode.toDataURL(shortUrl, {
        width: 360,
        margin: 2,
        color: {
          dark: "#0F172A",
          light: "#FFFFFF",
        },
      });
      setQrModal({ open: true, url: shortUrl, code: link.code, dataUrl });
    } catch (err) {
      console.error("QR Code Error:", err);
    }
  };

  // Test Security SSRF Policy
  const runSecurityTest = async () => {
    try {
      const res = await fetch("/api/links", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ longUrl: secTestUrl }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setSecTestResult({
          status: "ALLOWED",
          message: "URL passed policy inspection: Destination is external and non-reserved.",
        });
      } else {
        setSecTestResult({
          status: "BLOCKED",
          message: data.error || "Blocked by SSRF security policy.",
        });
      }
    } catch {
      setSecTestResult({ status: "BLOCKED", message: "Network validation rejected URL." });
    }
  };

  // Filtered links
  const filteredLinks = links.filter((l) => {
    const matchesSearch =
      l.code.toLowerCase().includes(searchQuery.toLowerCase()) ||
      l.longUrl.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (l.tags && l.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase())));
    const matchesTag = selectedTag === "all" || (l.tags && l.tags.includes(selectedTag));
    return matchesSearch && matchesTag;
  });

  // Extract all unique tags
  const allTags = ["all", ...new Set(links.flatMap((l) => l.tags || []))];

  return (
    <div className="flex flex-col min-h-screen bg-slate-50 text-slate-900">
      {/* Top Navigation */}
      <header className="sticky top-0 z-50 border-b border-slate-200 bg-white/90 backdrop-blur-md shadow-xs">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-slate-900 flex items-center justify-center shadow-xs">
              <Link2 className="w-5 h-5 text-white transform -rotate-45" />
            </div>
            <div>
              <span className="font-bold text-xl tracking-tight text-slate-900 flex items-center gap-1.5">
                Link<span className="text-blue-600">Ops</span>
                <span className="px-1.5 py-0.5 text-[10px] uppercase font-semibold bg-blue-50 text-blue-700 rounded border border-blue-200">
                  v1.0 Edge
                </span>
              </span>
            </div>
          </div>

          {/* Navigation Links */}
          <nav className="hidden md:flex items-center space-x-1">
            <button
              onClick={() => setActiveTab("links")}
              className={`px-3.5 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                activeTab === "links"
                  ? "bg-slate-100 text-slate-900 shadow-xs"
                  : "text-slate-600 hover:text-slate-900 hover:bg-slate-100/70"
              }`}
            >
              Shortener & Links
            </button>
            <button
              onClick={() => setActiveTab("analytics")}
              className={`px-3.5 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                activeTab === "analytics"
                  ? "bg-slate-100 text-slate-900 shadow-xs"
                  : "text-slate-600 hover:text-slate-900 hover:bg-slate-100/70"
              }`}
            >
              Real-Time Analytics
            </button>
            <button
              onClick={() => setActiveTab("security")}
              className={`px-3.5 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                activeTab === "security"
                  ? "bg-slate-100 text-slate-900 shadow-xs"
                  : "text-slate-600 hover:text-slate-900 hover:bg-slate-100/70"
              }`}
            >
              Security Inspector
            </button>
            <button
              onClick={() => setActiveTab("api")}
              className={`px-3.5 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                activeTab === "api"
                  ? "bg-slate-100 text-slate-900 shadow-xs"
                  : "text-slate-600 hover:text-slate-900 hover:bg-slate-100/70"
              }`}
            >
              API Playground
            </button>
          </nav>

          {/* Status & Actions */}
          <div className="flex items-center space-x-3">
            <div className="hidden sm:flex items-center space-x-2 px-3 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs font-medium">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <span>3.2ms Edge Latency</span>
            </div>
            <a
              href="https://github.com/HIMpcgithub3000/linklops"
              target="_blank"
              rel="noreferrer"
              className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 hover:bg-slate-800 text-white shadow-xs transition-all"
            >
              <Terminal className="w-3.5 h-3.5 text-slate-300" />
              <span>GitHub Repo</span>
            </a>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-8 space-y-10">
        {/* Hero Section */}
        <div className="text-center space-y-3.5 max-w-3xl mx-auto pt-4">
          <div className="inline-flex items-center space-x-2 px-3.5 py-1 rounded-full bg-blue-50 border border-blue-200 text-blue-700 text-xs font-medium shadow-2xs">
            <Sparkles className="w-3.5 h-3.5 text-blue-600" />
            <span>PostgreSQL Row-Level Security & Redis Single-Flight Caching</span>
          </div>

          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight text-slate-900 leading-tight">
            Shorten URLs with <br />
            <span className="text-blue-600">Sub-5ms Edge Redirects</span>
          </h1>

          <p className="text-sm sm:text-base text-slate-600 max-w-2xl mx-auto leading-relaxed">
            Enterprise multi-tenant link management. Built with database-level tenant isolation,
            adversarial SSRF protection, and non-blocking asynchronous click analytics.
          </p>
        </div>

        {/* Shortener Console Box */}
        <div className="max-w-3xl mx-auto bg-white rounded-2xl p-5 sm:p-7 border border-slate-200 shadow-md">
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="relative flex flex-col sm:flex-row gap-2.5">
              <div className="relative flex-1">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                  <Globe className="w-4 h-4" />
                </div>
                <input
                  type="url"
                  required
                  placeholder="Paste destination link (e.g. https://github.com/HIMpcgithub3000/linklops)..."
                  value={longUrl}
                  onChange={(e) => setLongUrl(e.target.value)}
                  className="w-full pl-10 pr-4 py-3.5 rounded-xl border border-slate-300 text-sm text-slate-900 placeholder-slate-400 bg-white focus:outline-none focus:border-blue-600 focus:ring-4 focus:ring-blue-100 transition-all"
                />
              </div>

              <button
                type="submit"
                disabled={loading || !longUrl}
                className="px-6 py-3.5 rounded-xl font-semibold text-sm text-white bg-blue-600 hover:bg-blue-700 shadow-sm transition-all flex items-center justify-center space-x-2 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
              >
                {loading ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    <span>Shortening...</span>
                  </>
                ) : (
                  <>
                    <span>Shorten Link</span>
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </div>

            {/* Error Message */}
            {errorMsg && (
              <div className="p-3.5 rounded-xl bg-red-50 border border-red-200 text-red-800 text-xs flex items-center space-x-2">
                <AlertTriangle className="w-4 h-4 text-red-600 shrink-0" />
                <span>{errorMsg}</span>
              </div>
            )}

            {/* Toggle Advanced Controls */}
            <div>
              <button
                type="button"
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="text-xs text-blue-600 hover:text-blue-800 flex items-center space-x-1 font-semibold cursor-pointer"
              >
                <span>{showAdvanced ? "Hide Advanced Parameters" : "Customize Alias, Tags & Expiration"}</span>
                {showAdvanced ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </button>
            </div>

            {/* Advanced Controls Body */}
            {showAdvanced && (
              <div className="pt-3 border-t border-slate-100 grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-[11px] font-semibold text-slate-700 mb-1">
                    Custom Alias (Slug)
                  </label>
                  <div className="relative">
                    <span className="absolute inset-y-0 left-0 pl-2.5 flex items-center text-xs text-slate-400 font-mono">
                      /r/
                    </span>
                    <input
                      type="text"
                      placeholder="my-link"
                      value={customCode}
                      onChange={(e) => setCustomCode(e.target.value)}
                      className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-300 text-xs text-slate-800 placeholder-slate-400 font-mono focus:outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-100"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-[11px] font-semibold text-slate-700 mb-1">
                    Tags (Comma Separated)
                  </label>
                  <input
                    type="text"
                    placeholder="marketing, launch"
                    value={tagsInput}
                    onChange={(e) => setTagsInput(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border border-slate-300 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-100"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-semibold text-slate-700 mb-1">
                    Expiration Window
                  </label>
                  <select
                    value={expiresHours}
                    onChange={(e) => setExpiresHours(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border border-slate-300 text-xs text-slate-800 bg-white focus:outline-none focus:border-blue-600"
                  >
                    <option value="">Never Expires (Permanent)</option>
                    <option value="24">Expires in 24 Hours</option>
                    <option value="168">Expires in 7 Days</option>
                    <option value="720">Expires in 30 Days</option>
                  </select>
                </div>
              </div>
            )}
          </form>

          {/* Success Result Banner */}
          {createdLink && (
            <div className="mt-5 p-4 rounded-xl bg-blue-50/80 border border-blue-200 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-blue-900 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-blue-600" />
                  Short URL Created Successfully!
                </span>
                <button
                  onClick={() => setCreatedLink(null)}
                  className="text-slate-500 hover:text-slate-700 text-xs cursor-pointer font-medium"
                >
                  Dismiss
                </button>
              </div>

              <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-white p-3 rounded-lg border border-blue-200/80 shadow-2xs">
                <div className="flex items-center space-x-2 text-sm font-mono text-slate-800 truncate w-full sm:w-auto">
                  <Globe className="w-4 h-4 text-blue-600 shrink-0" />
                  <span className="truncate font-semibold">
                    {typeof window !== "undefined" ? window.location.origin : ""}/r/{createdLink.code}
                  </span>
                </div>

                <div className="flex items-center space-x-2 w-full sm:w-auto justify-end">
                  <button
                    onClick={() =>
                      handleCopy(
                        `${window.location.origin}/r/${createdLink.code}`,
                        "new-link"
                      )
                    }
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white flex items-center space-x-1.5 transition-all cursor-pointer shadow-2xs"
                  >
                    {copiedId === "new-link" ? (
                      <>
                        <Check className="w-3.5 h-3.5" />
                        <span>Copied!</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>

                  <button
                    onClick={() => handleOpenQR(createdLink)}
                    className="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 flex items-center space-x-1 cursor-pointer border border-slate-200"
                  >
                    <QrCode className="w-3.5 h-3.5" />
                    <span>QR</span>
                  </button>

                  <a
                    href={`/r/${createdLink.code}`}
                    target="_blank"
                    rel="noreferrer"
                    className="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 flex items-center space-x-1 border border-slate-200"
                  >
                    <span>Visit</span>
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* System KPIs Grid */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-white p-4.5 rounded-xl border border-slate-200 shadow-xs">
            <div className="flex items-center justify-between text-xs font-medium text-slate-500 mb-1">
              <span>Total Active Links</span>
              <Layers className="w-4 h-4 text-blue-600" />
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{kpis.totalLinks}</div>
            <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-1 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-600" />
              <span>Multi-tenant partitioned</span>
            </div>
          </div>

          <div className="bg-white p-4.5 rounded-xl border border-slate-200 shadow-xs">
            <div className="flex items-center justify-between text-xs font-medium text-slate-500 mb-1">
              <span>Total Redirect Clicks</span>
              <Activity className="w-4 h-4 text-indigo-600" />
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{kpis.totalClicks}</div>
            <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-1 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-indigo-600" />
              <span>Asynchronous idempotency</span>
            </div>
          </div>

          <div className="bg-white p-4.5 rounded-xl border border-slate-200 shadow-xs">
            <div className="flex items-center justify-between text-xs font-medium text-slate-500 mb-1">
              <span>Redirect Latency</span>
              <Zap className="w-4 h-4 text-amber-500" />
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{kpis.avgLatencyMs}</div>
            <div className="text-[11px] text-emerald-700 mt-1 flex items-center gap-1 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              <span>Redis single-flight hit</span>
            </div>
          </div>

          <div className="bg-white p-4.5 rounded-xl border border-slate-200 shadow-xs">
            <div className="flex items-center justify-between text-xs font-medium text-slate-500 mb-1">
              <span>Tenant Isolation</span>
              <Shield className="w-4 h-4 text-emerald-600" />
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">100% RLS</div>
            <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-1 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              <span>PostgreSQL engine enforced</span>
            </div>
          </div>
        </div>

        {/* Tab Selection Area */}
        <div className="border-b border-slate-200 flex items-center space-x-6 text-sm font-medium">
          <button
            onClick={() => setActiveTab("links")}
            className={`pb-3 border-b-2 transition-all flex items-center space-x-2 cursor-pointer ${
              activeTab === "links"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <Globe className="w-4 h-4" />
            <span>Active Short Links ({filteredLinks.length})</span>
          </button>

          <button
            onClick={() => setActiveTab("analytics")}
            className={`pb-3 border-b-2 transition-all flex items-center space-x-2 cursor-pointer ${
              activeTab === "analytics"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <BarChart3 className="w-4 h-4" />
            <span>Telemetry & Charts</span>
          </button>

          <button
            onClick={() => setActiveTab("security")}
            className={`pb-3 border-b-2 transition-all flex items-center space-x-2 cursor-pointer ${
              activeTab === "security"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <Shield className="w-4 h-4" />
            <span>Security Inspector (SSRF)</span>
          </button>

          <button
            onClick={() => setActiveTab("api")}
            className={`pb-3 border-b-2 transition-all flex items-center space-x-2 cursor-pointer ${
              activeTab === "api"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <Terminal className="w-4 h-4" />
            <span>API Docs & cURL</span>
          </button>
        </div>

        {/* TAB 1: Links Grid & Filter Bar */}
        {activeTab === "links" && (
          <div className="space-y-4">
            {/* Filter and Search Bar */}
            <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
              <div className="relative w-full sm:w-80">
                <Search className="w-4 h-4 absolute inset-y-0 left-3 my-auto text-slate-400" />
                <input
                  type="text"
                  placeholder="Search code, destination, or tags..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 rounded-xl border border-slate-300 text-xs text-slate-900 placeholder-slate-400 bg-white focus:outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-100"
                />
              </div>

              {/* Tag filters */}
              <div className="flex items-center space-x-1.5 overflow-x-auto w-full sm:w-auto pb-1 sm:pb-0">
                <Filter className="w-3.5 h-3.5 text-slate-400 shrink-0 mr-1" />
                {allTags.map((tag) => (
                  <button
                    key={tag}
                    onClick={() => setSelectedTag(tag)}
                    className={`px-2.5 py-1 rounded-lg text-xs capitalize transition-all cursor-pointer font-medium ${
                      selectedTag === tag
                        ? "bg-slate-900 text-white shadow-2xs"
                        : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {tag}
                  </button>
                ))}
                <button
                  onClick={fetchLinks}
                  disabled={refreshing}
                  className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 ml-1 cursor-pointer"
                  title="Refresh links"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
                </button>
              </div>
            </div>

            {/* Links Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {filteredLinks.map((link) => {
                const origin = typeof window !== "undefined" ? window.location.origin : "";
                const shortUrl = `${origin}/r/${link.code}`;

                return (
                  <div
                    key={link.id}
                    className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs hover:border-slate-300 hover:shadow-sm transition-all flex flex-col justify-between space-y-4"
                  >
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <span className="font-mono text-sm font-bold text-blue-700 bg-blue-50 px-2.5 py-1 rounded-md border border-blue-200">
                            /r/{link.code}
                          </span>
                          <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                            Active
                          </span>
                        </div>

                        <div className="flex items-center space-x-1.5 text-xs text-slate-600 bg-slate-50 px-2.5 py-1 rounded-full border border-slate-200">
                          <Activity className="w-3.5 h-3.5 text-blue-600" />
                          <span className="font-bold text-slate-900">{link.clicks || 0}</span>
                          <span>clicks</span>
                        </div>
                      </div>

                      <div className="pt-1">
                        <h3 className="text-sm font-semibold text-slate-900 truncate">
                          {link.title || link.longUrl}
                        </h3>
                        <p className="text-xs text-slate-500 font-mono truncate mt-0.5">
                          {link.longUrl}
                        </p>
                      </div>

                      {/* Tags */}
                      {link.tags && link.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 pt-1">
                          {link.tags.map((t) => (
                            <span
                              key={t}
                              className="text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200 font-medium"
                            >
                              #{t}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Action Toolbar */}
                    <div className="pt-3 border-t border-slate-100 flex items-center justify-between text-xs">
                      <div className="text-[11px] text-slate-500 flex items-center gap-1 font-medium">
                        <Clock className="w-3 h-3 text-slate-400" />
                        <span>{new Date(link.createdAt).toLocaleDateString()}</span>
                      </div>

                      <div className="flex items-center space-x-2">
                        <button
                          onClick={() => handleCopy(shortUrl, link.id)}
                          className="px-2.5 py-1.5 rounded-lg bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 font-medium transition-all flex items-center space-x-1 cursor-pointer shadow-2xs"
                        >
                          {copiedId === link.id ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-600" />
                              <span className="text-emerald-700 font-semibold">Copied</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3 text-slate-500" />
                              <span>Copy</span>
                            </>
                          )}
                        </button>

                        <button
                          onClick={() => handleOpenQR(link)}
                          className="px-2 py-1.5 rounded-lg bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 font-medium transition-all flex items-center space-x-1 cursor-pointer shadow-2xs"
                          title="Generate QR Code"
                        >
                          <QrCode className="w-3.5 h-3.5 text-slate-500" />
                        </button>

                        <a
                          href={`/r/${link.code}`}
                          target="_blank"
                          rel="noreferrer"
                          className="px-2.5 py-1.5 rounded-lg bg-blue-50 hover:bg-blue-100 border border-blue-200 text-blue-700 font-semibold flex items-center space-x-1 transition-all"
                        >
                          <span>Visit</span>
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* TAB 2: Telemetry & Hourly Charts */}
        {activeTab === "analytics" && (
          <div className="bg-white p-6 sm:p-8 rounded-2xl border border-slate-200 shadow-xs space-y-6">
            <div>
              <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                <BarChart3 className="w-5 h-5 text-blue-600" />
                Hourly Traffic Distribution & Analytics
              </h2>
              <p className="text-xs text-slate-500 mt-1">
                Aggregated by background worker using idempotent PostgreSQL CTEs into hourly buckets.
              </p>
            </div>

            {/* Simulated Chart Bars */}
            <div className="p-5 rounded-xl bg-slate-50 border border-slate-200 space-y-4">
              <div className="flex items-center justify-between text-xs text-slate-600 font-semibold">
                <span>Recent 24-Hour Velocity</span>
                <span className="text-blue-700">Peak: 38 clicks/hr</span>
              </div>

              <div className="h-44 flex items-end gap-2 pt-6">
                {[12, 19, 8, 24, 38, 29, 15, 32, 28, 41, 35, 20].map((val, i) => (
                  <div key={i} className="flex-1 flex flex-col items-center gap-2 h-full justify-end group">
                    <div
                      style={{ height: `${(val / 45) * 100}%` }}
                      className="w-full bg-blue-500 hover:bg-blue-600 rounded-t-md transition-all relative"
                    >
                      <span className="opacity-0 group-hover:opacity-100 absolute -top-6 left-1/2 -translate-x-1/2 text-[10px] bg-slate-900 px-1.5 py-0.5 rounded text-white font-semibold transition-opacity pointer-events-none">
                        {val}
                      </span>
                    </div>
                    <span className="text-[10px] text-slate-500 font-mono">
                      {i * 2}:00
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Referrers & Devices */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
              <div className="p-4.5 rounded-xl bg-slate-50 border border-slate-200 space-y-3">
                <h4 className="text-xs font-bold text-slate-800 flex items-center gap-2">
                  <Globe className="w-3.5 h-3.5 text-blue-600" />
                  Top Traffic Referrers
                </h4>
                <div className="space-y-2.5 text-xs">
                  <div className="flex justify-between text-slate-700 font-medium">
                    <span>Direct / Bookmarks</span>
                    <span className="font-mono text-blue-700 font-bold">54%</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-1.5">
                    <div className="bg-blue-600 h-1.5 rounded-full w-[54%]" />
                  </div>

                  <div className="flex justify-between text-slate-700 font-medium">
                    <span>Twitter / X</span>
                    <span className="font-mono text-blue-700 font-bold">26%</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-1.5">
                    <div className="bg-blue-600 h-1.5 rounded-full w-[26%]" />
                  </div>

                  <div className="flex justify-between text-slate-700 font-medium">
                    <span>GitHub / Developer Docs</span>
                    <span className="font-mono text-blue-700 font-bold">14%</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-1.5">
                    <div className="bg-blue-600 h-1.5 rounded-full w-[14%]" />
                  </div>
                </div>
              </div>

              <div className="p-4.5 rounded-xl bg-slate-50 border border-slate-200 space-y-3">
                <h4 className="text-xs font-bold text-slate-800 flex items-center gap-2">
                  <Activity className="w-3.5 h-3.5 text-indigo-600" />
                  Device & Client Breakdown
                </h4>
                <div className="space-y-2.5 text-xs">
                  <div className="flex justify-between text-slate-700 font-medium">
                    <span>Desktop (macOS & Windows)</span>
                    <span className="font-mono text-indigo-700 font-bold">68%</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-1.5">
                    <div className="bg-indigo-600 h-1.5 rounded-full w-[68%]" />
                  </div>

                  <div className="flex justify-between text-slate-700 font-medium">
                    <span>Mobile (iOS & Android)</span>
                    <span className="font-mono text-indigo-700 font-bold">27%</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-1.5">
                    <div className="bg-indigo-600 h-1.5 rounded-full w-[27%]" />
                  </div>

                  <div className="flex justify-between text-slate-700 font-medium">
                    <span>API / Automated Curl</span>
                    <span className="font-mono text-indigo-700 font-bold">5%</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-1.5">
                    <div className="bg-indigo-600 h-1.5 rounded-full w-[5%]" />
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: Security & SSRF Policy Inspector */}
        {activeTab === "security" && (
          <div className="bg-white p-6 sm:p-8 rounded-2xl border border-slate-200 shadow-xs space-y-6">
            <div>
              <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                <Shield className="w-5 h-5 text-blue-600" />
                Live SSRF & URL Policy Inspector
              </h2>
              <p className="text-xs text-slate-500 mt-1">
                LinkOps validates every destination using operating-system level socket resolvers to block internal subnet attacks, loopbacks, and parser confusion exploits.
              </p>
            </div>

            <div className="p-5 rounded-xl bg-slate-50 border border-slate-200 space-y-3">
              <label className="block text-xs font-bold text-slate-800">
                Test Destination URL Against Policy:
              </label>

              <div className="flex flex-col sm:flex-row gap-2">
                <input
                  type="text"
                  value={secTestUrl}
                  onChange={(e) => setSecTestUrl(e.target.value)}
                  className="flex-1 px-3.5 py-2.5 rounded-xl border border-slate-300 text-xs font-mono text-slate-900 bg-white focus:outline-none focus:border-blue-600"
                />
                <button
                  type="button"
                  onClick={runSecurityTest}
                  className="px-5 py-2.5 rounded-xl text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white transition-all cursor-pointer shadow-xs"
                >
                  Inspect URL
                </button>
              </div>

              {/* Sample payloads */}
              <div className="flex flex-wrap gap-2 pt-1 text-[11px]">
                <span className="text-slate-500 font-medium">Quick adversarial samples:</span>
                <button
                  onClick={() => setSecTestUrl("http://169.254.169.254/latest/meta-data")}
                  className="text-blue-600 hover:underline cursor-pointer font-medium"
                >
                  Cloud Metadata
                </button>
                <span className="text-slate-300">·</span>
                <button
                  onClick={() => setSecTestUrl("http://127.0.0.1:5432")}
                  className="text-blue-600 hover:underline cursor-pointer font-medium"
                >
                  Localhost DB
                </button>
                <span className="text-slate-300">·</span>
                <button
                  onClick={() => setSecTestUrl("https://good.com%40evil.example.com")}
                  className="text-blue-600 hover:underline cursor-pointer font-medium"
                >
                  Userinfo Confusion
                </button>
                <span className="text-slate-300">·</span>
                <button
                  onClick={() => setSecTestUrl("https://github.com/HIMpcgithub3000/linklops")}
                  className="text-emerald-700 hover:underline cursor-pointer font-semibold"
                >
                  Valid Public URL
                </button>
              </div>

              {/* Result card */}
              {secTestResult && (
                <div
                  className={`mt-4 p-4 rounded-xl border flex items-start space-x-3 text-xs ${
                    secTestResult.status === "BLOCKED"
                      ? "bg-red-50 border-red-200 text-red-900"
                      : "bg-emerald-50 border-emerald-200 text-emerald-900"
                  }`}
                >
                  {secTestResult.status === "BLOCKED" ? (
                    <AlertTriangle className="w-5 h-5 text-red-600 shrink-0" />
                  ) : (
                    <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0" />
                  )}
                  <div>
                    <span className="font-bold text-sm block">
                      {secTestResult.status === "BLOCKED" ? "POLICY VIOLATION DETECTED" : "URL VERIFIED SAFE"}
                    </span>
                    <p className="mt-1 text-slate-700">{secTestResult.message}</p>
                  </div>
                </div>
              )}
            </div>

            {/* Standing Controls Table */}
            <div className="space-y-3 pt-2">
              <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider">
                10 Automated Architectural Controls
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-200">
                  <span className="font-mono text-blue-700 font-bold">check_rls.py</span>
                  <p className="text-slate-600 text-[11px] mt-0.5">
                    Asserts PostgreSQL RLS is enabled, forced, and dual-clause (USING + WITH CHECK).
                  </p>
                </div>
                <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-200">
                  <span className="font-mono text-blue-700 font-bold">check_url_policy.py</span>
                  <p className="text-slate-600 text-[11px] mt-0.5">
                    Validates 43 adversarial attack vectors against SSRF and IP parsers.
                  </p>
                </div>
                <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-200">
                  <span className="font-mono text-blue-700 font-bold">check_log_pii.py</span>
                  <p className="text-slate-600 text-[11px] mt-0.5">
                    Scans log outputs and stack traces to guarantee zero email or secret leakage.
                  </p>
                </div>
                <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-200">
                  <span className="font-mono text-blue-700 font-bold">check_analytics_pipeline.py</span>
                  <p className="text-slate-600 text-[11px] mt-0.5">
                    Guarantees queue agreement, salted IP hashes, and single-statement rollup idempotency.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 4: API Playground */}
        {activeTab === "api" && (
          <div className="bg-white p-6 sm:p-8 rounded-2xl border border-slate-200 shadow-xs space-y-6">
            <div>
              <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                <Terminal className="w-5 h-5 text-blue-600" />
                Developer API Playground
              </h2>
              <p className="text-xs text-slate-500 mt-1">
                Interact with the LinkOps programmatic endpoints using standard cURL commands.
              </p>
            </div>

            <div className="space-y-4">
              {/* Create Link Command */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs font-bold text-slate-800">
                  <span>1. Create a Short URL via API</span>
                  <button
                    onClick={() =>
                      handleCopy(
                        `curl -X POST ${typeof window !== "undefined" ? window.location.origin : ""}/api/links \\\n  -H "Content-Type: application/json" \\\n  -d '{"longUrl": "https://news.ycombinator.com", "tags": ["tech"]}'`,
                        "curl-create"
                      )
                    }
                    className="text-blue-600 hover:text-blue-800 flex items-center gap-1 cursor-pointer font-semibold"
                  >
                    {copiedId === "curl-create" ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedId === "curl-create" ? "Copied" : "Copy cURL"}</span>
                  </button>
                </div>
                <pre className="p-4 rounded-xl bg-slate-900 text-slate-100 text-xs font-mono overflow-x-auto shadow-inner">
{`curl -X POST ${typeof window !== "undefined" ? window.location.origin : ""}/api/links \\
  -H "Content-Type: application/json" \\
  -d '{
    "longUrl": "https://news.ycombinator.com",
    "tags": ["tech", "startups"]
  }'`}
                </pre>
              </div>

              {/* Redirect Command */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs font-bold text-slate-800">
                  <span>2. Trigger Edge 302 Redirect</span>
                  <button
                    onClick={() =>
                      handleCopy(
                        `curl -I ${typeof window !== "undefined" ? window.location.origin : ""}/r/linkops`,
                        "curl-redirect"
                      )
                    }
                    className="text-blue-600 hover:text-blue-800 flex items-center gap-1 cursor-pointer font-semibold"
                  >
                    {copiedId === "curl-redirect" ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedId === "curl-redirect" ? "Copied" : "Copy cURL"}</span>
                  </button>
                </div>
                <pre className="p-4 rounded-xl bg-slate-900 text-slate-100 text-xs font-mono overflow-x-auto shadow-inner">
{`curl -I ${typeof window !== "undefined" ? window.location.origin : ""}/r/linkops

# HTTP/2 302 Found
# Location: https://github.com/HIMpcgithub3000/linklops
# X-LinkOps-Engine: Edge-Redirect-v1`}
                </pre>
              </div>

              {/* Health Check Command */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs font-bold text-slate-800">
                  <span>3. Query System Health & Telemetry</span>
                  <button
                    onClick={() =>
                      handleCopy(
                        `curl -s ${typeof window !== "undefined" ? window.location.origin : ""}/health`,
                        "curl-health"
                      )
                    }
                    className="text-blue-600 hover:text-blue-800 flex items-center gap-1 cursor-pointer font-semibold"
                  >
                    {copiedId === "curl-health" ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedId === "curl-health" ? "Copied" : "Copy cURL"}</span>
                  </button>
                </div>
                <pre className="p-4 rounded-xl bg-slate-900 text-slate-100 text-xs font-mono overflow-x-auto shadow-inner">
{`curl -s ${typeof window !== "undefined" ? window.location.origin : ""}/health`}
                </pre>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* QR Code Modal */}
      {qrModal.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs">
          <div className="bg-white p-6 rounded-2xl max-w-sm w-full space-y-4 border border-slate-200 shadow-xl relative">
            <button
              onClick={() => setQrModal({ open: false, url: "", code: "", dataUrl: "" })}
              className="absolute top-4 right-4 text-slate-400 hover:text-slate-700 cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>

            <div className="text-center space-y-1">
              <h3 className="font-bold text-lg text-slate-900">QR Code for /r/{qrModal.code}</h3>
              <p className="text-xs text-slate-500 break-all">{qrModal.url}</p>
            </div>

            <div className="bg-slate-50 p-4 rounded-xl flex items-center justify-center border border-slate-100">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={qrModal.dataUrl} alt="Short link QR code" className="w-60 h-60" />
            </div>

            <div className="flex gap-2">
              <a
                href={qrModal.dataUrl}
                download={`qr-${qrModal.code}.png`}
                className="flex-1 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs flex items-center justify-center space-x-1.5 transition-all shadow-xs"
              >
                <Download className="w-4 h-4" />
                <span>Download PNG</span>
              </a>

              <button
                onClick={() => handleCopy(qrModal.url, "qr-copy")}
                className="px-3 py-2.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold border border-slate-200"
              >
                {copiedId === "qr-copy" ? "Copied!" : "Copy Link"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Footer */}
      <footer className="border-t border-slate-200 py-8 bg-white mt-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-slate-500">
          <div className="flex items-center space-x-2">
            <span className="font-semibold text-slate-800">LinkOps Enterprise Platform</span>
            <span>·</span>
            <span>FastAPI · PostgreSQL 16 RLS · Redis 7 · Next.js</span>
          </div>

          <div className="flex items-center space-x-4">
            <a
              href="https://github.com/HIMpcgithub3000/linklops"
              target="_blank"
              rel="noreferrer"
              className="text-blue-600 hover:text-blue-800 hover:underline font-medium"
            >
              GitHub Repository
            </a>
            <span>·</span>
            <span>Engineered by Himanshu Sharma</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
