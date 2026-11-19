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
import { TabList, Tab } from "@astryxdesign/core/TabList";
import { TextInput } from "@astryxdesign/core/TextInput";
import { TextArea } from "@astryxdesign/core/TextArea";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Divider } from "@astryxdesign/core/Divider";
import { PageHeader, SectionHeading, ErrorBanner, Loading, Empty, Prose } from "../components/Common.jsx";
import { api, getPlatform, money, formatDate, formatWhen, divisionVariant } from "../api.js";
import { go } from "../router.js";
import { useAuth } from "../auth.jsx";
import { Band } from "./marketing.jsx";

export function Games() {
  const [platform, setPlatform] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { getPlatform().then(setPlatform).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!platform) return <Band><Loading label="Loading formats…" /></Band>;
  return (
    <>
      <Band>
        <PageHeader
          eyebrow="Games"
          title="Five formats. One ladder."
          description="Hold'em is the main event. The rest exist because a good bot should survive a change of rules."
        />
        <Grid columns={{ minWidth: 320, max: 3 }} gap={4}>
          {platform.games.map((game) => (
            <Card key={game.slug} padding={5} elevation="low" className="hover-card">
              <VStack gap={3}>
                <HStack gap={2} wrap="wrap">
                  {game.badges.map((badge) => <Badge key={badge} label={badge} variant="neutral" />)}
                </HStack>
                <Heading level={2}>{game.name}</Heading>
                <Text type="body" color="secondary">{game.summary}</Text>
                <HStack gap={2} wrap="wrap">
                  <Button label="Play" variant="primary" onClick={() => go(`#/play?mode=${game.mode}`)} />
                  <Button label="Rules" variant="secondary" onClick={() => go(`#/games/${game.slug}`)} />
                </HStack>
              </VStack>
            </Card>
          ))}
        </Grid>
      </Band>
      <Band variant="muted">
        <SectionHeading eyebrow="Format design" title="Why these five?" />
        <Grid columns={{ minWidth: 260, max: 3 }} gap={4}>
          <Card padding={5}><VStack gap={2}><Heading level={3}>Betting</Heading><Text type="body" color="secondary">No-limit, fixed-limit and no betting at all. A bot that only knows Hold'em gets exposed quickly.</Text></VStack></Card>
          <Card padding={5}><VStack gap={2}><Heading level={3}>Information</Heading><Text type="body" color="secondary">Private cards, shared boards and a fully open OFC board. Each format leaks information differently.</Text></VStack></Card>
          <Card padding={5}><VStack gap={2}><Heading level={3}>Scoring</Heading><Text type="body" color="secondary">bb/100 for Hold'em, points for OFC, chips for lowball. The leaderboard normalises the result.</Text></VStack></Card>
        </Grid>
      </Band>
    </>
  );
}

export function GameDetail({ slug }) {
  const [platform, setPlatform] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { getPlatform().then(setPlatform).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!platform) return <Band><Loading label="Loading rules…" /></Band>;
  const game = platform.games.find((item) => item.slug === slug);
  if (!game) return <Band><Empty title="No such game" description="Check the games index for the available formats." action={<Button label="All games" onClick={() => go("#/games")} />} /></Band>;
  return (
    <>
      <Band>
        <PageHeader
          eyebrow={game.badges.join(" · ")}
          title={game.name}
          description={game.tagline}
          actions={[
            <Button key="play" label="Play this format" variant="primary" onClick={() => go(`#/play?mode=${game.mode}`)} />,
            <Button key="games" label="All games" variant="secondary" onClick={() => go("#/games")} />,
          ]}
        />
        <Card padding={6}><Text type="large" color="secondary">{game.summary}</Text></Card>
        <VStack gap={3}>
          {game.rules.map(([title, body]) => (
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
        <SectionHeading eyebrow="Next" title="Try it against a house bot" description="No account needed. The five JavaScript bots start instantly; Python bots load in your browser." />
        <HStack gap={2} wrap="wrap">
          <Button label="Play now" variant="primary" onClick={() => go(`#/play?mode=${game.mode}`)} />
          <Button label="Create an account" variant="secondary" onClick={() => go("#/signup")} />
        </HStack>
      </Band>
    </>
  );
}

export function Stack() {
  const [platform, setPlatform] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { getPlatform().then(setPlatform).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!platform) return <Band><Loading label="Loading the Stack…" /></Band>;
  return (
    <Band>
      <PageHeader
        eyebrow="The Stack"
        title="Notes from the arena"
        description="Match formats, bot development and the strategy behind each game. Written for people who are going to ship a bot."
      />
      <Grid columns={{ minWidth: 300, max: 3 }} gap={4}>
        {platform.stack.map((article) => (
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
  );
}

export function Article({ slug }) {
  const [article, setArticle] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    api(`/api/stack/${encodeURIComponent(slug)}`).then((data) => setArticle(data.article)).catch(setError);
  }, [slug]);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!article) return <Band><Loading label="Loading the article…" /></Band>;
  return (
    <Band>
      <PageHeader
        eyebrow={`${article.tag} · ${formatDate(article.date)}`}
        title={article.title}
        description={article.excerpt}
        actions={[<Button key="back" label="All notes" variant="secondary" onClick={() => go("#/stack")} />]}
      />
      <Card padding={8}>
        <Markdown contentWidth={760}>{article.body}</Markdown>
      </Card>
    </Band>
  );
}

export function Newsletter() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("");
  const [tone, setTone] = useState("neutral");
  const [count, setCount] = useState(null);

  useEffect(() => {
    api("/api/newsletter").then((data) => setCount(data.subscribers)).catch(() => {});
  }, []);

  async function subscribe() {
    setStatus("");
    try {
      const data = await api("/api/newsletter", {
        method: "POST",
        body: JSON.stringify({ email, source: "newsletter-page" }),
      });
      setStatus(data.message);
      setTone("success");
      setEmail("");
      setCount((current) => (current || 0) + (data.already ? 0 : 1));
    } catch (error) {
      setStatus(error.message);
      setTone("error");
    }
  }

  return (
    <Band>
      <PageHeader
        eyebrow="Newsletter"
        title="One email when something actually happens"
        description="Season openings, tournament dates, new formats and the occasional bot-building note. No drip campaign."
      />
      <Grid columns={{ minWidth: 320, max: 2 }} gap={8}>
        <Card padding={6}>
          <VStack gap={4}>
            <TextInput
              label="Email"
              type="email"
              value={email}
              onChange={setEmail}
              placeholder="you@example.com"
              description={count == null ? undefined : `${count} people are on the list.`}
            />
            <HStack gap={2} vAlign="center" wrap="wrap">
              <Button label="Subscribe" variant="primary" onClick={subscribe} />
              {status ? (
                <Text type="body" color={tone === "error" ? "inherit" : "secondary"}>
                  {status}
                </Text>
              ) : null}
            </HStack>
          </VStack>
        </Card>
        <VStack gap={3}>
          <Heading level={2}>What lands in your inbox</Heading>
          <VStack gap={2}>
            {[
              "Season 2 starts 18 November 2026.",
              "Season 2 Kickoff opens entries in December.",
              "New: upload a Python bot and run rated 20-hand challenges.",
              "Prize pool and payout changes are announced before the event.",
            ].map((line) => (
              <HStack key={line} gap={2} vAlign="start">
                <Text type="body" color="accent" weight="bold">→</Text>
                <Text type="body" color="secondary">{line}</Text>
              </HStack>
            ))}
          </VStack>
          <Divider />
          <Heading level={3}>Archive</Heading>
          <VStack gap={1}>
            <Link href="#/stack/how-a-match-is-run" isStandalone>How a match is actually run</Link>
            <Link href="#/stack/your-first-bot" isStandalone>Your first bot: the Model interface</Link>
            <Link href="#/stack/ofc-fouls" isStandalone>Pineapple OFC: fouls are the whole game</Link>
            <Link href="#/stack/draw27-unlearning" isStandalone>2-7 Triple Draw: unlearning Hold'em</Link>
          </VStack>
        </VStack>
      </Grid>
    </Band>
  );
}

export function Live() {
  const [feed, setFeed] = useState(null);
  const [error, setError] = useState(null);
  const [updated, setUpdated] = useState(null);

  useEffect(() => {
    let alive = true;
    let timer = null;
    async function tick() {
      try {
        const data = await api("/api/live");
        if (!alive) return;
        setFeed(data);
        setUpdated(new Date(data.generatedAt));
      } catch (cause) {
        if (alive) setError(cause);
      }
    }
    tick();
    timer = setInterval(tick, 6000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  return (
    <Band>
      <PageHeader
        eyebrow="Live"
        title="Tables in progress"
        description="A rolling feed generated from the round-robin pairings. Refreshes every six seconds."
        actions={[
          <Badge key="live" label="Live" variant="success" />,
          updated ? <Text key="time" type="supporting" color="secondary">Updated {updated.toLocaleTimeString("en-GB")}</Text> : null,
        ]}
      />
      {!feed ? <Loading label="Connecting to the tables…" /> : (
        <Grid columns={{ minWidth: 300, max: 3 }} gap={4}>
          {feed.tables.map((table) => (
            <Card key={table.id} padding={5}>
              <VStack gap={3}>
                <HStack gap={2} vAlign="center" wrap="wrap">
                  <Badge label="Live" variant="success" />
                  <Badge label={table.mode === "hu" ? "Heads-up" : "6-max"} variant="neutral" />
                  <Text type="supporting" color="secondary">Hand {table.hand}</Text>
                </HStack>
                <VStack gap={2}>
                  {table.players.map((player) => (
                    <HStack key={player.name} hAlign="between" vAlign="center">
                      <Text type="body" weight="semibold">{player.name}</Text>
                      <Text type="body" hasTabularNumbers>{player.stack.toLocaleString("en-US")}</Text>
                    </HStack>
                  ))}
                </VStack>
                <Divider />
                <Text type="supporting" color="secondary">Board {table.board}</Text>
                <Text type="body">Pot {table.pot}</Text>
                <Text type="supporting" color="secondary">{table.last.join(" · ")}</Text>
              </VStack>
            </Card>
          ))}
        </Grid>
      )}
    </Band>
  );
}

export function Season({ id }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api("/api/seasons").then(setData).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the season…" /></Band>;
  const season = data.seasons.find((s) => s.id === String(id)) || data.seasons.find((s) => s.status === "active");
  if (!season) return <Band><Empty title="No such season" /></Band>;
  return (
    <>
      <Band>
        <PageHeader
          eyebrow={`${formatDate(season.starts)} – ${formatDate(season.ends)} · ${season.status}`}
          title={season.name}
          description={season.blurb}
          actions={[<Button key="ladder" label="Leaderboard" variant="primary" onClick={() => go("#/leaderboard")} />]}
        />
        {season.champion ? (
          <Card variant="muted" padding={5}>
            <HStack gap={2} vAlign="center">
              <Badge label="Champion" variant="success" />
              <Link href={`#/team/${encodeURIComponent(season.champion)}`} isStandalone>{season.champion}</Link>
            </HStack>
          </Card>
        ) : null}
        <Card padding={0}>
          <Table
            data={data.standings.map((row) => ({ id: `${row.place}-${row.name}`, ...row }))}
            idKey="id"
            density="compact"
            dividers="rows"
            columns={[
              { key: "place", header: "#", align: "right", width: proportional(0.4) },
              { key: "name", header: "Bot", width: proportional(2, { minWidth: 160 }) },
              { key: "kind", header: "Type", renderCell: (row) => <Badge label={row.kind === "house" ? "House" : "Uploaded"} variant="neutral" /> },
              { key: "points", header: "Points", align: "right", renderCell: (row) => row.points },
              { key: "rating", header: "Rating", align: "right", renderCell: (row) => row.rating },
              { key: "record", header: "Record" },
            ]}
          />
        </Card>
      </Band>
    </>
  );
}

export function Forum({ query }) {
  const [data, setData] = useState(null);
  const [category, setCategory] = useState(query.category || "all");
  const [error, setError] = useState(null);
  const { me } = useAuth();

  useEffect(() => { api("/api/forum/threads").then(setData).catch(setError); }, []);

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the forum…" /></Band>;

  const threads = data.threads.filter((thread) => category === "all" || thread.category === category);

  return (
    <Band>
      <PageHeader
        eyebrow="Forum"
        title="Bot development, tournaments and bugs"
        description="A place to ask what broke, share a strategy and argue about the right way to score a bot."
        actions={[<Button key="new" label="New thread" variant="primary" onClick={() => go("#/forum/new")} />]}
      />
      <TabList value={category} onChange={setCategory} hasDivider>
        <Tab value="all" label="All" />
        {data.categories.map((item) => <Tab key={item} value={item} label={item[0].toUpperCase() + item.slice(1)} />)}
      </TabList>
      {threads.length === 0 ? <Empty title="No threads yet" description="Start the first one." action={<Button label="New thread" onClick={() => go("#/forum/new")} />} /> : (
        <Card padding={0}>
          <Table
            data={threads.map((thread) => ({ id: thread.id, ...thread }))}
            idKey="id"
            density="balanced"
            dividers="rows"
            columns={[
              {
                key: "title",
                header: "Thread",
                width: proportional(3, { minWidth: 240 }),
                renderCell: (row) => (
                  <Link href={`#/forum/${row.id}`}>{row.pinned ? "📌 " : ""}{row.title}</Link>
                ),
              },
              { key: "category", header: "Category", renderCell: (row) => <Badge label={row.category} variant="neutral" /> },
              { key: "author", header: "Author", renderCell: (row) => `@${row.author}` },
              { key: "replies", header: "Replies", align: "right", renderCell: (row) => row.replies },
              { key: "updated_at", header: "Last post", renderCell: (row) => formatWhen(row.updated_at) },
            ]}
          />
        </Card>
      )}
      {!me ? <Text type="supporting" color="secondary">Log in to start a thread or reply.</Text> : null}
    </Band>
  );
}

export function Thread({ id }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [body, setBody] = useState("");
  const [status, setStatus] = useState("");
  const { me } = useAuth();

  async function load() {
    try {
      setData(await api(`/api/forum/threads/${encodeURIComponent(id)}`));
    } catch (cause) {
      setError(cause);
    }
  }

  useEffect(() => { load(); }, [id]);

  async function reply() {
    setStatus("");
    try {
      await api(`/api/forum/threads/${encodeURIComponent(id)}/posts`, {
        method: "POST",
        body: JSON.stringify({ body }),
      });
      setBody("");
      await load();
    } catch (cause) {
      setStatus(cause.message);
    }
  }

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the thread…" /></Band>;
  const thread = data.thread;

  return (
    <Band>
      <PageHeader
        eyebrow={`${thread.category} · started by @${thread.author} · ${formatWhen(thread.created_at)}`}
        title={thread.title}
        actions={[<Button key="back" label="Forum" variant="secondary" onClick={() => go("#/forum")} />]}
      />
      <Card padding={6}>
        <VStack gap={2}>
          <HStack gap={2} vAlign="center">
            <Text type="body" weight="semibold">@{thread.author}</Text>
            <Text type="supporting" color="secondary">{formatWhen(thread.created_at)}</Text>
          </HStack>
          <Markdown contentWidth={720}>{thread.body}</Markdown>
        </VStack>
      </Card>
      {data.posts.map((post) => (
        <Card key={post.id} padding={6}>
          <VStack gap={2}>
            <HStack gap={2} vAlign="center">
              <Text type="body" weight="semibold">@{post.author}</Text>
              {post.role === "admin" ? <Badge label="Admin" variant="purple" /> : null}
              <Text type="supporting" color="secondary">{formatWhen(post.created_at)}</Text>
            </HStack>
            <Markdown contentWidth={720}>{post.body}</Markdown>
          </VStack>
        </Card>
      ))}
      {thread.locked ? (
        <Card variant="muted" padding={4}><Text type="body" color="secondary">This thread is locked.</Text></Card>
      ) : me ? (
        <Card padding={5}>
          <VStack gap={3}>
            <TextArea label="Reply" value={body} onChange={setBody} rows={5} placeholder="Write a reply…" />
            <HStack gap={2} vAlign="center">
              <Button label="Post reply" variant="primary" onClick={reply} />
              {status ? <Text type="supporting" color="secondary">{status}</Text> : null}
            </HStack>
          </VStack>
        </Card>
      ) : (
        <Card variant="muted" padding={4}>
          <HStack gap={2} vAlign="center">
            <Text type="body" color="secondary">Log in to reply.</Text>
            <Button label="Log in" variant="primary" size="sm" onClick={() => go("#/login")} />
          </HStack>
        </Card>
      )}
    </Band>
  );
}

export function NewThread() {
  const { me } = useAuth();
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("general");
  const [body, setBody] = useState("");
  const [status, setStatus] = useState("");

  async function post() {
    setStatus("");
    try {
      const data = await api("/api/forum/threads", {
        method: "POST",
        body: JSON.stringify({ title, body, category }),
      });
      go(`#/forum/${data.id}`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  if (!me) {
    return (
      <Band>
        <PageHeader eyebrow="Forum" title="New thread" />
        <Card variant="muted" padding={6}>
          <VStack gap={3}>
            <Text type="body" color="secondary">Log in to start a thread.</Text>
            <Button label="Log in" variant="primary" onClick={() => go("#/login")} />
          </VStack>
        </Card>
      </Band>
    );
  }

  return (
    <Band>
      <PageHeader
        eyebrow="Forum"
        title="Start a thread"
        description="Markdown-lite: headings, bold, inline code and lists."
        actions={[<Button key="back" label="Back to forum" variant="secondary" onClick={() => go("#/forum")} />]}
      />
      <Card padding={6}>
        <VStack gap={4}>
          <TextInput label="Title" value={title} onChange={setTitle} placeholder="What is the thread about?" maxLength={120} />
          <TabList value={category} onChange={setCategory} hasDivider>
            {["general", "development", "tournaments", "bugs"].map((item) => (
              <Tab key={item} value={item} label={item[0].toUpperCase() + item.slice(1)} />
            ))}
          </TabList>
          <TextArea label="Post" value={body} onChange={setBody} rows={10} placeholder="Write the post…" />
          <HStack gap={2} vAlign="center">
            <Button label="Post thread" variant="primary" onClick={post} />
            {status ? <Text type="supporting" color="secondary">{status}</Text> : null}
          </HStack>
        </VStack>
      </Card>
    </Band>
  );
}
