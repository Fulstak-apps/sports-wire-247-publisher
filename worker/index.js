const SOURCES = [
  "WNBA", "NBA", "NFL", "MLB", "NHL", "NCAA",
  "official teams", "official schools", "transaction wires", "verified reporters"
];

export default {
  async fetch(request, env) {
    return new Response(JSON.stringify({
      service: "sports-wire-247-newsroom-monitor",
      status: "ok",
      sources: SOURCES,
      cron: "*/20 * * * *",
      note: "Source ingestion requires an authorized feed/API configured in Worker secrets."
    }), { headers: { "content-type": "application/json" } });
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runMonitor(env));
  }
};

async function runMonitor(env) {
  // This Worker is intentionally a safe scheduler/health endpoint until an
  // authorized source-ingestion provider is configured. It does not bypass
  // Instagram access controls or scrape private content.
  const now = new Date().toISOString();
  await env.CRASH_OUT_SPORTS_STATE?.put("last_run", now);
}
