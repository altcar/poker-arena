// Stripe billing: Checkout, Customer Portal and webhooks.
//
// Configure these Worker secrets before taking real payments:
//   STRIPE_SECRET_KEY
//   STRIPE_WEBHOOK_SECRET
//   STRIPE_PRICE_PRO_MONTHLY, STRIPE_PRICE_PRO_ANNUAL
//   STRIPE_PRICE_STUDIO_MONTHLY, STRIPE_PRICE_STUDIO_ANNUAL
//   APP_URL (optional; defaults to the request origin)
//
// The webhook endpoint is POST /api/webhooks/stripe.

import { currentUser, ensurePaperAccount, addPaperTransaction, getSubscription, upsertSubscription } from "./platform.js";
import { PLAN_LIMITS, PLAN_PRICES, PLAN_PAPER_CREDIT } from "./plans.js";

const API = "https://api.stripe.com/v1";

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });

const PRICE_ENV = {
  pro: { monthly: "STRIPE_PRICE_PRO_MONTHLY", annual: "STRIPE_PRICE_PRO_ANNUAL" },
  studio: { monthly: "STRIPE_PRICE_STUDIO_MONTHLY", annual: "STRIPE_PRICE_STUDIO_ANNUAL" },
};


function encode(params, prefix, out) {
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    const name = prefix ? `${prefix}[${key}]` : key;
    if (Array.isArray(value)) {
      value.forEach((item, index) => encode(item, `${name}[${index}]`, out));
    } else if (typeof value === "object") {
      encode(value, name, out);
    } else {
      out.append(name, String(value));
    }
  }
  return out;
}

async function stripe(env, method, path, params = null) {
  if (!env.STRIPE_SECRET_KEY) {
    const error = new Error("Stripe is not configured");
    error.code = "billing_not_configured";
    throw error;
  }
  const options = {
    method,
    headers: { authorization: `Bearer ${env.STRIPE_SECRET_KEY}` },
  };
  if (method !== "GET" && params) {
    const body = encode(params, "", new URLSearchParams());
    options.headers["content-type"] = "application/x-www-form-urlencoded";
    options.body = body.toString();
  }
  const response = await fetch(`${API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error?.message || `Stripe HTTP ${response.status}`);
    error.code = data.error?.code || "stripe_error";
    throw error;
  }
  return data;
}

function priceId(env, plan, interval) {
  return env[PRICE_ENV[plan]?.[interval]];
}

function planFromPrice(env, price) {
  for (const [plan, intervals] of Object.entries(PRICE_ENV)) {
    for (const [interval, key] of Object.entries(intervals)) {
      if (env[key] && env[key] === price) return { plan, interval };
    }
  }
  return null;
}

function billingConfigured(env) {
  return Boolean(
    env.STRIPE_SECRET_KEY &&
    env.STRIPE_WEBHOOK_SECRET &&
    priceId(env, "pro", "monthly") &&
    priceId(env, "pro", "annual") &&
    priceId(env, "studio", "monthly") &&
    priceId(env, "studio", "annual"),
  );
}

async function getOrCreateCustomer(env, user, subscription, appUrl) {
  if (subscription?.stripe_customer_id) return subscription.stripe_customer_id;
  const customer = await stripe(env, "POST", "/customers", {
    email: user.email,
    name: user.handle,
    metadata: { user_id: user.id, handle: user.handle },
  });
  await upsertSubscription(env, {
    userId: user.id,
    plan: subscription?.plan || "open",
    status: subscription?.status || "active",
    interval: subscription?.interval || "monthly",
    customerId: customer.id,
  });
  void appUrl;
  return customer.id;
}

async function handleCheckout(request, env, url, user) {
  if (!user) return json({ error: "log in to subscribe" }, 401);
  if (!billingConfigured(env)) {
    return json({
      error: "Payments are not configured yet. Add the Stripe keys and price IDs as Worker secrets.",
      code: "billing_not_configured",
    }, 503);
  }
  const body = await request.json().catch(() => ({}));
  const plan = body.plan === "studio" ? "studio" : "pro";
  const interval = body.interval === "annual" ? "annual" : "monthly";
  const price = priceId(env, plan, interval);
  if (!price) return json({ error: "That plan is not configured" }, 503);

  const subscription = await getSubscription(env, user.id);
  const customer = await getOrCreateCustomer(env, user, subscription, url.origin);
  const appUrl = env.APP_URL || url.origin;

  const session = await stripe(env, "POST", "/checkout/sessions", {
    mode: "subscription",
    customer,
    client_reference_id: user.id,
    success_url: `${appUrl}/#/account?checkout=success`,
    cancel_url: `${appUrl}/#/pricing?checkout=cancelled`,
    allow_promotion_codes: true,
    billing_address_collection: "auto",
    line_items: [{ price, quantity: 1 }],
    metadata: { user_id: user.id, plan, interval },
    subscription_data: { metadata: { user_id: user.id, plan, interval } },
  });
  return json({ url: session.url, id: session.id });
}

async function handlePortal(request, env, url, user) {
  if (!user) return json({ error: "log in to manage billing" }, 401);
  if (!billingConfigured(env)) {
    return json({ error: "Payments are not configured yet.", code: "billing_not_configured" }, 503);
  }
  const subscription = await getSubscription(env, user.id);
  if (!subscription?.stripe_customer_id) return json({ error: "No billing account yet. Choose a plan first." }, 400);
  const appUrl = env.APP_URL || url.origin;
  const session = await stripe(env, "POST", "/billing_portal/sessions", {
    customer: subscription.stripe_customer_id,
    return_url: `${appUrl}/#/account`,
  });
  return json({ url: session.url });
}

function hex(buffer) {
  return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function equal(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function verifySignature(payload, header, secret) {
  if (!header || !secret) return false;
  const parts = header.split(",");
  const timestamp = parts.find((part) => part.startsWith("t="))?.slice(2);
  const signatures = parts.filter((part) => part.startsWith("v1=")).map((part) => part.slice(3));
  if (!timestamp || !signatures.length) return false;
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${timestamp}.${payload}`));
  const expected = hex(digest);
  return signatures.some((signature) => equal(signature, expected));
}

async function subscriptionFromEvent(env, object) {
  const metadata = object.metadata || {};
  const userId = metadata.user_id;
  if (!userId) return null;
  const price = object.items?.data?.[0]?.price?.id;
  const found = planFromPrice(env, price);
  const plan = metadata.plan && metadata.plan !== "open" ? metadata.plan : found?.plan || "pro";
  const interval = metadata.interval || found?.interval || "monthly";
  await upsertSubscription(env, {
    userId,
    plan: object.status === "active" || object.status === "trialing" ? plan : "open",
    status: object.status || "active",
    interval,
    customerId: typeof object.customer === "string" ? object.customer : object.customer?.id,
    subscriptionId: object.id,
    periodEnd: object.current_period_end ? object.current_period_end * 1000 : null,
    cancelAtPeriodEnd: object.cancel_at_period_end ? 1 : 0,
  });
  return { userId, plan, interval };
}

async function handleWebhook(request, env) {
  const payload = await request.text();
  const signature = request.headers.get("stripe-signature");
  if (!env.STRIPE_WEBHOOK_SECRET) return json({ error: "webhook secret not configured" }, 503);
  if (!(await verifySignature(payload, signature, env.STRIPE_WEBHOOK_SECRET))) {
    return json({ error: "invalid signature" }, 400);
  }
  let event;
  try {
    event = JSON.parse(payload);
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }

  const seen = await env.DB.prepare("SELECT id FROM stripe_events WHERE id = ?").bind(event.id).first();
  if (seen) return json({ received: true, duplicate: true });
  await env.DB.prepare("INSERT INTO stripe_events (id, type, created_at) VALUES (?, ?, ?)")
    .bind(event.id, event.type, Date.now())
    .run();

  const object = event.data?.object || {};
  try {
    if (event.type === "checkout.session.completed" && object.mode === "subscription") {
      const userId = object.metadata?.user_id || object.client_reference_id;
      if (userId) {
        await upsertSubscription(env, {
          userId,
          plan: object.metadata?.plan || "pro",
          status: "active",
          interval: object.metadata?.interval || "monthly",
          customerId: typeof object.customer === "string" ? object.customer : object.customer?.id,
          subscriptionId: typeof object.subscription === "string" ? object.subscription : object.subscription?.id,
        });
      }
    } else if (event.type.startsWith("customer.subscription.")) {
      const result = await subscriptionFromEvent(env, object);
      if (event.type === "customer.subscription.deleted" && result?.userId) {
        await env.DB.prepare("UPDATE subscriptions SET plan = 'open', status = 'canceled', updated_at = ? WHERE user_id = ?")
          .bind(Date.now(), result.userId)
          .run();
      }
    } else if (event.type === "invoice.paid") {
      const subscriptionId = typeof object.subscription === "string" ? object.subscription : object.subscription?.id;
      const row = subscriptionId
        ? await env.DB.prepare("SELECT * FROM subscriptions WHERE stripe_subscription_id = ?").bind(subscriptionId).first()
        : null;
      const plan = row?.plan || "pro";
      if (row?.user_id && row.plan !== "open") {
        await addPaperTransaction(
          env,
          row.user_id,
          "subscription_credit",
          PLAN_PAPER_CREDIT[plan] || 25000,
          `${plan === "studio" ? "Studio" : "Pro"} monthly paper credit`,
        );
      }
    } else if (event.type === "invoice.payment_failed") {
      const subscriptionId = typeof object.subscription === "string" ? object.subscription : object.subscription?.id;
      if (subscriptionId) {
        await env.DB.prepare("UPDATE subscriptions SET status = 'past_due', updated_at = ? WHERE stripe_subscription_id = ?")
          .bind(Date.now(), subscriptionId)
          .run();
      }
    }
  } catch (error) {
    console.error("stripe webhook", event.type, error);
    await env.DB.prepare("DELETE FROM stripe_events WHERE id = ?").bind(event.id).run();
    return json({ error: String(error.message || error) }, 500);
  }
  return json({ received: true });
}

export async function handleBilling(request, env, url) {
  const path = url.pathname;
  if (path === "/api/webhooks/stripe" && request.method === "POST") {
    return handleWebhook(request, env);
  }
  if (!path.startsWith("/api/billing")) return null;

  const user = await currentUser(request, env);

  if (request.method === "GET" && path === "/api/billing") {
    const subscription = user ? await getSubscription(env, user.id) : null;
    if (user) await ensurePaperAccount(env, user.id);
    return json({
      configured: billingConfigured(env),
      plan: subscription?.plan || "open",
      subscription: subscription
        ? {
            plan: subscription.plan,
            status: subscription.status,
            interval: subscription.interval,
            currentPeriodEnd: subscription.current_period_end,
            cancelAtPeriodEnd: Boolean(subscription.cancel_at_period_end),
            hasCustomer: Boolean(subscription.stripe_customer_id),
          }
        : { plan: "open", status: "active", interval: "monthly", hasCustomer: false },
      prices: {
        pro: { monthly: Boolean(priceId(env, "pro", "monthly")), annual: Boolean(priceId(env, "pro", "annual")) },
        studio: { monthly: Boolean(priceId(env, "studio", "monthly")), annual: Boolean(priceId(env, "studio", "annual")) },
      },
    });
  }

  if (request.method === "POST" && path === "/api/billing/checkout") {
    return handleCheckout(request, env, url, user);
  }

  if (request.method === "POST" && path === "/api/billing/portal") {
    return handlePortal(request, env, url, user);
  }

  return json({ error: "not found" }, 404);
}
