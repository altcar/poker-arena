import React, { useEffect, useMemo, useState } from "react";
import { Section } from "@astryxdesign/core/Section";
import { VStack } from "@astryxdesign/core/VStack";
import { HStack } from "@astryxdesign/core/HStack";
import { Grid } from "@astryxdesign/core/Grid";
import { Card } from "@astryxdesign/core/Card";
import { Heading } from "@astryxdesign/core/Heading";
import { Text } from "@astryxdesign/core/Text";
import { Button } from "@astryxdesign/core/Button";
import { Badge } from "@astryxdesign/core/Badge";
import { Link } from "@astryxdesign/core/Link";
import { Table, proportional } from "@astryxdesign/core/Table";
import { TabList, Tab } from "@astryxdesign/core/TabList";
import { ProgressBar } from "@astryxdesign/core/ProgressBar";
import { Divider } from "@astryxdesign/core/Divider";
import { PageHeader, SectionHeading, Stat, Price, ErrorBanner, Loading, Prose } from "../components/Common.jsx";
import { api, getPlatform, getBusiness, money, number, percent, bb, formatDate } from "../api.js";
import { go } from "../router.js";
import { useAuth } from "../auth.jsx";

export function Band({ variant = "transparent", padding = 8, children, className = "" }) {
  return (
    <Section variant={variant} padding={padding} className={className}>
      <VStack gap={6} maxWidth={1200} style={{ marginInline: "auto", width: "100%" }}>
        {children}
      </VStack>
    </Section>
  );
}

export function Pricing() {
  const [data, setData] = useState(null);
  const [billing, setBilling] = useState("monthly");
  const [error, setError] = useState(null);
  const [billingInfo, setBillingInfo] = useState(null);
  const [checkoutError, setCheckoutError] = useState("");
  const [busyPlan, setBusyPlan] = useState("");
  const { me, plan: currentPlan } = useAuth();

  useEffect(() => {
    getBusiness().then(setData).catch(setError);
    api("/api/billing").then(setBillingInfo).catch(() => {});
  }, []);

  async function subscribe(plan) {
    setCheckoutError("");
    if (plan === "open") {
      go("#/signup");
      return;
    }
    if (!me) {
      go(`#/signup?plan=${plan}`);
      return;
    }
    setBusyPlan(plan);
    try {
      const data = await api("/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan, interval: billing }),
      });
      if (data.url) window.location.href = data.url;
      else setCheckoutError("Stripe did not return a checkout URL.");
    } catch (cause) {
      setCheckoutError(cause.message);
    } finally {
      setBusyPlan("");
    }
  }

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading plans…" /></Band>;

  const planRows = data.plans.map((plan) => ({
    id: plan.slug,
    feature: plan.name,
    open: plan.slug === "open" ? "Current" : "—",
    pro: plan.slug === "pro" ? "Current" : "—",
    studio: plan.slug === "studio" ? "Current" : "—",
    bots: plan.limits.bots,
    challenges: plan.limits.challenges,
    entries: plan.limits.entries,
    seats: plan.limits.seats,
  }));

  return (
    <>
      <Band>
        <PageHeader
          eyebrow="Pricing"
          title="Free to play. Paid to compete."
          description="The ladder, the forum and every open tournament are free. Pro adds volume and paid events. Studio is for teams and private leagues. A share of every subscription funds the prize pool."
        />
        <TabList value={billing} onChange={setBilling} hasDivider>
          <Tab value="monthly" label="Monthly" />
          <Tab value="annual" label="Annual · 2 months free" />
        </TabList>
        <Grid columns={{ minWidth: 280, max: 3 }} gap={4}>
          {data.plans.map((plan) => {
            const amount = billing === "annual" ? plan.annual : plan.monthly;
            return (
              <Card
                key={plan.slug}
                padding={6}
                elevation={plan.highlight ? "med" : "none"}
                className={plan.highlight ? "plan-highlight" : ""}
              >
                <VStack gap={4}>
                  <HStack gap={2} hAlign="between" vAlign="center">
                    <Heading level={2}>{plan.name}</Heading>
                    {currentPlan === plan.slug ? <Badge label="Your plan" variant="success" /> : plan.highlight ? <Badge label="Most popular" variant="success" /> : null}
                  </HStack>
                  <Price amount={amount} period={amount ? (billing === "annual" ? "/ year" : "/ month") : "forever"} />
                  <Text type="body" color="secondary">{plan.tagline}</Text>
                  <Button
                    label={plan.cta}
                    variant={plan.highlight ? "primary" : "secondary"}
                    isLoading={busyPlan === plan.slug}
                    onClick={() => subscribe(plan.slug)}
                  />
                  <Divider />
                  <VStack gap={2}>
                    {plan.features.map((feature) => (
                      <HStack key={feature} gap={2} vAlign="start">
                        <Text type="body" color="accent" weight="bold">✓</Text>
                        <Text type="body">{feature}</Text>
                      </HStack>
                    ))}
                  </VStack>
                  <Text type="supporting" color="secondary">
                    {plan.limits.bots} bot{plan.limits.bots === 1 ? "" : "s"} · {plan.limits.challenges} · {plan.limits.seats} seat{plan.limits.seats === 1 ? "" : "s"}
                  </Text>
                </VStack>
              </Card>
            );
          })}
        </Grid>
        {checkoutError ? <Text type="body" color="secondary">{checkoutError}</Text> : null}
        <Text type="supporting" color="secondary">
          Prices in USD. Cancel any time. Annual plans are billed once and include two months free.{" "}
          {billingInfo && !billingInfo.configured ? "Stripe checkout is being configured; the keys are not set yet." : "Secure checkout by Stripe."}
        </Text>
      </Band>

      <Band variant="muted">
        <SectionHeading
          eyebrow="Prize pool"
          title={`${money(data.prizePool.seasonPool)} for the Season Final`}
          description="A published pool, a published split and a funding formula you can check."
          action={<Button label="Prize pool details" onClick={() => go("#/prize-pool")} />}
        />
        <Grid columns={{ minWidth: 300, max: 2 }} gap={6}>
          <Card padding={6}>
            <VStack gap={4}>
              <HStack hAlign="between" vAlign="center">
                <VStack gap={0}>
                  <Text type="label" color="secondary">Season 2</Text>
                  <Heading level={2}>{money(data.prizePool.seasonPool)}</Heading>
                </VStack>
                <Badge label="Published" variant="success" />
              </HStack>
              {data.prizePool.payouts.map((row) => (
                <VStack key={row.place} gap={1}>
                  <HStack hAlign="between">
                    <Text type="body" weight="semibold">{row.place}</Text>
                    <Text type="body" hasTabularNumbers>{money(row.amount)}</Text>
                  </HStack>
                  <ProgressBar label={`${row.place} share`} value={row.share * 100} max={100} isLabelHidden />
                </VStack>
              ))}
            </VStack>
          </Card>
          <VStack gap={4}>
            {data.prizePool.fundedBy.map((source) => (
              <Card key={source.source} variant="muted" padding={5}>
                <VStack gap={2}>
                  <HStack hAlign="between">
                    <Text type="body" weight="semibold">{source.source}</Text>
                    <Text type="body" hasTabularNumbers>{percent(source.share)}</Text>
                  </HStack>
                  <ProgressBar label={source.source} value={source.share * 100} max={100} isLabelHidden />
                  <Text type="supporting" color="secondary">{source.note}</Text>
                </VStack>
              </Card>
            ))}
            <Text type="supporting" color="secondary">{data.note}</Text>
          </VStack>
        </Grid>
      </Band>

      <Band>
        <SectionHeading eyebrow="Compare" title="What each plan includes" />
        <Card padding={0}>
          <Table
            data={planRows}
            idKey="id"
            density="balanced"
            dividers="rows"
            columns={[
              { key: "feature", header: "Plan", width: proportional(2, { minWidth: 140 }) },
              { key: "bots", header: "Bots", align: "right" },
              { key: "challenges", header: "Rated challenges" },
              { key: "entries", header: "Paid entries" },
              { key: "seats", header: "Seats", align: "right" },
            ]}
          />
        </Card>
      </Band>

      <Band variant="muted">
        <SectionHeading eyebrow="FAQ" title="The questions people actually ask" />
        <VStack gap={3}>
          {data.faq.map((item) => (
            <Card key={item.q} padding={5}>
              <VStack gap={2}>
                <Heading level={3}>{item.q}</Heading>
                <Text type="body" color="secondary">{item.a}</Text>
              </VStack>
            </Card>
          ))}
        </VStack>
        <HStack gap={2} wrap="wrap">
          <Button label="Read the business plan" variant="primary" onClick={() => go("#/business")} />
          <Button label="Create a free account" variant="secondary" onClick={() => go("#/signup")} />
        </HStack>
      </Band>
    </>
  );
}

export function PrizePool() {
  const [data, setData] = useState(null);
  const [board, setBoard] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([getBusiness(), api("/api/leaderboard")])
      .then(([biz, b]) => {
        setData(biz);
        setBoard(b);
      })
      .catch(setError);
  }, []);

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the pool…" /></Band>;
  const pool = data.prizePool;

  return (
    <>
      <Band>
        <PageHeader
          eyebrow={`${pool.season} · updated ${formatDate(pool.updated)}`}
          title={`${money(pool.seasonPool)} on the table`}
          description="One season pool, a monthly purse and a weekly Pro cup. Funded by subscriptions and sponsors, published before the first hand."
          actions={[
            <Button key="final" label="How to qualify" variant="primary" onClick={() => go("#/leaderboard")} />,
            <Button key="business" label="Business plan" variant="secondary" onClick={() => go("#/business")} />,
          ]}
        />
        <Grid columns={{ minWidth: 220, max: 4 }} gap={4}>
          <Stat label="Season Final" value={money(pool.seasonPool)} note="Top 32 by season points." tone="accent" />
          <Stat label="Monthly purse" value={money(pool.monthlyPool)} note="First Sunday, every month." />
          <Stat label="Weekly Pro Cup" value={money(pool.weeklyProPool)} note="Sunday, 32-bot heads-up." />
          <Stat label="Paid places" value="8" note="40 / 24 / 16 / 4×4 percent." />
        </Grid>
      </Band>

      <Band variant="muted">
        <Grid columns={{ minWidth: 340, max: 2 }} gap={8}>
          <VStack gap={4}>
            <SectionHeading eyebrow="Payouts" title="Season Final split" />
            <Card padding={6}>
              <VStack gap={4}>
                {pool.payouts.map((row) => (
                  <VStack key={row.place} gap={1}>
                    <HStack hAlign="between">
                      <Text type="body" weight="semibold">{row.place}</Text>
                      <Text type="body" hasTabularNumbers>{money(row.amount)}</Text>
                    </HStack>
                    <ProgressBar label={`${row.place} share`} value={row.share * 100} max={100} isLabelHidden />
                  </VStack>
                ))}
              </VStack>
            </Card>
          </VStack>
          <VStack gap={4}>
            <SectionHeading eyebrow="Monthly" title="Arena Purse split" />
            <Card padding={6}>
              <VStack gap={4}>
                {pool.monthlyPayouts.map((row) => (
                  <VStack key={row.place} gap={1}>
                    <HStack hAlign="between">
                      <Text type="body" weight="semibold">{row.place}</Text>
                      <Text type="body" hasTabularNumbers>{money(row.amount)}</Text>
                    </HStack>
                    <ProgressBar label={`${row.place} share`} value={row.share * 100} max={100} isLabelHidden />
                  </VStack>
                ))}
              </VStack>
            </Card>
          </VStack>
        </Grid>
      </Band>

      <Band>
        <SectionHeading eyebrow="Schedule" title="What is coming" description="Every event is listed with its pool before entries open." />
        <Card padding={0}>
          <Table
            data={pool.schedule.map((row, index) => ({ id: String(index), ...row }))}
            idKey="id"
            density="balanced"
            dividers="rows"
            columns={[
              { key: "event", header: "Event", width: proportional(2, { minWidth: 160 }) },
              { key: "cadence", header: "When" },
              { key: "format", header: "Format" },
              { key: "entry", header: "Entry" },
              { key: "pool", header: "Pool", align: "right", renderCell: (row) => money(row.pool) },
            ]}
          />
        </Card>
      </Band>

      <Band variant="muted">
        <Grid columns={{ minWidth: 320, max: 2 }} gap={8}>
          <VStack gap={3}>
            <SectionHeading eyebrow="Qualification" title="How the final is filled" />
            <VStack gap={2}>
              {pool.qualification.map((line) => (
                <HStack key={line} gap={2} vAlign="start">
                  <Text type="body" color="accent" weight="bold">→</Text>
                  <Text type="body" color="secondary">{line}</Text>
                </HStack>
              ))}
            </VStack>
          </VStack>
          <VStack gap={3}>
            <SectionHeading eyebrow="Funding" title="Where the money comes from" />
            {pool.fundedBy.map((source) => (
              <VStack key={source.source} gap={1}>
                <HStack hAlign="between">
                  <Text type="body" weight="semibold">{source.source}</Text>
                  <Text type="body" hasTabularNumbers>{percent(source.share)}</Text>
                </HStack>
                <ProgressBar label={source.source} value={source.share * 100} max={100} isLabelHidden />
                <Text type="supporting" color="secondary">{source.note}</Text>
              </VStack>
            ))}
          </VStack>
        </Grid>
      </Band>

      <Band>
        <SectionHeading
          eyebrow="Contenders"
          title="Current top eight"
          action={<Button label="Full leaderboard" variant="secondary" onClick={() => go("#/leaderboard")} />}
        />
        <Card padding={0}>
          <Table
            data={(board?.bots || []).slice(0, 8).map((bot) => ({ id: bot.id || bot.name, ...bot }))}
            idKey="id"
            density="compact"
            dividers="rows"
            columns={[
              { key: "place", header: "#", align: "right", width: proportional(0.4) },
              { key: "name", header: "Bot", width: proportional(2, { minWidth: 160 }) },
              { key: "division", header: "Division", renderCell: (row) => <Badge label={row.division} variant="neutral" /> },
              { key: "rating", header: "Rating", align: "right", renderCell: (row) => Math.round(row.rating) },
              { key: "score", header: "bb/100", align: "right", renderCell: (row) => bb(row.score) },
            ]}
          />
        </Card>
        <Text type="supporting" color="secondary">{data.note}</Text>
      </Band>
    </>
  );
}

export function Business() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getBusiness().then(setData).catch(setError);
  }, []);

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the plan…" /></Band>;
  const plan = data.business;
  const pool = data.prizePool;

  return (
    <>
      <Band>
        <PageHeader
          eyebrow={`Business plan · updated ${formatDate(plan.updated)}`}
          title={plan.headline}
          description={plan.summary}
          actions={[
            <Button key="pricing" label="See pricing" variant="primary" onClick={() => go("#/pricing")} />,
            <Button key="pool" label="Prize pool" variant="secondary" onClick={() => go("#/prize-pool")} />,
          ]}
        />
        <Grid columns={{ minWidth: 200, max: 4 }} gap={4}>
          {plan.metrics.map((metric) => (
            <Stat key={metric.label} label={metric.label} value={metric.value} />
          ))}
        </Grid>
      </Band>

      <Band variant="muted">
        <Grid columns={{ minWidth: 320, max: 2 }} gap={8}>
          <VStack gap={3}>
            <SectionHeading eyebrow="Problem" title="Bots die after the demo" />
            <VStack gap={2}>
              {plan.problem.map((line) => <Text key={line} type="body" color="secondary">• {line}</Text>)}
            </VStack>
          </VStack>
          <VStack gap={3}>
            <SectionHeading eyebrow="Product" title="A season, not a benchmark" />
            <VStack gap={2}>
              {plan.product.map((line) => <Text key={line} type="body" color="secondary">• {line}</Text>)}
            </VStack>
          </VStack>
        </Grid>
      </Band>

      <Band>
        <Grid columns={{ minWidth: 320, max: 2 }} gap={8}>
          <VStack gap={3}>
            <SectionHeading eyebrow="Market" title="The agent-builder long tail" />
            <VStack gap={2}>
              {plan.market.map((line) => <Text key={line} type="body" color="secondary">• {line}</Text>)}
            </VStack>
          </VStack>
          <VStack gap={3}>
            <SectionHeading eyebrow="Revenue" title="Three streams" />
            {plan.streams.map((stream) => (
              <Card key={stream.name} padding={4}>
                <VStack gap={1}>
                  <HStack hAlign="between">
                    <Text type="body" weight="semibold">{stream.name}</Text>
                    <Text type="body" hasTabularNumbers>{percent(stream.share)}</Text>
                  </HStack>
                  <ProgressBar label={stream.name} value={stream.share * 100} max={100} isLabelHidden />
                  <Text type="supporting" color="secondary">{stream.note}</Text>
                </VStack>
              </Card>
            ))}
          </VStack>
        </Grid>
      </Band>

      <Band variant="muted">
        <SectionHeading eyebrow="Unit economics" title="Small tickets, high margin" description="Illustrative at 350 Pro and 25 Studio subscriptions in Year 1." />
        <Grid columns={{ minWidth: 180, max: 4 }} gap={4}>
          {plan.unitEconomics.map((row) => (
            <Card key={row.label} padding={4}>
              <VStack gap={1}>
                <Text type="supporting" color="secondary">{row.label}</Text>
                <Heading level={3}>{row.value}</Heading>
              </VStack>
            </Card>
          ))}
        </Grid>
      </Band>

      <Band>
        <SectionHeading eyebrow="Projection" title="Three years, no paid entry fees" description="Subscriptions fund the pool; the pool funds the supply of bots." />
        <Card padding={0}>
          <Table
            data={plan.projections.map((row) => ({ id: row.year, ...row }))}
            idKey="id"
            density="balanced"
            dividers="rows"
            columns={[
              { key: "year", header: "Year" },
              { key: "pro", header: "Pro subs", align: "right", renderCell: (row) => number(row.pro) },
              { key: "studio", header: "Studio subs", align: "right", renderCell: (row) => number(row.studio) },
              { key: "arr", header: "ARR", align: "right", renderCell: (row) => money(row.arr) },
              { key: "pool", header: "Subs ring-fence", align: "right", renderCell: (row) => money(row.pool) },
            ]}
          />
        </Card>
        <Text type="supporting" color="secondary">
          Illustrative model, not a forecast. Prize pool assumes 40% of subscription revenue is ring-fenced.
        </Text>
      </Band>

      <Band variant="muted">
        <Grid columns={{ minWidth: 320, max: 2 }} gap={8}>
          <VStack gap={3}>
            <SectionHeading eyebrow="Use of funds" title="Where the next cheque goes" />
            {plan.useOfFunds.map((row) => (
              <VStack key={row.area} gap={1}>
                <HStack hAlign="between">
                  <Text type="body" weight="semibold">{row.area}</Text>
                  <Text type="body" hasTabularNumbers>{percent(row.share)}</Text>
                </HStack>
                <ProgressBar label={row.area} value={row.share * 100} max={100} isLabelHidden />
                <Text type="supporting" color="secondary">{row.note}</Text>
              </VStack>
            ))}
          </VStack>
          <VStack gap={3}>
            <SectionHeading eyebrow="Roadmap" title="The next four quarters" />
            {plan.roadmap.map((item) => (
              <Card key={item.when} padding={4}>
                <HStack gap={3} vAlign="start">
                  <Badge label={item.when} variant="neutral" />
                  <Text type="body">{item.what}</Text>
                </HStack>
              </Card>
            ))}
          </VStack>
        </Grid>
      </Band>

      <Band>
        <SectionHeading eyebrow="Risks" title="What could go wrong" />
        <Grid columns={{ minWidth: 280, max: 2 }} gap={4}>
          {plan.risks.map((row) => (
            <Card key={row.risk} padding={5}>
              <VStack gap={2}>
                <Heading level={3}>{row.risk}</Heading>
                <Text type="body" color="secondary">{row.mitigation}</Text>
              </VStack>
            </Card>
          ))}
        </Grid>
        <Card variant="muted" padding={6}>
          <VStack gap={2}>
            <Heading level={3}>How the pool is funded</Heading>
            <Text type="body" color="secondary">
              60% subscriptions, 30% sponsors and 10% private leagues. The Season 2 pool is {money(pool.seasonPool)}:
              40% subscription revenue ring-fenced, plus named sponsors. Every payout is published before entries open.
            </Text>
            <Text type="supporting" color="secondary">{data.note}</Text>
          </VStack>
        </Card>
      </Band>
    </>
  );
}

export function Rules() {
  const sections = [
    ["The basics", "Poker Arena is a free, no-stakes competition for developer-built bots. There is no real-money wagering, no entry fee and no prize pool funded by player losses. Ratings and points are a scoreboard."],
    ["One account", "One account per person. Upload your own work. If you fork or adapt someone else's model, say so on the bot page and link the source."],
    ["The model interface", "A bot must return a legal PlayerAction and must not crash on purpose. Faults are recorded, and a bot with repeated faults is removed from the ladder."],
    ["No server-side execution", "Uploaded Python runs in the browser of whoever is testing it. The arena does not execute uploaded code on Cloudflare. Do not upload code that tries to escape the browser sandbox."],
    ["Fair play", "No bot may inspect another bot's hole cards, the deck order, or another user's browser storage. No denial of service and no scraping that degrades the site."],
    ["Rated challenges", "Rated results are self-reported by the browser that ran them. They are accepted in good faith. Deliberately false results are cheating and will be reverted."],
    ["Prizes", "Cash prizes are funded by subscriptions and sponsors, never by entry fees or wagering. Events are skill-based. Void where prohibited."],
    ["Forum conduct", "Be civil. No harassment, spam, malware or doxxing. Threads can be locked or removed. Bug reports are welcome; insults are not."],
    ["Sanctions and appeals", "A bot can be suspended, removed from the ladder or deleted. Appeal by opening a thread in the Forum with the bot name and the reason."],
  ];
  return (
    <>
      <Band>
        <PageHeader
          eyebrow="Platform"
          title="Rules that keep the arena fair"
          description="Free to play, no stakes, no entry fees. These rules apply to the ladder, tournaments, the forum and every uploaded bot."
        />
        <VStack gap={3}>
          {sections.map(([title, body]) => (
            <Card key={title} padding={5}>
              <VStack gap={2}>
                <Heading level={3}>{title}</Heading>
                <Text type="body" color="secondary">{body}</Text>
              </VStack>
            </Card>
          ))}
        </VStack>
      </Band>
      <Band variant="muted">
        <SectionHeading eyebrow="Game rules" title="Every format has a rules page" />
        <HStack gap={2} wrap="wrap">
          <Button label="NLH 6-max" variant="secondary" onClick={() => go("#/games/nlh6")} />
          <Button label="NLH Heads-up" variant="secondary" onClick={() => go("#/games/hu")} />
          <Button label="Big Bad Bots" variant="secondary" onClick={() => go("#/games/boss")} />
          <Button label="2-7 Triple Draw" variant="secondary" onClick={() => go("#/games/draw27")} />
          <Button label="Pineapple OFC" variant="secondary" onClick={() => go("#/games/ofc")} />
        </HStack>
      </Band>
    </>
  );
}

export function About() {
  return (
    <>
      <Band>
        <PageHeader
          eyebrow="About"
          title="A competitive arena for bots people build"
          description="Poker Arena started as a playable version of a university poker-bot competition. It is now a season: a public ladder, five formats, uploaded Python models and a published prize pool."
        />
        <Grid columns={{ minWidth: 320, max: 2 }} gap={8}>
          <VStack gap={3}>
            <Heading level={2}>The engine</Heading>
            <Text type="body" color="secondary">
              The engine is a line-for-line JavaScript port of the competition's Python engine. Duplicate deals and
              swapped seats remove the deck and the button from the comparison. Every result is reported in bb/100
              with a 95% confidence interval.
            </Text>
            <Text type="body" color="secondary">
              The 57 competition models keep their original Python and run in the browser through Pyodide. The five
              house JavaScript bots start instantly. Uploaded models use the same shim and the same table.
            </Text>
          </VStack>
          <VStack gap={3}>
            <Heading level={2}>The platform</Heading>
            <Text type="body" color="secondary">
              Accounts, bot uploads, rated challenges, divisions, tournaments, a forum, a newsletter and a live feed
              are served by a Cloudflare Worker with D1. Uploaded code never runs on the server.
            </Text>
            <Text type="body" color="secondary">
              The business model is simple: subscriptions fund the prize pool, the pool attracts better bots, and
              better bots make the arena worth watching.
            </Text>
            <HStack gap={2} wrap="wrap">
              <Button label="Pricing" variant="primary" onClick={() => go("#/pricing")} />
              <Button label="Business plan" variant="secondary" onClick={() => go("#/business")} />
            </HStack>
          </VStack>
        </Grid>
      </Band>
      <Band variant="muted">
        <SectionHeading eyebrow="Built with" title="The stack" />
        <Grid columns={{ minWidth: 220, max: 4 }} gap={4}>
          <Stat label="Front end" value="React 19" note="Astryx design system, neutral theme." />
          <Stat label="Runtime" value="Cloudflare" note="Worker + D1 + static assets." />
          <Stat label="Bot runtime" value="Pyodide" note="Python in the browser, numpy included." />
          <Stat label="Game engine" value="JavaScript" note="Port of the competition Python engine." />
        </Grid>
      </Band>
    </>
  );
}
