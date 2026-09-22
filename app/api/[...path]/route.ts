const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
]);

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const backend = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const incoming = new URL(request.url);
  const target = `${backend}/api/${path.join("/")}${incoming.search}`;
  const headers = new Headers(request.headers);

  headers.delete("host");
  headers.delete("origin");
  headers.delete("content-length");
  const user = process.env.BACKEND_OPERATOR_USER;
  const password = process.env.BACKEND_OPERATOR_PASSWORD;
  if (user && password) {
    headers.set(
      "authorization",
      `Basic ${Buffer.from(`${user}:${password}`, "utf8").toString("base64")}`,
    );
  } else {
    headers.delete("authorization");
  }

  const body = request.method === "GET" || request.method === "HEAD"
    ? undefined
    : await request.arrayBuffer();
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
  });
  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key)) responseHeaders.set(key, value);
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
