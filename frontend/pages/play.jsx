import React, { useEffect, useMemo, useState } from "react";
import { VStack } from "@astryxdesign/core/VStack";
import { HStack } from "@astryxdesign/core/HStack";
import { Grid } from "@astryxdesign/core/Grid";
import { Card } from "@astryxdesign/core/Card";
import { Heading } from "@astryxdesign/core/Heading";
import { Text } from "@astryxdesign/core/Text";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Link } from "@astryxdesign/core/Link";
import { CheckboxInput } from "@astryxdesign/core/CheckboxInput";
import { MultiSelector } from "@astryxdesign/core/MultiSelector";
import { Selector } from "@astryxdesign/core/Selector";
import { usePlay, BOSSES, MAX_SELECTED } from "../play/context.jsx";
import PokerTable from "../play/Table.jsx";
import { Draw27, Ofc } from "../play/games.jsx";
import { PageHeader, ErrorBanner, Loading, SectionHeading } from "../components/Common.jsx";
import { api, getPlatform } from "../api.js";
import { go } from "../router.js";
import { Band } from "./marketing.jsx";

function ModeBar({ active }) {
  const { newSession } = usePlay();
  const modes = [
    ["nlh6", "NLH 6-max", "#/play"],
    ["hu", "Heads-up", "#/play?mode=hu"],
    ["boss", "Big Bad Bots", "#/play?mode=boss"],
    ["draw27", "2-7 Triple Draw", "#/play?mode=draw27"],
    ["ofc", "Pineapple OFC", "#/play?mode=ofc"],
  ];
  return (
    <HStack gap={1} wrap="wrap" paddingBlockEnd={3}>
      {modes.map(([key, label, href]) => (
        <Button
          key={key}
          label={label}
          size="sm"
          variant={active === key ? "primary" : "ghost"}
          onClick={() => {
            if (key !== "draw27" && key !== "ofc") newSession();
            go(href);
          }}
        />
      ))}
      <Button label="All games" size="sm" variant="ghost" onClick={() => go("#/games")} />
    </HStack>
  );
}

function useOpponents() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([getPlatform(), api("/api/tournament"), api("/api/bots").catch(() => ({ bots: [] }))])
      .then(([platform, tournament, bots]) => {
        const options = [
          { value: "conservative", label: "Conservative (JavaScript)" },
          { value: "workhorse", label: "Workhorse (JavaScript)" },
          { value: "adaptive", label: "Adaptive (JavaScript)" },
          { value: "mixed", label: "Mixed (JavaScript)" },
          { value: "aggressive", label: "Aggressive (JavaScript)" },
        ];
        const playable = new Set(tournament.availableTeams || []);
        const teamOptions = (tournament.allTeams?.teams || [])
          .filter((team) => playable.has(team.team))
          .map((team) => ({ value: team.team, label: team.team }))
          .sort((a, b) => a.label.localeCompare(b.label));
        const userOptions = (bots.bots || []).map((bot) => ({
          value: `user:${bot.id}`,
          label: `${bot.name} (uploaded by ${bot.owner || "?"})`,
        }));
        setData([...options, ...teamOptions, ...userOptions]);
        void platform;
      })
      .catch(setError);
  }, []);

  return { options: data, error };
}

function NlhSetup() {
  const { selectedBots: selected, chooseBots, humanPlaying, setHumanPlaying, startSession, loading, error } = usePlay();
  const { options, error: optionsError } = useOpponents();
  const selectedCount = selected.length;

  return (
    <VStack gap={5}>
      <PageHeader
        eyebrow="Play"
        title="Sit at a six-max table"
        description="Pick up to six opponents. The five JavaScript bots start instantly; competition and uploaded bots load their Python in your browser."
        actions={[
          <Button key="teams" label="Browse teams" variant="secondary" onClick={() => go("#/teams")} />,
          <Button key="upload" label="Upload a bot" variant="ghost" onClick={() => go("#/upload")} />,
        ]}
      />
      {loading ? <Loading label={loading} /> : null}
      {error ? <ErrorBanner error={error} /> : null}
      {optionsError ? <ErrorBanner error={optionsError} /> : null}
      <Card padding={6}>
        <VStack gap={4}>
          {options ? (
            <MultiSelector
              label="Opponents"
              options={options}
              value={selected}
              onChange={(values) => chooseBots(values.slice(0, MAX_SELECTED))}
              hasSearch
              hasSelectAll={false}
              triggerDisplay="badges"
              maxBadges={6}
              description={`${selectedCount} of ${MAX_SELECTED} selected.`}
            />
          ) : (
            <Loading label="Loading opponents…" />
          )}
          <CheckboxInput
            label="I'm playing"
            value={humanPlaying}
            onChange={setHumanPlaying}
            description="Turn this off to watch the bots play each other."
          />
          <HStack gap={2} wrap="wrap">
            <Button
              label={humanPlaying ? "Start the table" : "Watch the table"}
              variant="primary"
              onClick={() => startSession(humanPlaying ? "You" : "Spectator", selected, humanPlaying)}
              isDisabled={selected.length === 0}
            />
            <Button label="Reset to the classic five" variant="secondary" onClick={() => chooseBots(["conservative", "workhorse", "adaptive", "mixed", "aggressive"])} />
          </HStack>
          <Text type="supporting" color="secondary">
            The table stays in this tab. Python models load once per session and then play instantly.
          </Text>
        </VStack>
      </Card>
    </VStack>
  );
}

function HuSetup() {
  const { startChallenge, loading, error } = usePlay();
  const { options, error: optionsError } = useOpponents();
  const [opponent, setOpponent] = useState("adaptive");

  const choices = options || [];
  const random = () => {
    if (!choices.length) return;
    const pick = choices[Math.floor(Math.random() * choices.length)];
    setOpponent(pick.value);
  };

  return (
    <VStack gap={5}>
      <PageHeader
        eyebrow="Play"
        title="Heads-up Hold'em"
        description="One opponent. The button posts the small blind and acts first before the flop, then last after it."
        actions={[<Button key="rules" label="Rules" variant="secondary" onClick={() => go("#/games/hu")} />]}
      />
      {loading ? <Loading label={loading} /> : null}
      {error ? <ErrorBanner error={error} /> : null}
      {optionsError ? <ErrorBanner error={optionsError} /> : null}
      <Card padding={6}>
        <VStack gap={4}>
          {choices.length ? (
            <>
              <Selector label="Opponent" options={choices} value={opponent} onChange={setOpponent} hasSearch />
              <HStack gap={2} wrap="wrap">
                <Button
                  label="Sit down"
                  variant="primary"
                  onClick={() => {
                    if (opponent.startsWith("user:")) startChallenge({ botId: opponent.slice(5) });
                    else startChallenge({ opponent });
                  }}
                />
                <Button label="Random opponent" variant="secondary" onClick={random} />
              </HStack>
            </>
          ) : (
            <Loading label="Loading opponents…" />
          )}
        </VStack>
      </Card>
    </VStack>
  );
}

function BossSetup() {
  const { startBossRun } = usePlay();
  return (
    <VStack gap={5}>
      <PageHeader
        eyebrow="Play"
        title="Big Bad Bots"
        description="A three-boss gauntlet. You start each round with 1,000 chips; the bosses start with more. Blinds rise. No rebuys."
        actions={[<Button key="rules" label="Full rules" variant="secondary" onClick={() => go("#/games/boss")} />]}
      />
      <Grid columns={{ minWidth: 280, max: 3 }} gap={4}>
        {BOSSES.map((boss, index) => (
          <Card key={boss.name} padding={5} elevation={index === 0 ? "low" : "none"}>
            <VStack gap={2}>
              <Badge label={`Round ${index + 1}`} variant="purple" />
              <Heading level={2}>{boss.name}</Heading>
              <Text type="body" color="secondary">{boss.style} · {boss.stack.toLocaleString("en-US")} chips</Text>
              <Text type="supporting" color="secondary">Blinds {boss.smallBlind}/{boss.bigBlind}</Text>
            </VStack>
          </Card>
        ))}
      </Grid>
      <HStack gap={2} wrap="wrap">
        <Button label="Start the run" variant="primary" size="lg" onClick={() => startBossRun(0)} />
        <Text type="supporting" color="secondary">Take the boss's whole stack to advance. Lose yours and the run restarts.</Text>
      </HStack>
    </VStack>
  );
}

function Challenge({ opponent, botId }) {
  const { startChallenge, loading, error, gameState } = usePlay();
  useEffect(() => {
    startChallenge({ opponent, botId });
  }, [opponent, botId, startChallenge]);

  if (loading) return <Loading label={loading} />;
  if (error) return <ErrorBanner error={error} />;
  if (gameState) return <PokerTable />;
  return <Loading label="Preparing the table…" />;
}

export default function Play({ route }) {
  const { gameState, mode, loading, error, bossRun } = usePlay();
  const query = route.query;
  const requested = query.mode || (query.opponent || query.bot ? "hu" : "nlh6");
  const active = gameState ? mode : null;

  if (requested === "draw27") {
    return (
      <Band>
        <PageHeader eyebrow="Play" title="2-7 Triple Draw" description="Fixed limit · heads-up · lowest hand wins · three draws" actions={[<Button key="rules" label="Rules" variant="secondary" onClick={() => go("#/games/draw27")} />]} />
        <ModeBar active="draw27" />
        <Draw27 />
      </Band>
    );
  }

  if (requested === "ofc") {
    return (
      <Band>
        <PageHeader eyebrow="Play" title="Pineapple OFC" description="No betting · build three rows · bottom ≥ middle ≥ top" actions={[<Button key="rules" label="Rules" variant="secondary" onClick={() => go("#/games/ofc")} />]} />
        <ModeBar active="ofc" />
        <Ofc />
      </Band>
    );
  }

  if (requested === "boss") {
    return (
      <Band>
        <PageHeader eyebrow="Play" title={bossRun ? "Big Bad Bots" : "Big Bad Bots"} description={bossRun ? "Take the boss's stack to advance. No rebuys." : "A three-boss gauntlet with rising blinds and bigger stacks."} />
        <ModeBar active="boss" />
        {bossRun && gameState ? <PokerTable /> : <BossSetup />}
      </Band>
    );
  }

  if (requested === "hu") {
    const challenge = query.opponent || query.bot;
    return (
      <Band>
        <PageHeader eyebrow="Play" title="Heads-up Hold'em" description="One opponent, one table, no place to hide." />
        <ModeBar active="hu" />
        {challenge ? (
          <Challenge opponent={query.opponent} botId={query.bot} />
        ) : active === "hu" && gameState ? (
          <PokerTable />
        ) : (
          <HuSetup />
        )}
      </Band>
    );
  }

  return (
    <Band>
      <PageHeader eyebrow="Play" title="The table" description="Six seats, no-limit Hold'em. Pick your opponents or watch the bots fight it out." />
      <ModeBar active={active || "nlh6"} />
      {active && gameState ? <PokerTable /> : <NlhSetup />}
    </Band>
  );
}
