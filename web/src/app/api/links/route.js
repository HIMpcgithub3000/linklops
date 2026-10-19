import { NextResponse } from "next/server";
import { getAllLinks, createLink, deleteLink, toggleLinkStatus, getOverallKPIs } from "@/lib/store";

export async function GET() {
  const links = getAllLinks();
  const kpis = getOverallKPIs();
  return NextResponse.json({ success: true, kpis, links });
}

export async function POST(request) {
  try {
    const body = await request.json();
    const { longUrl, customCode, tags, expiresHours } = body;

    if (!longUrl) {
      return NextResponse.json(
        { success: false, error: "Destination URL is required." },
        { status: 400 }
      );
    }

    const newLink = createLink({
      longUrl,
      customCode,
      tags: Array.isArray(tags) ? tags : [],
      expiresHours: expiresHours ? Number(expiresHours) : null,
    });

    return NextResponse.json({ success: true, link: newLink }, { status: 201 });
  } catch (err) {
    return NextResponse.json(
      { success: false, error: err.message || "Failed to create short link" },
      { status: 400 }
    );
  }
}

export async function PATCH(request) {
  try {
    const body = await request.json();
    const { code, action } = body;

    if (action === "toggle") {
      const updated = toggleLinkStatus(code);
      return NextResponse.json({ success: true, link: updated });
    }

    if (action === "delete") {
      const deleted = deleteLink(code);
      return NextResponse.json({ success: deleted });
    }

    return NextResponse.json({ success: false, error: "Invalid action" }, { status: 400 });
  } catch (err) {
    return NextResponse.json({ success: false, error: err.message }, { status: 400 });
  }
}
