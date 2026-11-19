# Payments and paper accounts

The arena uses **Stripe Checkout** for Pro and Studio subscriptions and Stripe's
signed webhook to keep plans and paper credits in sync. No card data touches the
Worker.

## What is implemented

- `POST /api/billing/checkout` creates a subscription Checkout Session.
- `POST /api/billing/portal` opens the Stripe Customer Portal.
- `POST /api/webhooks/stripe` verifies the Stripe signature and handles
  `checkout.session.completed`, `customer.subscription.*`, `invoice.paid` and
  `invoice.payment_failed`.
- Plan limits are enforced on bot upload: Open 1, Pro 5, Studio 25.
- Paper accounts are created on signup with 10,000 credits. A rated win adds
  250, a loss subtracts 100. Pro adds 25,000 credits per paid month, Studio
  100,000. `POST /api/paper/reset` returns to the starting balance.

## 1. Create the Stripe resources

Use a test key first, then repeat with a live key.

```sh
STRIPE_SECRET_KEY=sk_test_... node scripts/stripe-setup.mjs https://poker-arena.hheylau.workers.dev
```

The script creates:

- Products **Poker Arena Pro** and **Poker Arena Studio**
- Prices at $19/$190 and $99/$990 per month/year
- A webhook endpoint at `/api/webhooks/stripe` with the six billing events

It prints the price IDs and the webhook signing secret. It never prints the
secret key.

## 2. Add Worker secrets

```sh
npx wrangler secret put STRIPE_SECRET_KEY
npx wrangler secret put STRIPE_WEBHOOK_SECRET
npx wrangler secret put STRIPE_PRICE_PRO_MONTHLY
npx wrangler secret put STRIPE_PRICE_PRO_ANNUAL
npx wrangler secret put STRIPE_PRICE_STUDIO_MONTHLY
npx wrangler secret put STRIPE_PRICE_STUDIO_ANNUAL
npx wrangler secret put APP_URL
```

`APP_URL` is optional; it defaults to the request origin. Use
`https://poker-arena.hheylau.workers.dev` in production.

Redeploy:

```sh
npm run deploy
```

## 3. Test

1. Create an account and open **Pricing**.
2. Choose Pro, monthly.
3. Pay with `4242 4242 4242 4242`, any future expiry and any CVC.
4. Stripe redirects to `#/account?checkout=success`.
5. The webhook updates the plan and adds 25,000 paper credits.
6. Open **Account → Manage billing** to test the Customer Portal.

Check the plan and paper balance through the API:

```sh
curl -s -b cookies.txt https://poker-arena.hheylau.workers.dev/api/auth/me
curl -s -b cookies.txt https://poker-arena.hheylau.workers.dev/api/paper
```

## Going live

- Switch to live keys and repeat the setup script.
- Set the live `STRIPE_WEBHOOK_SECRET`.
- Use a live `APP_URL`.
- Enable Stripe Tax / invoices if you need them; the integration already
  collects billing addresses.
- The pricing page reads `/api/billing`; when `configured` is false it shows a
  setup note instead of sending users to a broken checkout.

## Security notes

- Webhook signatures are verified with HMAC-SHA256 and a five-minute tolerance.
- Processed event IDs are stored in `stripe_events` for idempotency.
- The secret key is only read from the Worker environment and is never sent to
  the browser.
- Customer Portal links are generated server-side for the logged-in customer.
