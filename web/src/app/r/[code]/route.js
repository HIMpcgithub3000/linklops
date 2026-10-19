import { NextResponse } from "next/server";
import { getLinkByCode, recordClick } from "@/lib/store";

export async function GET(request, context) {
  const { params } = context;
  const code = (await params)?.code;

  if (!code) {
    return NextResponse.redirect(new URL("/?error=missing_code", request.url));
  }

  const link = getLinkByCode(code);

  if (!link) {
    // 404 or redirect back to home with clear message
    return NextResponse.redirect(new URL(`/?not_found=${encodeURIComponent(code)}`, request.url));
  }

  // Asynchronous telemetry logging
  const userAgent = request.headers.get("user-agent") || "";
  const referrer = request.headers.get("referer") || "";

  try {
    recordClick(code, { userAgent, referrer });
  } catch (err) {
    console.error("Click logging error:", err);
  }

  // Edge 302 Redirect with cache control headers
  const response = NextResponse.redirect(link.longUrl, 302);
  response.headers.set("Cache-Control", "no-cache, no-store, must-revalidate");
  response.headers.set("X-LinkOps-Engine", "Edge-Redirect-v1");
  return response;
}
