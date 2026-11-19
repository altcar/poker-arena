import React, { useEffect, useState } from "react";
import { VStack } from "@astryxdesign/core/VStack";
import { HStack } from "@astryxdesign/core/HStack";
import { Grid } from "@astryxdesign/core/Grid";
import { Card } from "@astryxdesign/core/Card";
import { Heading } from "@astryxdesign/core/Heading";
import { Text } from "@astryxdesign/core/Text";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Link } from "@astryxdesign/core/Link";
import { Table, proportional } from "@astryxdesign/core/Table";
import { TextInput } from "@astryxdesign/core/TextInput";
import { TextArea } from "@astryxdesign/core/TextArea";
import { Selector } from "@astryxdesign/core/Selector";
import { FileInput } from "@astryxdesign/core/FileInput";
import { Divider } from "@astryxdesign/core/Divider";
import { PageHeader, ErrorBanner, Loading, Empty, Stat } from "../components/Common.jsx";
import { api, bb, ratingText, divisionVariant, formatDate } from "../api.js";
import { go } from "../router.js";
import { useAuth } from "../auth.jsx";
import { Band } from "./marketing.jsx";

const BOT_TEMPLATE = `from card_utils import Card
from player_action import PlayerAction

class Model:
    """A minimal, legal bot. Replace run() with your own logic."""

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id

    def run(self, actions, state):
        if state.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def end_game(self, actions, state):
        pass
`;

export function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("");

  async function submit() {
    setStatus("");
    try {
      await login(email, password);
      go("#/account");
    } catch (error) {
      setStatus(error.message);
    }
  }

  return (
    <Band>
      <VStack maxWidth={520} style={{ marginInline: "auto", width: "100%" }} gap={5}>
        <PageHeader eyebrow="Account" title="Log in" description="Your bots, forum posts and tournament entries live under one account." />
        <Card padding={6}>
          <VStack gap={4}>
            <TextInput label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
            <TextInput label="Password" type="password" value={password} onChange={setPassword} onEnter={submit} />
            <HStack gap={2} vAlign="center" wrap="wrap">
              <Button label="Log in" variant="primary" onClick={submit} />
              <Button label="Create an account" variant="secondary" onClick={() => go("#/signup")} />
            </HStack>
            {status ? <Text type="body" color="secondary">{status}</Text> : null}
            <Text type="supporting" color="secondary">
              Email is only used for login and the newsletter if you opt in. No password reset flow yet.
            </Text>
          </VStack>
        </Card>
      </VStack>
    </Band>
  );
}

export function Signup() {
  const { signup } = useAuth();
  const [email, setEmail] = useState("");
  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("");

  async function submit() {
    setStatus("");
    try {
      await signup(email, handle, password);
      go("#/account");
    } catch (error) {
      setStatus(error.message);
    }
  }

  return (
    <Band>
      <VStack maxWidth={520} style={{ marginInline: "auto", width: "100%" }} gap={5}>
        <PageHeader
          eyebrow="Account"
          title="Create a free account"
          description="One account for your bots, the forum and tournament entries. Open plan, no card."
        />
        <Card padding={6}>
          <VStack gap={4}>
            <TextInput label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
            <TextInput label="Handle" value={handle} onChange={setHandle} placeholder="lowercase, 3-24 characters" description="Shown on the forum and next to your bots." />
            <TextInput label="Password" type="password" value={password} onChange={setPassword} description="At least 8 characters." onEnter={submit} />
            <HStack gap={2} vAlign="center" wrap="wrap">
              <Button label="Sign up" variant="primary" onClick={submit} />
              <Button label="I already have an account" variant="secondary" onClick={() => go("#/login")} />
            </HStack>
            {status ? <Text type="body" color="secondary">{status}</Text> : null}
            <Text type="supporting" color="secondary">
              By signing up you agree to the <Link href="#/rules" isStandalone>platform rules</Link>.
            </Text>
          </VStack>
        </Card>
      </VStack>
    </Band>
  );
}

export function Account() {
  const { me, bots, ready, logout, refresh } = useAuth();

  useEffect(() => { refresh(); }, [refresh]);

  if (!ready) return <Band><Loading label="Loading your account…" /></Band>;
  if (!me) {
    return (
      <Band>
        <PageHeader eyebrow="Account" title="Log in to manage your bots" />
        <HStack gap={2} wrap="wrap">
          <Button label="Log in" variant="primary" onClick={() => go("#/login")} />
          <Button label="Create an account" variant="secondary" onClick={() => go("#/signup")} />
        </HStack>
      </Band>
    );
  }

  return (
    <Band>
      <PageHeader
        eyebrow="Account"
        title={`@${me.handle}`}
        description={me.email}
        actions={[
          <Button key="upload" label="Upload a bot" variant="primary" onClick={() => go("#/upload")} />,
          <Button key="logout" label="Log out" variant="secondary" onClick={async () => { await logout(); go("#/"); }} />,
        ]}
      />
      <Grid columns={{ minWidth: 200, max: 3 }} gap={4}>
        <Stat label="Bots" value={bots.length} note={`${bots.reduce((sum, bot) => sum + bot.matches, 0)} rated matches`} />
        <Stat label="Best rating" value={bots.length ? Math.max(...bots.map((bot) => bot.rating)) : "—"} />
        <Stat label="Division" value={bots[0]?.division || "—"} note="Uploaded bots start at 1200." />
      </Grid>

      {bots.length === 0 ? (
        <Empty
          title="No bots yet"
          description="Upload a model.py that defines class Model, then run a rated challenge."
          action={<Button label="Upload your first bot" variant="primary" onClick={() => go("#/upload")} />}
        />
      ) : (
        <Card padding={0}>
          <Table
            data={bots.map((bot) => ({ id: bot.id, ...bot }))}
            idKey="id"
            density="balanced"
            dividers="rows"
            columns={[
              {
                key: "name",
                header: "Bot",
                width: proportional(2, { minWidth: 180 }),
                renderCell: (bot) => (
                  <VStack gap={0}>
                    <Text type="body" weight="semibold">{bot.name}</Text>
                    <Text type="supporting" color="secondary">{bot.description || "No description"}</Text>
                  </VStack>
                ),
              },
              { key: "division", header: "Division", renderCell: (bot) => <Badge label={bot.division} variant={divisionVariant(bot.division)} /> },
              { key: "rating", header: "Rating", align: "right", renderCell: (bot) => ratingText(bot.rating) },
              { key: "wins", header: "W–D–L", align: "right", renderCell: (bot) => `${bot.wins}–${bot.draws}–${bot.losses}` },
              { key: "matches", header: "Matches", align: "right", renderCell: (bot) => bot.matches },
              {
                key: "actions",
                header: "",
                align: "right",
                renderCell: (bot) => (
                  <HStack gap={1} wrap="wrap">
                    <Button label="Rated test" size="sm" variant="secondary" onClick={() => go(`#/test/${bot.id}`)} />
                    <Button label="Play" size="sm" variant="ghost" onClick={() => go(`#/play?bot=${bot.id}&mode=hu`)} />
                    <Button
                      label="Delete"
                      size="sm"
                      variant="destructive"
                      onClick={async () => {
                        if (!window.confirm(`Delete ${bot.name}?`)) return;
                        await api(`/api/bots/${bot.id}`, { method: "DELETE" });
                        await refresh();
                      }}
                    />
                  </HStack>
                ),
              },
            ]}
          />
        </Card>
      )}
      <Text type="supporting" color="secondary">
        A rated test runs 20 hands in your browser against a house table and updates your Elo rating. Uploaded bots are public so anyone can challenge them.
      </Text>
    </Band>
  );
}

export function Upload() {
  const { me, refresh, ready } = useAuth();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [mode, setMode] = useState("nlh");
  const [source, setSource] = useState(BOT_TEMPLATE);
  const [sourceFile, setSourceFile] = useState(null);
  const [extraFiles, setExtraFiles] = useState([]);
  const [pickleFile, setPickleFile] = useState(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  if (!ready) return <Band><Loading label="Loading…" /></Band>;
  if (!me) {
    return (
      <Band>
        <PageHeader eyebrow="Upload" title="Upload a bot" description="You need an account first." />
        <Button label="Log in" variant="primary" onClick={() => go("#/login")} />
      </Band>
    );
  }

  async function readSource(file) {
    setSourceFile(file);
    if (file) setSource(await file.text());
  }

  async function submit() {
    setStatus("");
    setBusy(true);
    try {
      let pickle = null;
      let pklName = "";
      if (pickleFile) {
        if (pickleFile.size > 700000) throw new Error("pickle is larger than 700 KB");
        const bytes = new Uint8Array(await pickleFile.arrayBuffer());
        let binary = "";
        for (const byte of bytes) binary += String.fromCharCode(byte);
        pickle = btoa(binary);
        pklName = pickleFile.name;
      }
      const extras = [];
      for (const file of extraFiles) {
        if (!file.name.endsWith(".py")) continue;
        extras.push({ name: file.name, source: await file.text() });
      }
      await api("/api/bots", {
        method: "POST",
        body: JSON.stringify({ name, description, mode, source, extras, pickle, pklName }),
      });
      await refresh();
      go("#/account");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Band>
      <PageHeader
        eyebrow="Upload"
        title="Upload a bot"
        description="model.py plus any modules it imports. Nothing is executed on the server; the model runs in the visitor's browser."
        actions={[<Button key="account" label="Your account" variant="secondary" onClick={() => go("#/account")} />]}
      />
      <Grid columns={{ minWidth: 340, max: 2 }} gap={6}>
        <Card padding={6}>
          <VStack gap={4}>
            <TextInput label="Bot name" value={name} onChange={setName} placeholder="My first bot" maxLength={40} />
            <TextInput label="Description" value={description} onChange={setDescription} placeholder="One line about the strategy" maxLength={600} />
            <Selector
              label="Mode"
              options={[
                { value: "nlh", label: "No-Limit Hold'em" },
                { value: "hu", label: "Heads-up Hold'em" },
              ]}
              value={mode}
              onChange={setMode}
            />
            <FileInput label="model.py" value={sourceFile} onChange={readSource} accept=".py" />
            <FileInput label="Extra modules" value={extraFiles} onChange={(files) => setExtraFiles(files || [])} accept=".py" isMultiple maxFiles={6} />
            <FileInput label="Optional pickle" value={pickleFile} onChange={setPickleFile} accept=".pkl,.pickle,.bin" description="Up to 700 KB." />
            <TextArea label="model.py" value={source} onChange={setSource} rows={16} hasSpellCheck={false} />
            <HStack gap={2} vAlign="center" wrap="wrap">
              <Button label={busy ? "Uploading…" : "Upload bot"} variant="primary" onClick={submit} isLoading={busy} />
              <Button label="Cancel" variant="secondary" onClick={() => go("#/account")} />
            </HStack>
            {status ? <Text type="body" color="secondary">{status}</Text> : null}
          </VStack>
        </Card>
        <VStack gap={4}>
          <Card padding={6}>
            <VStack gap={3}>
              <Heading level={2}>The interface</Heading>
              <Text type="body" color="secondary">
                A bot is one Python class. The arena creates it once and calls seed, run and end_game. Return a
                PlayerAction: fold, call or raise_to. Raise amounts are the total to raise to.
              </Text>
              <Text type="supporting" color="secondary">
                Imports from card_utils, player_action and game_state are provided by the arena shim. Limits: 200 KB
                of Python, six extra modules, 700 KB pickle.
              </Text>
              <Link href="#/stack/your-first-bot" isStandalone>Read the longer walkthrough</Link>
            </VStack>
          </Card>
          <Card variant="muted" padding={5}>
            <VStack gap={2}>
              <Heading level={3}>Fair play</Heading>
              <Text type="body" color="secondary">
                Uploaded bots are public. Rated results are self-reported by the browser and accepted in good faith.
                Deliberately false results are cheating and will be reverted.
              </Text>
            </VStack>
          </Card>
        </VStack>
      </Grid>
    </Band>
  );
}

export function BotTest({ id }) {
  const [bot, setBot] = useState(null);
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api(`/api/bots/${encodeURIComponent(id)}`).then((data) => setBot(data.bot)).catch(setError);
  }, [id]);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    setProgress("Loading the Python runtime…");
    try {
      const { loadUserBot } = await import("../python_runtime.js");
      const { playHand, fullDeck } = await import("../../src/engine.js");
      const { createBot } = await import("../../src/bots.js");
      await loadUserBot(bot, setProgress);
      const key = `user:${bot.id}`;
      const opponents = ["conservative", "adaptive", "mixed", "aggressive"];
      const models = [createBot(key), ...opponents.map((name) => createBot(name))];
      const teams = [bot.name, "Conservative", "Adaptive", "Mixed", "Aggressive"];
      let net = 0;
      for (let hand = 0; hand < 20; hand += 1) {
        const deck = fullDeck();
        for (let i = deck.length - 1; i > 0; i -= 1) {
          const j = Math.floor(Math.random() * (i + 1));
          [deck[i], deck[j]] = [deck[j], deck[i]];
        }
        const outcome = playHand(models, deck, [500, 500, 500, 500, 500], hand % 5, 10, 20, teams);
        net += outcome.net[0];
        if (hand % 5 === 4) {
          setProgress(`Played ${hand + 1} of 20 hands · net ${net >= 0 ? "+" : ""}${net} chips`);
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }
      const outcome = net > 0 ? "win" : net < 0 ? "loss" : "draw";
      const data = await api(`/api/bots/${bot.id}/result`, {
        method: "POST",
        body: JSON.stringify({ opponent: "house", mode: "nlh", hands: 20, result: outcome, chips: net }),
      });
      setBot(data.bot);
      setResult({ outcome, net, change: data.change, rating: data.bot.rating });
      setProgress("");
    } catch (cause) {
      setError(cause);
      setProgress("");
    } finally {
      setBusy(false);
    }
  }

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!bot) return <Band><Loading label="Loading the bot…" /></Band>;

  return (
    <Band>
      <PageHeader
        eyebrow={`${bot.division} · ${Math.round(bot.rating)} rating · ${bot.wins}–${bot.draws}–${bot.losses}`}
        title={bot.name}
        description={bot.description || "An uploaded bot."}
        actions={[
          <Button key="play" label="Play against it" variant="secondary" onClick={() => go(`#/play?bot=${bot.id}&mode=hu`)} />,
          <Button key="account" label="Your account" variant="ghost" onClick={() => go("#/account")} />,
        ]}
      />
      <Card padding={6}>
        <VStack gap={4}>
          <Text type="body" color="secondary">
            A rated test loads the Python model in your browser and plays 20 hands against a four-bot house table.
            The result updates the rating. It is not a security boundary: this is a free arena.
          </Text>
          <HStack gap={2} wrap="wrap">
            <Button label="Run 20-hand rated test" variant="primary" onClick={run} isLoading={busy} />
          </HStack>
          {progress ? <Text type="body" color="secondary">{progress}</Text> : null}
          {result ? (
            <Card variant="muted" padding={5}>
              <VStack gap={2}>
                <Heading level={3}>
                  {result.outcome === "win" ? "Win" : result.outcome === "loss" ? "Loss" : "Draw"} · net{" "}
                  {result.net >= 0 ? "+" : ""}{result.net} chips
                </Heading>
                <Text type="body" color="secondary">
                  Rating {Math.round(result.rating)} ({result.change >= 0 ? "+" : ""}{result.change}).
                </Text>
              </VStack>
            </Card>
          ) : null}
        </VStack>
      </Card>
    </Band>
  );
}
