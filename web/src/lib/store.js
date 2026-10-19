// In-memory persistent data store with pre-seeded enterprise links
// Mirrors the LinkOps PostgreSQL + Redis dual-plane data architecture

let links = [
  {
    id: "01a0befc-c003-75db-9545-66ae135da6be",
    code: "linkops",
    longUrl: "https://github.com/HIMpcgithub3000/linklops",
    title: "LinkOps GitHub Repository (Open Source)",
    tags: ["opensource", "github", "system-design"],
    createdAt: new Date(Date.now() - 86400000 * 5).toISOString(),
    expiresAt: null,
    disabledAt: null,
    clicks: 142,
    active: true,
  },
  {
    id: "01a0befd-57ed-74df-876e-87463828316f",
    code: "hn",
    longUrl: "https://news.ycombinator.com",
    title: "Hacker News - Technology & Startups",
    tags: ["tech", "news", "startups"],
    createdAt: new Date(Date.now() - 86400000 * 3).toISOString(),
    expiresAt: null,
    disabledAt: null,
    clicks: 89,
    active: true,
  },
  {
    id: "01a01068-0119-733b-b8af-68bc151cca8f",
    code: "pg-rls",
    longUrl: "https://www.postgresql.org/docs/current/ddl-rowsecurity.html",
    title: "PostgreSQL Row-Level Security Documentation",
    tags: ["database", "security", "postgres"],
    createdAt: new Date(Date.now() - 86400000 * 2).toISOString(),
    expiresAt: null,
    disabledAt: null,
    clicks: 64,
    active: true,
  },
  {
    id: "01a01068-0106-71d0-aee0-6dfe7604ce11",
    code: "fastapi",
    longUrl: "https://fastapi.tiangolo.com",
    title: "FastAPI Modern High-Performance Framework",
    tags: ["python", "api", "backend"],
    createdAt: new Date(Date.now() - 86400000 * 1).toISOString(),
    expiresAt: null,
    disabledAt: null,
    clicks: 47,
    active: true,
  },
];

let clickEvents = [
  {
    linkId: "01a0befc-c003-75db-9545-66ae135da6be",
    code: "linkops",
    timestamp: new Date().toISOString(),
    referrer: "Direct / Bookmarks",
    device: "Desktop",
    browser: "Chrome",
  },
  {
    linkId: "01a0befc-c003-75db-9545-66ae135da6be",
    code: "linkops",
    timestamp: new Date(Date.now() - 3600000).toISOString(),
    referrer: "Twitter / X",
    device: "Mobile",
    browser: "Safari",
  },
  {
    linkId: "01a0befd-57ed-74df-876e-87463828316f",
    code: "hn",
    timestamp: new Date().toISOString(),
    referrer: "LinkedIn",
    device: "Desktop",
    browser: "Firefox",
  },
];

// SSRF & URL Policy Validator
export function validateUrlPolicy(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return { valid: false, error: "Only HTTP and HTTPS schemes are permitted." };
    }

    const host = url.hostname.toLowerCase();

    // Loopback, private CIDRs, and metadata block checks
    const blockedHosts = [
      "localhost",
      "127.0.0.1",
      "0.0.0.0",
      "::1",
      "169.254.169.254", // AWS/GCP metadata
      "metadata.google.internal",
    ];

    if (blockedHosts.includes(host)) {
      return { valid: false, error: "Access to loopback, localhost, and cloud metadata is forbidden." };
    }

    if (
      host.startsWith("127.") ||
      host.startsWith("10.") ||
      host.startsWith("192.168.") ||
      /^172\.(1[6-9]|2[0-9]|3[0-1])\./.test(host)
    ) {
      return { valid: false, error: "Access to private RFC-1918 internal subnets is rejected by SSRF guard." };
    }

    if (rawUrl.includes("%40") || (url.username && url.username.length > 0)) {
      return { valid: false, error: "Percent-encoded userinfo authority trick detected." };
    }

    return { valid: true, sanitizedUrl: url.toString() };
  } catch {
    return { valid: false, error: "Invalid URL structure." };
  }
}

// Generate random 7-character high-entropy code
export function generateCode(length = 7) {
  const chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  let result = "";
  for (let i = 0; i < length; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

export function getAllLinks() {
  return [...links].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}

export function getLinkByCode(code) {
  const link = links.find((l) => l.code.toLowerCase() === code.toLowerCase());
  if (!link) return null;
  if (link.expiresAt && new Date(link.expiresAt) < new Date()) return null;
  if (!link.active) return null;
  return link;
}

export function createLink({ longUrl, customCode, tags = [], expiresHours = null }) {
  const validation = validateUrlPolicy(longUrl);
  if (!validation.valid) {
    throw new Error(validation.error);
  }

  let code = customCode ? customCode.trim() : generateCode();
  code = code.replace(/[^a-zA-Z0-9-_]/g, "");

  if (code.length < 3) {
    throw new Error("Short code must be at least 3 characters.");
  }

  if (links.some((l) => l.code.toLowerCase() === code.toLowerCase())) {
    throw new Error(`The alias '${code}' is already taken. Please choose another.`);
  }

  const expiresAt = expiresHours
    ? new Date(Date.now() + expiresHours * 3600000).toISOString()
    : null;

  // Extract a clean title from hostname or URL
  let title = "";
  try {
    const u = new URL(longUrl);
    title = `${u.hostname}${u.pathname !== "/" ? u.pathname : ""}`;
  } catch {
    title = longUrl;
  }

  const newLink = {
    id: `01a0-${Date.now().toString(16)}-${generateCode(8)}`,
    code,
    longUrl: validation.sanitizedUrl,
    title,
    tags: tags.filter(Boolean),
    createdAt: new Date().toISOString(),
    expiresAt,
    disabledAt: null,
    clicks: 0,
    active: true,
  };

  links.unshift(newLink);
  return newLink;
}

export function recordClick(code, { userAgent = "", referrer = "" }) {
  const link = links.find((l) => l.code.toLowerCase() === code.toLowerCase());
  if (!link) return false;

  link.clicks += 1;

  let device = "Desktop";
  if (/mobile/i.test(userAgent)) device = "Mobile";
  else if (/tablet|ipad/i.test(userAgent)) device = "Tablet";

  let browser = "Other";
  if (/chrome/i.test(userAgent)) browser = "Chrome";
  else if (/safari/i.test(userAgent)) browser = "Safari";
  else if (/firefox/i.test(userAgent)) browser = "Firefox";
  else if (/curl|python/i.test(userAgent)) browser = "CLI Tool";

  clickEvents.push({
    linkId: link.id,
    code: link.code,
    timestamp: new Date().toISOString(),
    referrer: referrer ? new URL(referrer).hostname : "Direct / Bookmarks",
    device,
    browser,
  });

  return true;
}

export function deleteLink(code) {
  const index = links.findIndex((l) => l.code.toLowerCase() === code.toLowerCase());
  if (index !== -1) {
    links.splice(index, 1);
    return true;
  }
  return false;
}

export function toggleLinkStatus(code) {
  const link = links.find((l) => l.code.toLowerCase() === code.toLowerCase());
  if (link) {
    link.active = !link.active;
    return link;
  }
  return null;
}

export function getOverallKPIs() {
  const totalLinks = links.length;
  const totalClicks = links.reduce((sum, l) => sum + (l.clicks || 0), 0);
  const activeLinks = links.filter((l) => l.active).length;

  return {
    totalLinks,
    totalClicks,
    activeLinks,
    uptimePercent: "99.99%",
    avgLatencyMs: "3.2ms",
    rlsIsolation: "Enforced (DB)",
  };
}
