import React, { useEffect, useState } from "react";
import { Grid } from "@astryxdesign/core/Grid";
import { VStack } from "@astryxdesign/core/VStack";
import { HStack } from "@astryxdesign/core/HStack";
import { Card } from "@astryxdesign/core/Card";
import { Heading } from "@astryxdesign/core/Heading";
import { Text } from "@astryxdesign/core/Text";
import { Button } from "@astryxdesign/core/Button";
import { Badge } from "@astryxdesign/core/Badge";
import { TextInput } from "@astryxdesign/core/TextInput";
import { usePlay, BOSSES } from "../play/context.jsx";
import { money } from "../api.js";

const SUITS = { H: "♥", S: "♠", D: "♦", C: "♣" };
const SUIT_RED = new Set(["H", "D"]);

export function PlayingCard({ card, hidden = false, small = false, selected = false, onClick }) {
  if (hidden || !card) {
    return <span className={`playing-card back${small ? " small" : ""}`} aria-label="face-down card" />;
  }
  const rank = card[0];
  const suit = card[1];
  const red = SUIT_RED.has(suit);
  return (
    <span
      className={`playing-card${red ? " red" : ""}${small ? " small" : ""}${selected ? " selected" : ""}${onClick ? " clickable" : ""}`}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (event) => { if (event.key === "Enter" || event.key === " ") onClick(); } : undefined}
    >
      <span className="card-rank">{rank}</span>
      <span className="card-suit">{SUITS[suit] || suit}</span>
    </span>
  );
}

function useIsMobile() {
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 720px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 720px)");
    const update = () => setMobile(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return mobile;
}

function seatPositions(seat, count) {
  if (count <= 1) return [50, 50];
  if (seat === 0) return [50, 88];
  if (count === 2) return [50, 12];
  const others = count - 1;
  const gap = Math.min(40, 180 / others);
  const span = 360 - 2 * gap;
  const t = others === 1 ? 0.5 : (seat - 1) / (others - 1);
  const theta = ((90 + gap + t * span) * Math.PI) / 180;
  const rx = 42;
  const ry = 36;
  return [50 + rx * Math.cos(theta), 50 + ry * Math.sin(theta)];
}

function seatName(state, index) {
  return state.players?.[index]?.name || `Seat ${index}`;
}

export default function PokerTable() {
  const { gameState, act, next, rebuy, newSession, bossRun, humanPlaying } = usePlay();
  const [raiseTo, setRaiseTo] = useState("");
  const isMobile = useIsMobile();
  const state = gameState;

  useEffect(() => {
    if (state?.hand?.minRaiseTo) setRaiseTo(String(state.hand.minRaiseTo));
  }, [state?.hand?.minRaiseTo, state?.hand?.street]);

  if (!state?.hand) {
    return (
      <Card padding={6}>
        <VStack gap={3}>
          <Heading level={2}>No hand yet</Heading>
          <HStack gap={2}>
            <Button label="Deal the first hand" variant="primary" onClick={next} />
            <Button label="New table" variant="secondary" onClick={newSession} />
          </HStack>
        </VStack>
      </Card>
    );
  }

  const hand = state.hand;
  const boss = bossRun ? BOSSES[bossRun.level] : null;
  const you = hand.seats?.[0];

  const raise = () => {
    const min = hand.minRaiseTo || 0;
    const max = hand.maxRaiseTo || 0;
    const value = Math.max(min, Math.min(max, Number(raiseTo) || min));
    setRaiseTo(String(value));
    act("raise", value);
  };

  const mobileSeat = (seat) => {
    const hole = seat.hole ? seat.hole.map((card) => <PlayingCard key={card} card={card} small />) : [<PlayingCard key="a" hidden small />, <PlayingCard key="b" hidden small />];
    return (
      <div key={seat.seat} className={`mobile-seat${seat.isHuman ? " you" : ""}${hand.toAct === seat.seat && !hand.result ? " turn" : ""}`}>
        <div className="seat-hole">{hole}</div>
        <Text type="body" weight="semibold" display="block">{seat.name}</Text>
        <Text type="supporting" color="secondary" display="block">
          {seat.stack.toLocaleString("en-US")}{seat.bet ? ` · bet ${seat.bet}` : seat.folded ? " · folded" : ""}
        </Text>
      </div>
    );
  };

  if (isMobile) {
    return (
      <VStack gap={3}>
        {boss ? (
          <Card variant="muted" padding={4}>
            <VStack gap={1}>
              <HStack gap={2} vAlign="center">
                <Badge label={`Round ${bossRun.level + 1} / 3`} variant="purple" />
                <Heading level={3}>{boss.name}</Heading>
              </HStack>
              <Text type="supporting" color="secondary">
                You {state.stacks[0].toLocaleString("en-US")} · {boss.name} {state.stacks[1].toLocaleString("en-US")} · blinds {boss.smallBlind}/{boss.bigBlind}
              </Text>
            </VStack>
          </Card>
        ) : null}
        <div className="mobile-table">
          <Grid columns={2} gap={2}>
            {hand.seats.filter((seat) => !seat.isHuman).map((seat) => mobileSeat(seat))}
          </Grid>
          <div className="mobile-center">
            <div className="pot-pill">Pot {hand.pot.toLocaleString("en-US")}</div>
            <div className="board-row">
              {hand.board.length
                ? hand.board.map((card) => <PlayingCard key={card} card={card} small />)
                : Array.from({ length: 5 }, (_, index) => <PlayingCard key={index} hidden small />)}
            </div>
            <Text type="supporting" color="secondary">Hand {state.handNumber} · {hand.street}</Text>
          </div>
          {hand.seats.filter((seat) => seat.isHuman).map((seat) => mobileSeat(seat))}
        </div>

        {hand.result ? (
          <Card padding={5}>
            <VStack gap={2}>
              <Heading level={2}>Hand {state.handNumber} result</Heading>
              {hand.seats.map((seat, index) => (
                <HStack key={seat.seat} hAlign="between">
                  <Text type="body">{seat.name}</Text>
                  <Text type="body" hasTabularNumbers>{hand.result.net[index] >= 0 ? "+" : ""}{hand.result.net[index]}</Text>
                </HStack>
              ))}
              <HStack gap={2} wrap="wrap">
                <Button label="Deal next hand" variant="primary" onClick={next} />
                <Button label="New table" variant="secondary" onClick={newSession} />
              </HStack>
            </VStack>
          </Card>
        ) : state.waitingFor === "human" ? (
          <Card padding={5}>
            <VStack gap={3}>
              <HStack gap={2} wrap="wrap">
                <Button label="Fold" variant="destructive" onClick={() => act("fold")} />
                <Button label={hand.callAmount === 0 ? "Check" : `Call ${hand.callAmount}`} variant="primary" onClick={() => act("call")} />
              </HStack>
              {hand.canRaise ? (
                <HStack gap={2} wrap="wrap" vAlign="center">
                  <TextInput label="Raise to" value={raiseTo} onChange={setRaiseTo} isLabelHidden />
                  <Button label="Raise" variant="secondary" onClick={raise} />
                  <Button label="Min" size="sm" variant="ghost" onClick={() => setRaiseTo(String(hand.minRaiseTo))} />
                  <Button label="Pot" size="sm" variant="ghost" onClick={() => setRaiseTo(String(hand.maxRaiseTo))} />
                </HStack>
              ) : null}
              <Text type="supporting" color="secondary">Your cards: {you?.hole?.join(" ") || "—"}</Text>
            </VStack>
          </Card>
        ) : (
          <Card variant="muted" padding={4}><Text type="body" color="secondary">Waiting for the bots…</Text></Card>
        )}
      </VStack>
    );
  }

  return (
    <VStack gap={4}>
      {boss ? (
        <Card variant="muted" padding={4} className="boss-banner">
          <HStack gap={4} hAlign="between" vAlign="center" wrap="wrap">
            <HStack gap={2} vAlign="center">
              <Badge label={`Round ${bossRun.level + 1} / 3`} variant="purple" />
              <Heading level={3}>{boss.name}</Heading>
              <Text type="supporting" color="secondary">{boss.style}</Text>
            </HStack>
            <HStack gap={4}>
              <Text type="body" hasTabularNumbers>You {state.stacks[0].toLocaleString("en-US")}</Text>
              <Text type="body" hasTabularNumbers>{boss.name} {state.stacks[1].toLocaleString("en-US")}</Text>
              <Text type="supporting" color="secondary">Blinds {boss.smallBlind}/{boss.bigBlind}</Text>
            </HStack>
          </HStack>
        </Card>
      ) : null}

      <div className="poker-table">
        <div className="table-center">
          <div className="pot-pill">Pot {hand.pot.toLocaleString("en-US")}</div>
          <div className="board-row">
            {hand.board.length
              ? hand.board.map((card) => <PlayingCard key={card} card={card} />)
              : Array.from({ length: 5 }, (_, index) => <PlayingCard key={index} hidden />)}
          </div>
          <Text type="supporting" color="secondary">
            Hand {state.handNumber} · {hand.street}
          </Text>
        </div>

        {hand.seats.map((seat) => {
          const [left, top] = seatPositions(seat.seat, hand.seats.length);
          const isTurn = hand.toAct === seat.seat && !hand.result;
          const classes = ["table-seat"];
          if (seat.isHuman) classes.push("you");
          if (isTurn) classes.push("turn");
          const hole = seat.hole ? seat.hole.map((card) => <PlayingCard key={card} card={card} small />) : [<PlayingCard key="a" hidden small />, <PlayingCard key="b" hidden small />];
          return (
            <div key={seat.seat} className={classes.join(" ")} style={{ left: `${left}%`, top: `${top}%` }}>
              <div className="seat-box">
                <div className="seat-hole">{hole}</div>
                <Text type="body" weight="semibold">{seat.name}</Text>
                <HStack gap={1} vAlign="center" justify="center">
                  <Text type="supporting" color="secondary">{seat.stack.toLocaleString("en-US")}</Text>
                  {hand.button === seat.seat ? <Badge label="D" variant="neutral" /> : null}
                </HStack>
                <Text type="supporting" color="secondary">
                  {seat.folded ? "folded" : seat.bet ? `bet ${seat.bet}` : seat.status === "all_in" ? "all-in" : "\u00a0"}
                </Text>
              </div>
            </div>
          );
        })}
      </div>

      {hand.result ? (
        <Card padding={5}>
          <VStack gap={3}>
            <HStack gap={2} vAlign="center" wrap="wrap">
              <Heading level={2}>Hand {state.handNumber} result</Heading>
              <Badge label={hand.result.net[0] >= 0 ? `+${hand.result.net[0]}` : `${hand.result.net[0]}`} variant={hand.result.net[0] >= 0 ? "success" : "neutral"} />
            </HStack>
            <VStack gap={1}>
              {hand.seats.map((seat, index) => (
                <HStack key={seat.seat} hAlign="between">
                  <Text type="body">{seat.name}</Text>
                  <HStack gap={3}>
                    <Text type="body" hasTabularNumbers>{hand.result.net[index] >= 0 ? "+" : ""}{hand.result.net[index]}</Text>
                    <Text type="supporting" color="secondary">{hand.result.hands[index].join(" ")}</Text>
                  </HStack>
                </HStack>
              ))}
            </VStack>
            <HStack gap={2} wrap="wrap">
              {bossRun ? (
                bossRun.completed ? (
                  <Button label="New run" variant="primary" onClick={() => next()} />
                ) : state.stacks[0] <= 0 ? (
                  <Button label="Restart the run" variant="primary" onClick={() => next()} />
                ) : state.stacks[1] <= 0 ? (
                  <Button label={bossRun.level >= 2 ? "Finish the run" : `Face ${BOSSES[bossRun.level + 1].name}`} variant="primary" onClick={next} />
                ) : (
                  <Button label="Deal next hand" variant="primary" onClick={next} />
                )
              ) : (
                <Button label="Deal next hand" variant="primary" onClick={next} />
              )}
              {!bossRun && state.humanBusted ? <Button label="Rebuy" variant="destructive" onClick={rebuy} /> : null}
              <Button label="New table" variant="secondary" onClick={newSession} />
            </HStack>
          </VStack>
        </Card>
      ) : state.waitingFor === "human" ? (
        <Card padding={5}>
          <VStack gap={3}>
            <HStack gap={2} wrap="wrap" vAlign="center">
              <Button label="Fold" variant="destructive" onClick={() => act("fold")} />
              <Button
                label={hand.callAmount === 0 ? "Check" : `Call ${hand.callAmount}`}
                variant="primary"
                onClick={() => act("call")}
              />
              {hand.canRaise ? (
                <>
                  <TextInput
                    label="Raise to"
                    value={raiseTo}
                    onChange={setRaiseTo}
                    isLabelHidden
                    placeholder="Raise to"
                  />
                  <Button label="Raise" variant="secondary" onClick={raise} />
                  <Button label="Min" size="sm" variant="ghost" onClick={() => setRaiseTo(String(hand.minRaiseTo))} />
                  <Button label="½ pot" size="sm" variant="ghost" onClick={() => setRaiseTo(String(Math.min(hand.maxRaiseTo, Math.max(hand.minRaiseTo, Math.floor(hand.pot * 0.5) + hand.callAmount))))} />
                  <Button label="Pot" size="sm" variant="ghost" onClick={() => setRaiseTo(String(Math.min(hand.maxRaiseTo, Math.max(hand.minRaiseTo, hand.pot + hand.callAmount))))} />
                  <Button label="All-in" size="sm" variant="ghost" onClick={() => setRaiseTo(String(hand.maxRaiseTo))} />
                </>
              ) : null}
            </HStack>
            <Text type="supporting" color="secondary">
              Your cards: {you?.hole?.join(" ") || "—"} · pot odds {hand.potOdds === 0 ? "—" : `${(hand.potOdds * 100).toFixed(1)}%`}
            </Text>
          </VStack>
        </Card>
      ) : (
        <Card variant="muted" padding={4}>
          <Text type="body" color="secondary">Waiting for the bots…</Text>
        </Card>
      )}

      {hand.actions?.length ? (
        <Card padding={4}>
          <details>
            <summary><Text type="body" weight="semibold">Action log ({hand.actions.length})</Text></summary>
            <VStack gap={1} paddingBlockStart={2}>
              {hand.actions.slice(-30).map((action, index) => {
                const name = hand.seats[action.player_id]?.name || `seat ${action.player_id}`;
                let line = `${name} ${action.kind}`;
                if (action.kind === "reveal_card") line = action.player_id === -1 ? `Dealer reveals ${action.card}` : `${name} shows ${action.card}`;
                else if (action.kind === "win") line = `${name} wins ${action.amount}`;
                else if (action.kind === "call") line = action.amount === 0 ? `${name} checks` : `${name} calls ${action.amount}`;
                else if (action.kind === "raise") line = `${name} raises to ${action.amount}`;
                else if (action.kind === "fold") line = `${name} folds`;
                return <Text key={index} type="supporting" color="secondary">{line}</Text>;
              })}
            </VStack>
          </details>
        </Card>
      ) : null}
      {!humanPlaying ? <Text type="supporting" color="secondary">Spectator mode. Start a new game to take a seat.</Text> : null}
    </VStack>
  );
}
