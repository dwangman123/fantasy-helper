import { createHash, timingSafeEqual } from "node:crypto";

import { type NextRequest, NextResponse } from "next/server";

// The Trades page, and the two API routes only it calls, sit behind one shared password; the
// rest of the site stays open. Set SITE_PASSWORD to turn it on; unset, as in local dev, Trades is
// open too. The browser asks once (HTTP Basic auth, any user name); a cookie then lets the page's
// own /api calls through without asking again.
const COOKIE = "fh_trades";

export function proxy(request: NextRequest) {
  const password = process.env.SITE_PASSWORD;
  if (!password) return NextResponse.next();
  const token = tokenFor(password);
  if (same(request.cookies.get(COOKIE)?.value ?? "", token)) return NextResponse.next();
  const given = basicPassword(request.headers.get("authorization"));
  if (given === null || !same(given, password)) {
    return new NextResponse("Password required", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="Fantasy Helper trades", charset="UTF-8"' },
    });
  }
  const response = NextResponse.next();
  response.cookies.set(COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: request.nextUrl.protocol === "https:",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return response;
}

// What the cookie holds: a hash of the password, so it changes when the password does.
const tokenFor = (password: string) => createHash("sha256").update(`fh-trades:${password}`).digest("hex");

// Basic auth: "Basic base64(user:password)". Only the password is checked.
function basicPassword(header: string | null): string | null {
  const [scheme, encoded] = header?.split(" ") ?? [];
  if (scheme !== "Basic" || !encoded) return null;
  const decoded = Buffer.from(encoded, "base64").toString("utf8");
  return decoded.slice(decoded.indexOf(":") + 1);
}

function same(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export const config = {
  matcher: ["/trades", "/api/trades", "/api/trade"],
};
