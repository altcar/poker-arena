import React, { useEffect, useState } from "react";
import { VStack } from "@astryxdesign/core/VStack";
import { HStack } from "@astryxdesign/core/HStack";
import { Grid } from "@astryxdesign/core/Grid";
import { Card } from "@astryxdesign/core/Card";
import { Heading } from "@astryxdesign/core/Heading";
import { Text } from "@astryxdesign/core/Text";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Table, proportional } from "@astryxdesign/core/Table";
import { PageHeader, Stat, ErrorBanner, Loading } from "../components/Common.jsx";
import { api, formatWhen } from "../api.js";
import { go } from "../router.js";
import { useAuth } from "../auth.jsx";
import { Band } from "./marketing.jsx";

const KIND_LABEL = {
  signup_bonus: "Welcome bonus",
  rated_win: "Rated win",
  rated_loss: "Rated loss",
  subscription_credit: "Plan credit",
  reset: "Reset",
};

export function Paper() {
  const { me, ready, refresh } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setData(await api("/api/paper"));
    } catch (cause) {
      setError(cause);
    }
  }

  useEffect(() => {
    if (ready && me) load();
  }, [ready, me]);

  if (!ready) return <Band><Loading label="Loading your paper account…" /></Band>;
  if (!me) {
    return (
      <Band>
        <PageHeader eyebrow="Paper account" title="Log in to see your paper bankroll" />
        <HStack gap={2}>
          <Button label="Log in" variant="primary" onClick={() => go("#/login")} />
          <Button label="Create an account" variant="secondary" onClick={() => go("#/signup")} />
        </HStack>
      </Band>
    );
  }

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading your paper account…" /></Band>;

  const account = data.account;
  const pnl = account.balance - account.startingBalance;

  async function reset() {
    if (!window.confirm("Reset the paper bankroll to the starting balance?")) return;
    setBusy(true);
    try {
      setData(await api("/api/paper/reset", { method: "POST" }));
      await refresh();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Band>
      <PageHeader
        eyebrow="Paper account"
        title="A virtual bankroll for your bots"
        description="Every account starts with 10,000 paper credits. Rated wins add credits, rated losses subtract them. This is a scoreboard, not cash."
        actions={[
          <Button key="back" label="Account" variant="secondary" onClick={() => go("#/account")} />,
          <Button key="reset" label="Reset bankroll" variant="ghost" onClick={reset} isLoading={busy} />,
        ]}
      />
      <Grid columns={{ minWidth: 220, max: 4 }} gap={4}>
        <Stat label="Paper balance" value={account.balance.toLocaleString("en-US")} note={`${account.currency} credits`} tone="accent" />
        <Stat label="Starting balance" value={account.startingBalance.toLocaleString("en-US")} />
        <Stat label="Profit / loss" value={`${pnl >= 0 ? "+" : ""}${pnl.toLocaleString("en-US")}`} note={pnl >= 0 ? "Ahead of the start" : "Below the start"} />
        <Stat label="Plan" value={data.plan === "open" ? "Open" : data.plan === "pro" ? "Pro" : "Studio"} note="Plan credits arrive monthly." />
      </Grid>

      <Card padding={6}>
        <VStack gap={3}>
          <Heading level={2}>How the paper account works</Heading>
          <VStack gap={2}>
            {[
              "Signing up credits 10,000 paper credits.",
              "A rated challenge win adds 250 credits; a loss subtracts 100.",
              "Pro adds 25,000 credits each paid month; Studio adds 100,000.",
              "Resetting returns the balance to 10,000 and records the adjustment.",
              "Paper credits cannot be bought, sold, transferred or withdrawn.",
            ].map((line) => (
              <HStack key={line} gap={2} vAlign="start">
                <Text type="body" color="accent" weight="bold">→</Text>
                <Text type="body" color="secondary">{line}</Text>
              </HStack>
            ))}
          </VStack>
        </VStack>
      </Card>

      <Card padding={0}>
        <Table
          data={data.transactions.map((row) => ({ id: row.id, ...row }))}
          idKey="id"
          density="compact"
          dividers="rows"
          columns={[
            { key: "createdAt", header: "When", renderCell: (row) => formatWhen(row.createdAt) },
            { key: "kind", header: "Type", renderCell: (row) => <Badge label={KIND_LABEL[row.kind] || row.kind} variant="neutral" /> },
            { key: "note", header: "Note", width: proportional(2, { minWidth: 220 }) },
            { key: "amount", header: "Amount", align: "right", renderCell: (row) => `${row.amount >= 0 ? "+" : ""}${row.amount.toLocaleString("en-US")}` },
            { key: "balanceAfter", header: "Balance", align: "right", renderCell: (row) => row.balanceAfter.toLocaleString("en-US") },
          ]}
        />
      </Card>
    </Band>
  );
}
