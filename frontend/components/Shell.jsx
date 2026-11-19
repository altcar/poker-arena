import React from "react";
import { AppShell } from "@astryxdesign/core/AppShell";
import { TopNav, TopNavHeading, TopNavItem, TopNavMenu } from "@astryxdesign/core/TopNav";
import { HStack } from "@astryxdesign/core/HStack";
import { VStack } from "@astryxdesign/core/VStack";
import { Grid } from "@astryxdesign/core/Grid";
import { Section } from "@astryxdesign/core/Section";
import { Text } from "@astryxdesign/core/Text";
import { Heading } from "@astryxdesign/core/Heading";
import { Link } from "@astryxdesign/core/Link";
import { Button } from "@astryxdesign/core/Button";
import { Divider } from "@astryxdesign/core/Divider";
import { useAuth } from "../auth.jsx";
import { go } from "../router.js";
import { money } from "../api.js";

const moreItems = [
  { title: "Games and rules", description: "Five formats, from 6-max to Pineapple OFC.", href: "#/games" },
  { title: "Prize pool", description: "Season 2 purse, payouts and qualification.", href: "#/prize-pool" },
  { title: "Business plan", description: "How the arena makes money and funds prizes.", href: "#/business" },
  { title: "Live matches", description: "A rolling feed of tables in progress.", href: "#/live" },
  { title: "Forum", description: "Development, tournaments and bug reports.", href: "#/forum" },
  { title: "The Stack", description: "Notes on formats, ratings and bot building.", href: "#/stack" },
  { title: "Newsletter", description: "One email when something actually happens.", href: "#/newsletter" },
  { title: "Platform rules", description: "Fair play, conduct and appeals.", href: "#/rules" },
  { title: "Teams", description: "All 68 competition teams and their models.", href: "#/teams" },
  { title: "Head-to-head", description: "The full 62×62 matrix.", href: "#/matrix" },
  { title: "Matches", description: "Every round-robin result.", href: "#/matches" },
  { title: "About", description: "What this is and how it is built.", href: "#/about" },
];

function Footer() {
  const year = new Date().getFullYear();
  return (
    <Section variant="muted" padding={8} dividers={["top"]}>
      <VStack gap={6}>
        <Grid columns={{ minWidth: 220, max: 4 }} gap={6}>
          <VStack gap={2}>
            <Heading level={3}>Poker Arena</Heading>
            <Text type="body" color="secondary">
              A competitive arena for developer-built bots. Upload a model, climb the ladder, play for the pool.
            </Text>
            <Text type="supporting" color="secondary">
              Season 2 pool {money(25000)} · free to play
            </Text>
          </VStack>
          <VStack gap={2}>
            <Text type="label" weight="semibold">Product</Text>
            <Link href="#/play" isStandalone>Play</Link>
            <Link href="#/games" isStandalone>Games</Link>
            <Link href="#/leaderboard" isStandalone>Leaderboard</Link>
            <Link href="#/tournaments" isStandalone>Tournaments</Link>
            <Link href="#/prize-pool" isStandalone>Prize pool</Link>
          </VStack>
          <VStack gap={2}>
            <Text type="label" weight="semibold">Community</Text>
            <Link href="#/forum" isStandalone>Forum</Link>
            <Link href="#/stack" isStandalone>The Stack</Link>
            <Link href="#/live" isStandalone>Live matches</Link>
            <Link href="#/newsletter" isStandalone>Newsletter</Link>
            <Link href="#/teams" isStandalone>Teams</Link>
          </VStack>
          <VStack gap={2}>
            <Text type="label" weight="semibold">Company</Text>
            <Link href="#/pricing" isStandalone>Pricing</Link>
            <Link href="#/business" isStandalone>Business plan</Link>
            <Link href="#/rules" isStandalone>Rules</Link>
            <Link href="#/about" isStandalone>About</Link>
            <Link href="#/account" isStandalone>Account</Link>
          </VStack>
        </Grid>
        <Divider />
        <HStack gap={4} wrap="wrap" hAlign="between" vAlign="center">
          <Text type="supporting" color="secondary">
            © {year} Poker Arena. No wagering. No paid entry fees. Skill-based competition only.
          </Text>
          <HStack gap={2}>
            <Link href="#/rules" isStandalone type="supporting">Rules</Link>
            <Link href="#/newsletter" isStandalone type="supporting">Newsletter</Link>
          </HStack>
        </HStack>
      </VStack>
    </Section>
  );
}

export default function Shell({ route, children }) {
  const { me, bots } = useAuth();
  const key = route.key;

  const item = (label, href, keyName) => (
    <TopNavItem key={href} label={label} href={href} isSelected={key === keyName} />
  );

  return (
    <AppShell
      height="auto"
      variant="wash"
      contentPadding={0}
      topNav={
        <TopNav
          label="Primary"
          heading={
            <TopNavHeading
              logo={<span className="brand-mark" aria-hidden="true">♠</span>}
              heading="Poker Arena"
              headingHref="#/"
            />
          }
          startContent={
            <HStack gap={0.5} wrap="wrap">
              {item("Home", "#/", "home")}
              {item("Play", "#/play", "play")}
              {item("Pricing", "#/pricing", "pricing")}
              {item("Leaderboard", "#/leaderboard", "leaderboard")}
              {item("Tournaments", "#/tournaments", "tournaments")}
              <TopNavMenu label="More" items={moreItems} />
            </HStack>
          }
          endContent={
            <HStack gap={2} vAlign="center">
              {me ? (
                <TopNavItem label={`@${me.handle}`} href="#/account" isSelected={key === "account"} />
              ) : (
                <TopNavItem label="Log in" href="#/login" isSelected={key === "login"} />
              )}
              <Button
                label={me ? "Upload a bot" : "Start free"}
                variant="primary"
                size="sm"
                onClick={() => go(me ? "#/upload" : "#/pricing")}
              />
            </HStack>
          }
        />
      }
    >
      <VStack gap={0}>
        {children}
        <Footer />
      </VStack>
    </AppShell>
  );
}
