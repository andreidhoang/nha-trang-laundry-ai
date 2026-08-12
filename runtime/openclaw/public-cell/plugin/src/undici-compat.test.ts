import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { type Server } from "node:net";
import test, { type TestContext } from "node:test";

import {
  Agent,
  MockAgent,
  cacheStores,
  fetch,
  interceptors,
} from "undici";

async function listen(server: Server): Promise<string> {
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  assert(address && typeof address !== "string");
  return `http://127.0.0.1:${address.port}`;
}

async function close(server: Server): Promise<void> {
  const forceClose = (server as Server & { closeAllConnections?: () => void }).closeAllConnections;
  forceClose?.call(server);
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

test("undici 8.9.0 handles local HTTP and redirects", async (t: TestContext) => {
  const server = createHttpServer((requestMessage, response) => {
    if (requestMessage.url === "/redirect") {
      response.writeHead(302, { location: "/ok" }).end();
      return;
    }
    response.writeHead(200, { "content-type": "text/plain" }).end("synthetic-ok");
  });
  const origin = await listen(server);
  t.after(() => close(server));

  const response = await fetch(`${origin}/redirect`, { redirect: "follow" });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "synthetic-ok");
  assert.equal(response.url, `${origin}/ok`);
});

test("undici 8.9.0 cache interceptor does not cross synthetic responses", async (t) => {
  let requests = 0;
  const server = createHttpServer((_request, response) => {
    requests += 1;
    response
      .writeHead(200, { "cache-control": "public, max-age=60", "content-type": "text/plain" })
      .end("cache-safe");
  });
  const origin = await listen(server);
  t.after(() => close(server));
  const dispatcher = new Agent().compose(
    interceptors.cache({ store: new cacheStores.MemoryCacheStore() }),
  );
  t.after(() => dispatcher.close());

  const first = await fetch(`${origin}/cache`, { dispatcher });
  const second = await fetch(`${origin}/cache`, { dispatcher });
  assert.equal(await first.text(), "cache-safe");
  assert.equal(await second.text(), "cache-safe");
  assert.equal(requests, 1);
});

test("undici 8.9.0 fails closed on header timeout", async (t) => {
  const server = createHttpServer((_request, response) => {
    setTimeout(() => response.end("too-late"), 200);
  });
  const origin = await listen(server);
  t.after(() => close(server));

  await assert.rejects(fetch(`${origin}/slow`, { signal: AbortSignal.timeout(25) }), (error) => {
    return error instanceof DOMException && error.name === "TimeoutError";
  });
});

test("undici 8.9.0 propagates cancellation", async (t) => {
  const server = createHttpServer((_request, response) => {
    setTimeout(() => response.end("too-late"), 200);
  });
  const origin = await listen(server);
  t.after(() => close(server));
  const controller = new AbortController();
  const pending = fetch(`${origin}/cancel`, { signal: controller.signal });
  controller.abort();

  await assert.rejects(pending, (error: unknown) => {
    return error instanceof DOMException && error.name === "AbortError";
  });
});

test("undici 8.9.0 rejects malformed response payloads", async (t) => {
  const dispatcher = new MockAgent();
  dispatcher.disableNetConnect();
  dispatcher
    .get("http://synthetic.invalid")
    .intercept({ method: "GET", path: "/malformed" })
    .reply(200, "{not-json", { headers: { "content-type": "application/json" } });
  t.after(() => dispatcher.close());

  const response = await fetch("http://synthetic.invalid/malformed", { dispatcher });
  await assert.rejects(response.json(), SyntaxError);
});

test("undici 8.9.0 exposes deterministic synthetic transport errors", async (t) => {
  const dispatcher = new MockAgent();
  dispatcher.disableNetConnect();
  dispatcher
    .get("http://synthetic.invalid")
    .intercept({ method: "GET", path: "/error" })
    .replyWithError(new Error("synthetic transport failure"));
  t.after(() => dispatcher.close());

  await assert.rejects(fetch("http://synthetic.invalid/error", { dispatcher }), (error: unknown) => {
    return error instanceof TypeError && error.cause instanceof Error;
  });
});
