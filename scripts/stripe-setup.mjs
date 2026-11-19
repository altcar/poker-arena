// Create the Stripe products, prices and webhook for Poker Arena.
//
//   STRIPE_SECRET_KEY=sk_live_or_test node scripts/stripe-setup.mjs https://your-app.example
//
// The script is idempotent: products are found by metadata, prices by
// lookup_key and the webhook by URL. It prints the values to put in Worker
// secrets. It never prints the secret key.

const KEY = process.env.STRIPE_SECRET_KEY;
const APP_URL = (process.argv[2] || process.env.APP_URL || "https://poker-arena.hheylau.workers.dev").replace(/\/$/, "");

if (!KEY) {
  console.error("Set STRIPE_SECRET_KEY first (test or live).");
  process.exit(1);
}

const API = "https://api.stripe.com/v1";

function form(params, prefix = "", out = new URLSearchParams()) {
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    const name = prefix ? `${prefix}[${key}]` : key;
    if (Array.isArray(value)) value.forEach((item, index) => form(item, `${name}[${index}]`, out));
    else if (typeof value === "object") form(value, name, out);
    else out.append(name, String(value));
  }
  return out;
}

async function stripe(method, path, params) {
  const url = method === "GET" && params ? `${API}${path}?${form(params)}` : `${API}${path}`;
  const response = await fetch(url, {
    method,
    headers: {
      authorization: `Bearer ${KEY}`,
      ...(method === "GET" ? {} : { "content-type": "application/x-www-form-urlencoded" }),
    },
    body: method === "GET" ? undefined : form(params || {}).toString(),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || `Stripe HTTP ${response.status}`);
  return data;
}

async function product(plan, name, description) {
  const list = await stripe("GET", "/products", { limit: 100, active: true });
  const found = list.data.find((item) => item.metadata?.app === "poker-arena" && item.metadata?.plan === plan);
  if (found) return found;
  return stripe("POST", "/products", {
    name,
    description,
    metadata: { app: "poker-arena", plan },
  });
}

async function price(plan, interval, amount, prod) {
  const lookup = `arena_${plan}_${interval}`;
  const list = await stripe("GET", "/prices", { lookup_keys: [lookup], active: true, limit: 1 });
  if (list.data.length) return list.data[0];
  return stripe("POST", "/prices", {
    product: prod.id,
    currency: "usd",
    unit_amount: amount,
    recurring: { interval: interval === "annual" ? "year" : "month" },
    lookup_key: lookup,
    nickname: `Poker Arena ${plan} ${interval}`,
    metadata: { app: "poker-arena", plan, interval },
  });
}

const webhookEvents = [
  "checkout.session.completed",
  "customer.subscription.created",
  "customer.subscription.updated",
  "customer.subscription.deleted",
  "invoice.paid",
  "invoice.payment_failed",
];

async function main() {
  const pro = await product("pro", "Poker Arena Pro", "Five bots, unlimited rated challenges and two paid entries a month.");
  const studio = await product("studio", "Poker Arena Studio", "25 bots, five seats, private leagues and API access.");

  const prices = {
    STRIPE_PRICE_PRO_MONTHLY: await price("pro", "monthly", 1900, pro),
    STRIPE_PRICE_PRO_ANNUAL: await price("pro", "annual", 19000, pro),
    STRIPE_PRICE_STUDIO_MONTHLY: await price("studio", "monthly", 9900, studio),
    STRIPE_PRICE_STUDIO_ANNUAL: await price("studio", "annual", 99000, studio),
  };

  const webhookUrl = `${APP_URL}/api/webhooks/stripe`;
  const endpoints = await stripe("GET", "/webhook_endpoints", { limit: 100 });
  let endpoint = endpoints.data.find((item) => item.url === webhookUrl);
  let createdSecret = null;
  if (!endpoint) {
    endpoint = await stripe("POST", "/webhook_endpoints", {
      url: webhookUrl,
      description: "Poker Arena billing",
      enabled_events: webhookEvents,
    });
    createdSecret = endpoint.secret;
  }

  console.log("Stripe resources ready.\n");
  console.log(`Webhook: ${webhookUrl} (${endpoint.id})`);
  console.log(`Events:  ${webhookEvents.join(", ")}\n`);
  if (createdSecret) {
    console.log(`STRIPE_WEBHOOK_SECRET=${createdSecret}`);
  } else {
    console.log("Webhook already existed. Copy its signing secret from the Stripe dashboard.");
  }
  for (const [name, value] of Object.entries(prices)) console.log(`${name}=${value.id}`);
  console.log("\nAdd these as Worker secrets, then redeploy:");
  console.log("  npx wrangler secret put STRIPE_SECRET_KEY");
  console.log("  npx wrangler secret put STRIPE_WEBHOOK_SECRET");
  for (const name of Object.keys(prices)) console.log(`  npx wrangler secret put ${name}`);
  console.log("  npx wrangler secret put APP_URL   # optional; defaults to the request origin");
  console.log("\nTest with card 4242 4242 4242 4242, any future expiry and any CVC.");
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
