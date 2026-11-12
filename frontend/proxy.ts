import { timingSafeEqual } from "node:crypto";

import { type NextRequest, NextResponse } from "next/server";

// One shared password for the whole site, pages and /api/* alike (proxy runs before the
// rewrite to the backend). Set SITE_PASSWORD to turn it on; unset, as in local dev, the site is
// open. The browser asks once and resends the credentials with every request after that.
export function proxy(request: NextRequest) {
  const password = process.env.SITE_PASSWORD;
  if (!password || passwordMatches(request.headers.get("authorization"), password)) return NextResponse.next();
  return new NextResponse("Password required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Fantasy Helper", charset="UTF-8"' },
  });
}

// Basic auth: "Basic base64(user:password)". Any user name works; only the password is checked.
function passwordMatches(header: string | null, password: string): boolean {
  const [scheme, encoded] = header?.split(" ") ?? [];
  if (scheme !== "Basic" || !encoded) return false;
  const decoded = Buffer.from(encoded, "base64").toString("utf8");
  const given = Buffer.from(decoded.slice(decoded.indexOf(":") + 1));
  const expected = Buffer.from(password);
  return given.length === expected.length && timingSafeEqual(given, expected);
}

export const config = {
  // Everything except Next's static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
