import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  const password = process.env.OPERATOR_PASSWORD;
  if (!password && (process.env.APP_ENV ?? "local") === "local")
    return NextResponse.next();
  const expected = Buffer.from(
    `Basic ${Buffer.from(`operator:${password ?? ""}`).toString("base64")}`,
  );
  const supplied = Buffer.from(request.headers.get("authorization") ?? "");
  if (
    password &&
    expected.length === supplied.length &&
    timingSafeEqual(expected, supplied)
  ) {
    return NextResponse.next();
  }
  return new NextResponse("Operator sign-in required", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Hyatus Ops"',
      "Cache-Control": "no-store",
    },
  });
}
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
