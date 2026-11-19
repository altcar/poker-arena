import React, { useEffect, useState } from "react";
import { Theme } from "@astryxdesign/core/theme";
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
import { Divider } from "@astryxdesign/core/Divider";
import { SectionHeading, ErrorBanner } from "../components/Common.jsx";
import { api, getPlatform, getBusiness, money, number, formatDate } from "../api.js";
import { go } from "../router.js";
import { vividTheme } from "../vivid-theme.js";
import { Band } from "./marketing.jsx";

function GameCard({ game }) {
  return (
    <Card padding={5} elevation="low" className="hover-card">
      <VStack gap={2}>
        <HStack gap={2} vAlign="center" wrap="wrap">
          <Heading level={3}>{game.short}</Heading>
          <Badge label={game.badges?.[0] || "Format"} variant="neutral" />
        </HStack>
        <Text type="body" color="secondary">{game.summary}</Text>
        <HStack gap={2} wrap="wrap">
          <Button label="Play" size="sm" variant="primary" onClick={() => go(`#/play?mode=${game.mode}`)} />
          <Button label="Rules" size="sm" variant="secondary" onClick={() => go(`#/games/${game.slug}`)} />
        </HStack>
      </VStack>
    </Card>
  );
}

export default function Home() {
  const [platform, setPlatform] = useState(null);
  const [board, setBoard] = useState(null);
  const [business, setBusiness] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    Promise.allSettled([getPlatform(), api("/api/leaderboard"), getBusiness()]).then(([p, b, biz]) => {
      if (!live) return;
      if (p.status === "fulfilled") setPlatform(p.value);
      if (b.status === "fulfilled") setBoard(b.value);
      if (biz.status === "fulfilled") setBusiness(biz.value);
      const failed = [p, b, biz].find((result) => result.status === "rejected");
      if (failed) setError(failed.reason);
    });
    return () => { live = false; };
  }, []);

  const topBots = (board?.bots || []).slice(0, 5);
  const games = platform?.games || [];
  const counts = platform?.counts || {};
  const pool = business?.prizePool;
  const plans = business?.plans || [];

  return (
    <Theme theme={vividTheme} mode="system">
      <div className="home-vivid">
        <VStack className="hero-bold" padding={0}>
          <VStack maxWidth={1240} style={{ marginInline: "auto", width: "100%" }} padding={8} gap={8}>
            <Grid columns={{ minWidth: 380, max: 2 }} gap={8} align="center">
              <VStack gap={4}>
                <HStack gap={2} wrap="wrap">
                  <span className="hero-chip">SEASON 2 LIVE</span>
                  <span className="hero-chip light">NO WAGERING</span>
                  <span className="hero-chip light">10,000 PAPER CREDITS</span>
                </HStack>
                <Heading level={1} type="display-1" color="inherit" className="hero-title">
                  Build the bot.<br />Take the pool.
                </Heading>
                <Text type="large" color="inherit" as="p" style={{ maxWidth: "56ch", opacity: 0.86 }}>
                  The competitive arena for developer-built poker bots. Upload Python, play five formats,
                  climb a real ladder and enter a published prize pool.
                </Text>
                <HStack gap={2} wrap="wrap">
                  <Button label="Start free →" variant="primary" size="lg" onClick={() => go("#/signup")} />
                  <Button label="Play the demo" variant="secondary" size="lg" onClick={() => go("#/play")} />
                  <Button label="See the pool" variant="ghost" size="lg" onClick={() => go("#/prize-pool")} />
                </HStack>
                <HStack gap={4} wrap="wrap">
                  <Text type="supporting" color="inherit">No card to start</Text>
                  <Text type="supporting" color="inherit">10,000 paper credits on signup</Text>
                  <Text type="supporting" color="inherit">Python runs in your browser</Text>
                </HStack>
              </VStack>

              <VStack className="pool-card" padding={6} gap={4}>
                <HStack hAlign="between" vAlign="center">
                  <span className="pool-eyebrow">SEASON 2 POOL</span>
                  <span className="live-pill">● LIVE</span>
                </HStack>
                <Heading level={2} color="inherit" className="pool-amount">{money(pool?.seasonPool || 25000)}</Heading>
                <Text type="body" color="inherit" style={{ opacity: 0.7 }}>
                  {money(pool?.monthlyPool || 2500)} every month · {money(pool?.weeklyProPool || 500)} every Sunday
                </Text>
                <Divider />
                <VStack gap={2}>
                  {topBots.length === 0 ? (
                    <Text type="body" color="inherit" style={{ opacity: 0.65 }}>Loading the ladder…</Text>
                  ) : topBots.slice(0, 3).map((bot) => (
                    <HStack key={bot.name} gap={3} hAlign="between" vAlign="center">
                      <HStack gap={2} vAlign="center">
                        <span className="rank-chip">{bot.place}</span>
                        <Text type="body" color="inherit" weight="semibold">{bot.name}</Text>
                      </HStack>
                      <Text type="body" color="inherit" hasTabularNumbers>{Math.round(bot.rating)}</Text>
                    </HStack>
                  ))}
                </VStack>
                <Button label="Full leaderboard" variant="secondary" size="sm" onClick={() => go("#/leaderboard")} />
              </VStack>
            </Grid>
          </VStack>
        </VStack>

        <div className="ticker" aria-hidden="true">
          <div className="ticker-track">
            {[0, 1].map((copy) => (
              <span key={copy} className="ticker-row">
                {counts.houseBots || 62} BOTS <i>✳</i> {counts.matches ? number(counts.matches) : "1,891"} MATCHES <i>✳</i> $25,000 POOL <i>✳</i> 5 FORMATS <i>✳</i> 10,000 PAPER CREDITS <i>✳</i>
              </span>
            ))}
          </div>
        </div>

        {error ? <Band><ErrorBanner error={error} /></Band> : null}

        <Band>
          <VStack gap={4}>
            <Heading level={2} type="display-3">A serious game for people who ship.</Heading>
            <Grid columns={{ minWidth: 230, max: 4 }} gap={4}>
              <VStack className="stat-tile lime" padding={5} gap={1}>
                <Text type="label" color="inherit">HOUSE BOTS</Text>
                <Heading level={3} color="inherit">{counts.houseBots || 62}</Heading>
                <Text type="supporting" color="inherit">Five JavaScript bots and 57 original competition models.</Text>
              </VStack>
              <VStack className="stat-tile blue" padding={5} gap={1}>
                <Text type="label" color="inherit">MATCHES</Text>
                <Heading level={3} color="inherit">{counts.matches ? number(counts.matches) : "1,891"}</Heading>
                <Text type="supporting" color="inherit">Duplicate deals, swapped seats, bb/100 with intervals.</Text>
              </VStack>
              <VStack className="stat-tile coral" padding={5} gap={1}>
                <Text type="label" color="inherit">FORMATS</Text>
                <Heading level={3} color="inherit">{games.length || 5}</Heading>
                <Text type="supporting" color="inherit">NLH 6-max, heads-up, bosses, 2-7 and OFC.</Text>
              </VStack>
              <VStack className="stat-tile cream" padding={5} gap={1}>
                <Text type="label" color="inherit">SIGNUP BONUS</Text>
                <Heading level={3} color="inherit">10,000</Heading>
                <Text type="supporting" color="inherit">Paper credits to test a bot before you pay.</Text>
              </VStack>
            </Grid>
          </VStack>
        </Band>

        <VStack className="band-blue" padding={8}>
          <VStack maxWidth={1200} style={{ marginInline: "auto", width: "100%" }} gap={6}>
            <HStack hAlign="between" vAlign="end" wrap="wrap">
              <VStack gap={2}>
                <Text type="label" color="inherit">HOW IT WORKS</Text>
                <Heading level={2} type="display-2" color="inherit">From model.py to the final table.</Heading>
              </VStack>
              <Button label="Read the docs" variant="secondary" onClick={() => go("#/stack/your-first-bot")} />
            </HStack>
            <Grid columns={{ minWidth: 280, max: 3 }} gap={4}>
              {[
                { n: "01", t: "Upload", d: "One Model class, up to six modules and an optional pickle. Python runs in the browser through Pyodide." },
                { n: "02", t: "Challenge", d: "Twenty hands against a house table. Wins add paper credits and move your Elo rating." },
                { n: "03", t: "Compete", d: "Enter the season, qualify for the final and play for a published pool." },
              ].map((step) => (
                <VStack key={step.n} className="step-card" padding={5} gap={2}>
                  <span className="step-number">{step.n}</span>
                  <Heading level={3} color="inherit">{step.t}</Heading>
                  <Text type="body" color="inherit" style={{ opacity: 0.82 }}>{step.d}</Text>
                </VStack>
              ))}
            </Grid>
          </VStack>
        </VStack>

        <Band>
          <SectionHeading
            eyebrow="Games"
            title="Five formats. One ladder."
            description="Hold'em is the main event. The rest exist because a good bot should survive a change of rules."
            action={<Button label="All games" variant="secondary" onClick={() => go("#/games")} />}
          />
          <Grid columns={{ minWidth: 300, max: 3 }} gap={4}>
            {games.map((game) => <GameCard key={game.slug} game={game} />)}
          </Grid>
        </Band>

        <VStack className="band-ink" padding={8}>
          <VStack maxWidth={1200} style={{ marginInline: "auto", width: "100%" }} gap={6}>
            <Grid columns={{ minWidth: 340, max: 2 }} gap={8} align="center">
              <VStack gap={3}>
                <Text type="label" color="inherit">THE POOL</Text>
                <Heading level={2} type="display-1" color="inherit" className="pool-amount">{money(pool?.seasonPool || 25000)}</Heading>
                <Text type="large" color="inherit" style={{ opacity: 0.78 }}>
                  One published Season Final pool, a monthly purse and a weekly Pro Cup.
                  Funded by 40% of subscription revenue and named sponsors.
                </Text>
                <HStack gap={2} wrap="wrap">
                  <Button label="Prize pool details" variant="primary" onClick={() => go("#/prize-pool")} />
                  <Button label="Business plan" variant="secondary" onClick={() => go("#/business")} />
                </HStack>
                <Text type="supporting" color="inherit" style={{ opacity: 0.6 }}>
                  No wagering. No paid entry fees. Skill-based competition only.
                </Text>
              </VStack>
              <VStack gap={4}>
                {(pool?.payouts || []).slice(0, 5).map((row) => (
                  <VStack key={row.place} gap={1}>
                    <HStack hAlign="between">
                      <Text type="body" color="inherit" weight="semibold">{row.place}</Text>
                      <Text type="body" color="inherit" hasTabularNumbers>{money(row.amount)}</Text>
                    </HStack>
                    <div className="pool-bar"><div style={{ width: `${row.share * 100}%` }} /></div>
                  </VStack>
                ))}
              </VStack>
            </Grid>
          </VStack>
        </VStack>

        <VStack className="band-lime" padding={8}>
          <VStack maxWidth={1200} style={{ marginInline: "auto", width: "100%" }} gap={6}>
            <HStack hAlign="between" vAlign="end" wrap="wrap">
              <VStack gap={2}>
                <Text type="label" color="inherit">PRICING</Text>
                <Heading level={2} type="display-2" color="inherit">Free to start. Pro when it counts.</Heading>
                <Text type="body" color="inherit" style={{ opacity: 0.75 }}>The ladder is free. Pro buys volume and paid events. Studio is for teams.</Text>
              </VStack>
              <Button label="Compare plans" variant="primary" onClick={() => go("#/pricing")} />
            </HStack>
            <Grid columns={{ minWidth: 260, max: 3 }} gap={4}>
              {plans.map((plan) => (
                <VStack key={plan.slug} className="plan-tile" padding={5} gap={3}>
                  <HStack hAlign="between" vAlign="center">
                    <Heading level={3} color="inherit">{plan.name}</Heading>
                    {plan.highlight ? <span className="hero-chip dark">MOST POPULAR</span> : null}
                  </HStack>
                  <Heading level={2} color="inherit">{money(plan.monthly)}<span className="price-period">{plan.monthly ? "/mo" : ""}</span></Heading>
                  <Text type="body" color="inherit" style={{ opacity: 0.8 }}>{plan.tagline}</Text>
                  <Button
                    label={plan.cta}
                    variant={plan.highlight ? "primary" : "secondary"}
                    onClick={() => go(plan.slug === "open" ? "#/signup" : "#/pricing")}
                  />
                </VStack>
              ))}
            </Grid>
          </VStack>
        </VStack>

        <Band variant="muted">
          <SectionHeading eyebrow="The Stack" title="Notes from the arena" action={<Button label="Read more" variant="secondary" onClick={() => go("#/stack")} />} />
          <Grid columns={{ minWidth: 260, max: 3 }} gap={4}>
            {(platform?.stack || []).slice(0, 3).map((article) => (
              <Card key={article.slug} padding={5} elevation="low" className="hover-card">
                <VStack gap={2}>
                  <HStack gap={2} wrap="wrap">
                    <Badge label={article.tag} variant="neutral" />
                    <Text type="supporting" color="secondary">{formatDate(article.date)}</Text>
                  </HStack>
                  <Heading level={3}>{article.title}</Heading>
                  <Text type="body" color="secondary">{article.excerpt}</Text>
                  <Link href={`#/stack/${article.slug}`} isStandalone>Read the note</Link>
                </VStack>
              </Card>
            ))}
          </Grid>
        </Band>

        <Band>
          <VStack className="newsletter-cta" padding={8} gap={3}>
            <Heading level={2} type="display-2" color="inherit">One email when something actually happens.</Heading>
            <Text type="large" color="inherit" style={{ opacity: 0.78 }}>
              New seasons, tournament dates and format launches. No drip campaign.
            </Text>
            <HStack gap={2} wrap="wrap">
              <Button label="Join the newsletter" variant="primary" onClick={() => go("#/newsletter")} />
              <Button label="Create a free account" variant="secondary" onClick={() => go("#/signup")} />
            </HStack>
          </VStack>
        </Band>
      </div>
    </Theme>
  );
}
