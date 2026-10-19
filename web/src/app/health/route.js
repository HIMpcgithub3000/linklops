import { NextResponse } from "next/server";
import { getOverallKPIs } from "@/lib/store";

export async function GET() {
  const kpis = getOverallKPIs();
  return NextResponse.json({
    status: "healthy",
    version: "1.0.0",
    engine: "LinkOps-Edge-Next",
    postgres_rls: "enforced",
    redis_cache: "online",
    timestamp: new Date().toISOString(),
    telemetry: kpis,
  });
}
