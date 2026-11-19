import React, { useRef, useState } from "react";
import { VStack } from "@astryxdesign/core/VStack";
import { HStack } from "@astryxdesign/core/HStack";
import { Grid } from "@astryxdesign/core/Grid";
import { Card } from "@astryxdesign/core/Card";
import { Heading } from "@astryxdesign/core/Heading";
import { Text } from "@astryxdesign/core/Text";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Divider } from "@astryxdesign/core/Divider";
import { Draw27Game, describe27 } from "../../src/draw27.js";
import { OfcGame, handName, rank3, rank5 } from "../../src/ofc.js";
import { PlayingCard } from "./Table.jsx";

const STREETS = ["Predraw", "After draw 1", "After draw 2", "After draw 3"];

function newDrawGame() {
  return new Draw27Game({
    humanName: localStorage.getItem("arenaName") || "You",
    botName: "Deuce",
    stack: 500,
    smallBlind: 10,
    bigBlind: 20,
    seed: Date.now() % 1000000,
  });
}

export function Draw27() {
  const game = useRef(null);
  const [state, setState] = useState(null);
  const [selected, setSelected] = useState([]);
  const [version, setVersion] = useState(0);

  if (!game.current) {
    game.current = newDrawGame();
    game.current.startHand();
  }

  const current = state || game.current.publicState();
  const hand = current.hand;

  const refresh = (nextState) => {
    setState(nextState);
    setVersion((v) => v + 1);
  };

  if (!hand) return null;

  const toggle = (index) => {
    setSelected((currentSelection) => currentSelection.includes(index)
      ? currentSelection.filter((item) => item !== index)
      : [...currentSelection, index]);
  };

  const act = (kind) => {
    refresh(game.current.humanAction(kind));
    setSelected([]);
  };

  const draw = () => {
    refresh(game.current.humanDraw(selected));
    setSelected([]);
  };

  const nextHand = () => {
    if (game.current.stacks[0] === 0 || game.current.stacks[1] === 0) {
      game.current = newDrawGame();
      game.current.startHand();
      refresh(game.current.publicState());
      return;
    }
    refresh(game.current.startHand());
  };

  const reset = () => {
    game.current = newDrawGame();
    game.current.startHand();
    refresh(game.current.publicState());
  };

  const you = hand.humanCards;

  return (
    <VStack gap={4}>
      <Card variant="muted" padding={5} className="draw-table">
        <VStack gap={4}>
          <HStack hAlign="between" vAlign="center" wrap="wrap">
            <HStack gap={2} vAlign="center">
              <Heading level={3}>{current.names[1]}</Heading>
              <Badge label={`Stack ${current.stacks[1]}`} variant="neutral" />
              {hand.bets[1] ? <Badge label={`Bet ${hand.bets[1]}`} variant="warning" /> : null}
            </HStack>
            <Text type="supporting" color="secondary">
              {hand.drawCounts[1]} drawn
            </Text>
          </HStack>
          <HStack gap={2} wrap="wrap">
            {hand.botCards.map((card, index) => <PlayingCard key={index} card={card} hidden={!hand.result && !card} />)}
          </HStack>
          <Divider />
          <HStack hAlign="center" gap={4} wrap="wrap">
            <div className="pot-pill">Pot {hand.pot}</div>
            <Text type="body" color="secondary">{STREETS[hand.street] || "Showdown"} · bet {hand.betSize}</Text>
          </HStack>
          <Divider />
          <HStack hAlign="between" vAlign="center" wrap="wrap">
            <HStack gap={2} vAlign="center">
              <Heading level={3}>{current.names[0]}</Heading>
              <Badge label={`Stack ${current.stacks[0]}`} variant="neutral" />
              {hand.bets[0] ? <Badge label={`Bet ${hand.bets[0]}`} variant="warning" /> : null}
            </HStack>
            <Text type="supporting" color="secondary">{describe27(you)}</Text>
          </HStack>
          <HStack gap={2} wrap="wrap">
            {you.map((card, index) => (
              <PlayingCard
                key={`${card}-${index}-${version}`}
                card={card}
                selected={selected.includes(index)}
                onClick={current.waitingFor === "draw" ? () => toggle(index) : undefined}
              />
            ))}
          </HStack>
        </VStack>
      </Card>

      {hand.result ? (
        <Card padding={5}>
          <VStack gap={2}>
            <Heading level={2}>
              {hand.result.winner === -1 ? "Split pot" : hand.result.winner === 0 ? "You win the pot" : `${current.names[1]} wins the pot`}
            </Heading>
            <Text type="body" color="secondary">
              {current.names[0]}: {hand.result.names[0]} · {current.names[1]}: {hand.result.names[1]}
            </Text>
            <Text type="body">
              Pot {hand.result.pot} · net {hand.result.net[0] >= 0 ? "+" : ""}{hand.result.net[0]} for you.
            </Text>
            <HStack gap={2} wrap="wrap">
              <Button label="Deal next hand" variant="primary" onClick={nextHand} />
              <Button label="New match" variant="secondary" onClick={reset} />
            </HStack>
          </VStack>
        </Card>
      ) : current.waitingFor === "human" ? (
        <Card padding={5}>
          <HStack gap={2} wrap="wrap">
            <Button label="Fold" variant="destructive" onClick={() => act("fold")} />
            <Button label={hand.toCall ? `Call ${hand.toCall}` : "Check"} variant="primary" onClick={() => act("call")} />
            {hand.canRaise ? <Button label={`Raise to ${hand.bets[0] + hand.toCall + hand.betSize}`} variant="secondary" onClick={() => act("raise")} /> : null}
          </HStack>
        </Card>
      ) : current.waitingFor === "draw" ? (
        <Card padding={5}>
          <VStack gap={2}>
            <HStack gap={2} wrap="wrap">
              <Button label={selected.length ? `Draw ${selected.length}` : "Stand pat"} variant="primary" onClick={draw} />
              <Button label="Clear selection" variant="secondary" onClick={() => setSelected([])} />
            </HStack>
            <Text type="supporting" color="secondary">
              Click cards to discard them. Your opponent sees how many, not which.
            </Text>
          </VStack>
        </Card>
      ) : (
        <Card variant="muted" padding={4}><Text type="body" color="secondary">Waiting for the bot…</Text></Card>
      )}

      {hand.log?.length ? (
        <Card padding={4}>
          <details>
            <summary><Text type="body" weight="semibold">Hand log ({hand.log.length})</Text></summary>
            <VStack gap={1} paddingBlockStart={2}>
              {hand.log.slice(-30).map((line, index) => <Text key={index} type="supporting" color="secondary">{line}</Text>)}
            </VStack>
          </details>
        </Card>
      ) : null}
    </VStack>
  );
}

const ROWS = [
  ["top", "Top", 3],
  ["middle", "Middle", 5],
  ["bottom", "Bottom", 5],
];

function newOfcGame() {
  return new OfcGame({
    humanName: localStorage.getItem("arenaName") || "You",
    botName: "Pineapple",
    seed: Date.now() % 1000000,
  });
}

function OfcBoard({ board, interactive, onSlot }) {
  return (
    <Card padding={5}>
      <VStack gap={2}>
        {ROWS.map(([row, label, cap]) => {
          const cards = board[row];
          const ranks = row === "top" ? rank3(cards) : rank5(cards);
          return (
            <VStack key={row} gap={1}>
              <HStack gap={2} vAlign="center" wrap="wrap">
                <Text type="supporting" color="secondary" style={{ width: "4rem" }}>{label}</Text>
                <HStack gap={1} wrap="wrap">
                  {cards.map((card, index) => <PlayingCard key={`${card}-${index}`} card={card} small />)}
                  {Array.from({ length: Math.max(0, cap - cards.length) }, (_, index) => (
                    <span
                      key={`slot-${index}`}
                      className={`ofc-slot${interactive ? " interactive" : ""}`}
                      onClick={interactive ? () => onSlot(row) : undefined}
                      role={interactive ? "button" : undefined}
                      tabIndex={interactive ? 0 : undefined}
                      onKeyDown={interactive ? (event) => { if (event.key === "Enter" || event.key === " ") onSlot(row); } : undefined}
                    >
                      +
                    </span>
                  ))}
                </HStack>
              </HStack>
              {ranks ? <Text type="supporting" color="secondary" style={{ paddingInlineStart: "4.75rem" }}>{handName(ranks, row)}</Text> : null}
            </VStack>
          );
        })}
      </VStack>
    </Card>
  );
}

export function Ofc() {
  const game = useRef(null);
  const [state, setState] = useState(null);
  const [selected, setSelected] = useState(null);

  if (!game.current) {
    game.current = newOfcGame();
    game.current.startHand();
  }

  const current = state || game.current.publicState();
  const hand = current.hand;
  if (!hand) return null;

  const refresh = (nextState) => setState(nextState);

  const place = (row) => {
    if (selected == null) return;
    refresh(game.current.placeHuman(selected, row));
    setSelected(null);
  };

  const nextHand = () => {
    refresh(game.current.startHand());
    setSelected(null);
  };

  return (
    <VStack gap={4}>
      <HStack gap={2} wrap="wrap" vAlign="center">
        <Badge label={`You ${current.scores[0] >= 0 ? "+" : ""}${current.scores[0]}`} variant="neutral" />
        <Badge label={`${current.names[1]} ${current.scores[1] >= 0 ? "+" : ""}${current.scores[1]}`} variant="neutral" />
        <Text type="supporting" color="secondary">
          Hand {current.handNumber} · street {hand.street + 1} · button {current.button === 0 ? "you" : current.names[1]}
        </Text>
      </HStack>
      <Grid columns={{ minWidth: 320, max: 2 }} gap={4}>
        <VStack gap={2}>
          <Heading level={3}>{current.names[1]}</Heading>
          <OfcBoard board={hand.boards[1]} interactive={false} onSlot={() => {}} />
        </VStack>
        <VStack gap={2}>
          <Heading level={3}>You</Heading>
          <OfcBoard board={hand.boards[0]} interactive={current.waitingFor === "human"} onSlot={place} />
        </VStack>
      </Grid>

      {hand.phase === "place" ? (
        <Card padding={5}>
          <VStack gap={3}>
            <HStack gap={2} vAlign="center" wrap="wrap">
              <Heading level={3}>Place {hand.hands[0].length} card{hand.hands[0].length === 1 ? "" : "s"}</Heading>
              <Text type="supporting" color="secondary">Pick a card, then click an empty slot. The last card is discarded automatically.</Text>
            </HStack>
            <HStack gap={2} wrap="wrap">
              {hand.hands[0].map((card, index) => (
                <PlayingCard key={`${card}-${index}`} card={card} selected={selected === index} onClick={() => setSelected(selected === index ? null : index)} />
              ))}
            </HStack>
          </VStack>
        </Card>
      ) : null}

      {hand.result ? (
        <Card padding={5}>
          <VStack gap={3}>
            <Heading level={2}>
              {hand.result.fouls[0] && hand.result.fouls[1] ? "Both boards foul" : hand.result.fouls[0] ? "Your board fouled" : hand.result.fouls[1] ? `${current.names[1]} fouled` : hand.result.points[0] > 0 ? "You win the hand" : hand.result.points[0] < 0 ? `${current.names[1]} wins the hand` : "Hand tied"}
            </Heading>
            <Text type="body" color="secondary">
              Points: {hand.result.points[0] >= 0 ? "+" : ""}{hand.result.points[0]} · royalties {hand.result.royalties[0]} vs {hand.result.royalties[1]}
            </Text>
            <VStack gap={1}>
              {ROWS.map(([row, label]) => (
                <HStack key={row} hAlign="between">
                  <Text type="body">{label}</Text>
                  <Text type="supporting" color="secondary">{hand.result.names[0][row]} · {hand.result.names[1][row]}</Text>
                </HStack>
              ))}
            </VStack>
            <HStack gap={2}>
              <Button label="Deal next hand" variant="primary" onClick={nextHand} />
              <Button label="New match" variant="secondary" onClick={() => { game.current = newOfcGame(); game.current.startHand(); refresh(game.current.publicState()); }} />
            </HStack>
          </VStack>
        </Card>
      ) : null}
      {hand.log?.length ? (
        <Card padding={4}>
          <details>
            <summary><Text type="body" weight="semibold">Placement log ({hand.log.length})</Text></summary>
            <VStack gap={1} paddingBlockStart={2}>
              {hand.log.slice(-30).map((line, index) => <Text key={index} type="supporting" color="secondary">{line}</Text>)}
            </VStack>
          </details>
        </Card>
      ) : null}
    </VStack>
  );
}
