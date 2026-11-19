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
import { Table, proportional, pixel } from "@astryxdesign/core/Table";
import { TabList, Tab } from "@astryxdesign/core/TabList";
import { TextInput } from "@astryxdesign/core/TextInput";
import { Selector } from "@astryxdesign/core/Selector";
import { PageHeader, SectionHeading, Stat, ErrorBanner, Loading, Empty } from "../components/Common.jsx";
import { api, getPlatform, bb, money, number, formatDate, formatWhen, divisionVariant, ratingText } from "../api.js";
import { go } from "../router.js";
import { useAuth } from "../auth.jsx";
import { Band } from "./marketing.jsx";

export function Leaderboard({ query }) {
  const [data, setData] = useState(null);
  const [platform, setPlatform] = useState(null);
  const [division, setDivision] = useState(query.division || "all");
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api("/api/leaderboard"), getPlatform()])
      .then(([board, meta]) => {
        setData(board);
        setPlatform(meta);
      })
      .catch(setError);
  }, []);

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data || !platform) return <Band><Loading label="Loading the ladder…" /></Band>;

  const bots = data.bots.filter((bot) => division === "all" || bot.division === division);

  return (
    <Band>
      <PageHeader
        eyebrow="Leaderboard"
        title="House bots and uploaded bots, one ladder"
        description="Ratings move with results. Divisions are a coarse filter; the head-to-head matrix is the detailed picture."
        actions={[
          <Button key="season2" label="Season 2" variant="secondary" onClick={() => go("#/season/2")} />,
          <Button key="season1" label="Season 1 archive" variant="ghost" onClick={() => go("#/season/1")} />,
        ]}
      />
      <TabList value={division} onChange={setDivision} hasDivider>
        <Tab value="all" label="All" />
        {platform.divisions.map((item) => (
          <Tab key={item.key} value={item.key} label={`${item.name} (${data.grouped[item.key] || 0})`} />
        ))}
      </TabList>
      <Card padding={0}>
        <Table
          data={bots.map((bot) => ({ id: bot.id || bot.name, ...bot }))}
          idKey="id"
          density="compact"
          dividers="rows"
          hasHover
          columns={[
            { key: "place", header: "#", align: "right", width: proportional(0.4) },
            {
              key: "name",
              header: "Bot",
              width: proportional(2, { minWidth: 160 }),
              renderCell: (row) => (
                <VStack gap={0}>
                  {row.kind === "house" ? (
                    <Link href={`#/team/${encodeURIComponent(row.name)}`}>{row.name}</Link>
                  ) : (
                    <Text type="body" weight="semibold">{row.name}</Text>
                  )}
                  <Text type="supporting" color="secondary">
                    {row.kind === "house" ? "House bot" : `Uploaded by @${row.owner || "unknown"}`}
                  </Text>
                </VStack>
              ),
            },
            { key: "division", header: "Division", renderCell: (row) => <Badge label={row.division} variant={divisionVariant(row.division)} /> },
            { key: "rating", header: "Rating", align: "right", renderCell: (row) => ratingText(row.rating) },
            { key: "score", header: "bb/100", align: "right", renderCell: (row) => bb(row.score) },
            { key: "wins", header: "W–D–L", align: "right", renderCell: (row) => `${row.wins ?? 0}–${row.draws ?? 0}–${row.losses ?? 0}` },
            { key: "matches", header: "Matches", align: "right", renderCell: (row) => row.matches ?? row.samples ?? "—" },
            {
              key: "action",
              header: "",
              align: "right",
              renderCell: (row) => row.kind === "house"
                ? <Button label="Challenge" size="sm" variant="secondary" onClick={() => go(`#/play?mode=hu&opponent=${encodeURIComponent(row.name)}`)} />
                : <Button label="Test" size="sm" variant="secondary" onClick={() => go(`#/test/${row.id}`)} />,
            },
          ]}
        />
      </Card>
      <Text type="supporting" color="secondary">
        House ratings are derived from the 1,891-match round robin. Uploaded bots start at 1200 and move on an Elo curve after each rated 20-hand challenge.
      </Text>
    </Band>
  );
}

export function Tournaments() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api("/api/tournaments").then(setData).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading tournaments…" /></Band>;

  return (
    <Band>
      <PageHeader
        eyebrow="Tournaments"
        title="Scheduled events, brackets and open entries"
        description="Open events pay points. Pro events pay from the published pool. Every event seeds from the ladder."
        actions={[<Button key="pool" label="Prize pool" variant="secondary" onClick={() => go("#/prize-pool")} />]}
      />
      <Grid columns={{ minWidth: 320, max: 3 }} gap={4}>
        {data.tournaments.map((tournament) => (
          <Card key={tournament.id} padding={5} elevation="low">
            <VStack gap={3}>
              <HStack hAlign="between" vAlign="center" wrap="wrap">
                <Heading level={2}>{tournament.name}</Heading>
                <Badge label={tournament.status === "open" ? "Entries open" : "Completed"} variant={tournament.status === "open" ? "success" : "neutral"} />
              </HStack>
              <Text type="supporting" color="secondary">{tournament.format} · starts {formatDate(tournament.starts)}</Text>
              <Text type="body" color="secondary">{tournament.blurb}</Text>
              <VStack gap={1}>
                <Text type="supporting" color="secondary">Structure: {tournament.structure}</Text>
                <Text type="supporting" color="secondary">Prize: {tournament.prize}</Text>
                <Text type="supporting" color="secondary">Field: {tournament.entrants} bots{tournament.entries?.length ? ` + ${tournament.entries.length} uploaded` : ""}</Text>
              </VStack>
              {tournament.results?.length ? (
                <Text type="body">Winner: <Text type="body" weight="semibold">{tournament.results[0].team}</Text></Text>
              ) : null}
              <Button
                label={tournament.status === "open" ? "Enter" : "Results"}
                variant={tournament.status === "open" ? "primary" : "secondary"}
                onClick={() => go(`#/tournaments/${tournament.id}`)}
              />
            </VStack>
          </Card>
        ))}
      </Grid>
    </Band>
  );
}

export function TournamentDetail({ id }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [botId, setBotId] = useState(null);
  const [status, setStatus] = useState("");
  const { me, bots } = useAuth();

  async function load() {
    try {
      setData(await api(`/api/tournaments/${encodeURIComponent(id)}`));
    } catch (cause) {
      setError(cause);
    }
  }

  useEffect(() => { load(); }, [id]);

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the event…" /></Band>;
  const tournament = data.tournament;
  const entries = data.entries || [];

  async function enter() {
    setStatus("");
    try {
      await api(`/api/tournaments/${encodeURIComponent(tournament.id)}/enter`, {
        method: "POST",
        body: JSON.stringify({ botId: botId || bots[0]?.id }),
      });
      setStatus("Entered. Good luck.");
      await load();
    } catch (cause) {
      setStatus(cause.message);
    }
  }

  return (
    <Band>
      <PageHeader
        eyebrow={`${tournament.format} · ${formatDate(tournament.starts)}`}
        title={tournament.name}
        description={tournament.blurb}
        actions={[<Button key="all" label="All tournaments" variant="secondary" onClick={() => go("#/tournaments")} />]}
      />
      <Grid columns={{ minWidth: 260, max: 3 }} gap={4}>
        <Stat label="Status" value={tournament.status === "open" ? "Open" : "Completed"} />
        <Stat label="Prize" value={tournament.prize} />
        <Stat label="Field" value={`${tournament.entrants} house`} note={entries.length ? `+${entries.length} uploaded` : "No uploaded entries"} />
      </Grid>

      {tournament.results?.length ? (
        <Card padding={0}>
          <Table
            data={tournament.results.map((row) => ({ id: String(row.place), ...row }))}
            idKey="id"
            density="compact"
            dividers="rows"
            columns={[
              { key: "place", header: "#", align: "right", width: proportional(0.4) },
              { key: "team", header: "Bot", renderCell: (row) => <Link href={`#/team/${encodeURIComponent(row.team)}`}>{row.team}</Link> },
            ]}
          />
        </Card>
      ) : null}

      {tournament.status === "open" ? (
        <Card padding={6}>
          <VStack gap={4}>
            <Heading level={2}>Enter with your bot</Heading>
            {!me ? (
              <HStack gap={2} vAlign="center" wrap="wrap">
                <Text type="body" color="secondary">Log in to enter a bot.</Text>
                <Button label="Log in" variant="primary" onClick={() => go("#/login")} />
              </HStack>
            ) : bots.length === 0 ? (
              <HStack gap={2} vAlign="center" wrap="wrap">
                <Text type="body" color="secondary">You have not uploaded a bot yet.</Text>
                <Button label="Upload a bot" variant="primary" onClick={() => go("#/upload")} />
              </HStack>
            ) : (
              <>
                <Selector
                  label="Your bot"
                  options={bots.map((bot) => ({ value: bot.id, label: `${bot.name} (${Math.round(bot.rating)})` }))}
                  value={botId || bots[0]?.id}
                  onChange={setBotId}
                />
                <HStack gap={2} vAlign="center" wrap="wrap">
                  <Button label="Enter tournament" variant="primary" onClick={enter} />
                  {status ? <Text type="supporting" color="secondary">{status}</Text> : null}
                </HStack>
              </>
            )}
          </VStack>
        </Card>
      ) : null}

      {entries.length ? (
        <Card padding={0}>
          <Table
            data={entries.map((entry) => ({ id: entry.id, ...entry }))}
            idKey="id"
            density="compact"
            dividers="rows"
            columns={[
              { key: "handle", header: "Handle", renderCell: (row) => `@${row.handle || "unknown"}` },
              { key: "bot_name", header: "Bot", renderCell: (row) => row.bot_name || "—" },
              { key: "rating", header: "Rating", align: "right", renderCell: (row) => ratingText(row.rating) },
            ]}
          />
        </Card>
      ) : null}
    </Band>
  );
}

export function Teams() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    Promise.all([api("/api/tournament"), getPlatform()]).then(([tournament]) => setData(tournament)).catch(setError);
  }, []);

  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading teams…" /></Band>;

  const available = new Set(data.availableTeams || []);
  const repos = Object.fromEntries((data.teamsData || []).filter((row) => row.github_repo).map((row) => [row.team, row.github_repo]));
  const needle = filter.trim().toLowerCase();
  const teams = (data.allTeams?.teams || []).filter((team) => !needle || team.team.toLowerCase().includes(needle));

  return (
    <Band>
      <PageHeader
        eyebrow="Teams"
        title={`Every competition team (${(data.allTeams?.teams || []).length})`}
        description="Each team name links to the GitHub repository where its model was found. ✓ means the files are downloaded and playable in the browser."
      />
      <HStack gap={3} vAlign="center" wrap="wrap">
        <TextInput label="Filter" value={filter} onChange={setFilter} placeholder="Team name…" isLabelHidden />
        <Text type="supporting" color="secondary">{teams.length} shown · {available.size} playable</Text>
      </HStack>
      <Card padding={0}>
        <Table
          data={teams.map((team) => ({ id: team.team, ...team }))}
          idKey="id"
          density="compact"
          dividers="rows"
          columns={[
            { key: "team", header: "Team", width: proportional(2, { minWidth: 160 }), renderCell: (row) => repos[row.team] ? <Link href={repos[row.team]} isExternalLink>{row.team}</Link> : <Link href={`#/team/${encodeURIComponent(row.team)}`}>{row.team}</Link> },
            { key: "ok", header: "Playable", renderCell: (row) => available.has(row.team) ? <Badge label="Playable" variant="success" /> : <Badge label="Missing" variant="neutral" /> },
            { key: "score", header: "bb/100", align: "right", renderCell: (row) => bb(row.score) },
            { key: "ci95", header: "95% ±", align: "right", renderCell: (row) => row.ci95 == null ? "—" : row.ci95.toFixed(1) },
            { key: "wins", header: "W–D–L", align: "right", renderCell: (row) => `${row.wins ?? 0}–${row.draws ?? 0}–${row.losses ?? 0}` },
            { key: "repo", header: "Model", renderCell: (row) => repos[row.team] ? <Link href={repos[row.team]} isExternalLink>repo</Link> : "—" },
          ]}
        />
      </Card>
    </Band>
  );
}

export function Matrix() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api("/api/tournament").then(setData).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the matrix…" /></Band>;
  const ours = data.ourTournament;
  if (!ours?.teams?.length) return <Band><Empty title="No matrix yet" description="Run the round robin to fill this page." /></Band>;

  const names = ours.teams.map((team) => team.team);
  const columns = [
    { key: "team", header: "Team", width: pixel(150), renderCell: (row) => <Link href={`#/team/${encodeURIComponent(row.team)}`}>{row.team}</Link> },
    ...names.map((name) => ({
      key: name,
      header: name,
      width: pixel(72),
      align: "right",
      renderCell: (row) => {
        if (row.team === name) return <Text type="supporting" color="secondary">—</Text>;
        const cell = ours.matrix[row.team]?.[name];
        return cell ? <Text type="supporting" hasTabularNumbers>{bb(cell.mean_bb)}</Text> : "—";
      },
    })),
  ];

  return (
    <>
      <Band>
        <PageHeader
          eyebrow="Head-to-head"
          title="The full round robin"
          description={`All ${names.length} models whose files we have, ${ours.deals} deals each. The row team's bb/100 against the column team.`}
        />
        <Card padding={0}>
          <Table data={ours.teams.map((team) => ({ id: team.team, ...team }))} idKey="id" density="compact" columns={columns} />
        </Card>
      </Band>
      <Band variant="muted">
        <SectionHeading eyebrow="Standings" title="Season order" />
        <Card padding={0}>
          <Table
            data={ours.teams.map((team) => ({ id: team.team, ...team }))}
            idKey="id"
            density="compact"
            dividers="rows"
            columns={[
              { key: "rank", header: "#", align: "right", width: proportional(0.4) },
              { key: "team", header: "Team", renderCell: (row) => <Link href={`#/team/${encodeURIComponent(row.team)}`}>{row.team}</Link> },
              { key: "score", header: "bb/100", align: "right", renderCell: (row) => bb(row.score) },
              { key: "ci95", header: "95% ±", align: "right", renderCell: (row) => row.ci95 == null ? "—" : row.ci95.toFixed(1) },
              { key: "wins", header: "W–D–L", align: "right", renderCell: (row) => `${row.wins}–${row.draws}–${row.losses}` },
              { key: "samples", header: "Deals", align: "right" },
            ]}
          />
        </Card>
      </Band>
    </>
  );
}

export function Matches() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");
  useEffect(() => { api("/api/tournament").then(setData).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading matches…" /></Band>;
  const matches = (data.matches || []).slice().sort((a, b) => (b.finished_at || 0) - (a.finished_at || 0));
  const needle = filter.trim().toLowerCase();
  const filtered = needle ? matches.filter((m) => m.a.toLowerCase().includes(needle) || m.b.toLowerCase().includes(needle)) : matches;
  const shown = filtered.slice(0, 300);

  return (
    <Band>
      <PageHeader
        eyebrow="Matches"
        title={`${number(matches.length)} round-robin matches`}
        description="Every pairing, ten duplicate deals, seats swapped. Filter by team name."
        actions={[<Link key="json" href="/data/our-tournament.json" download="our-tournament.json" isStandalone>Download JSON</Link>]}
      />
      <HStack gap={3} vAlign="center" wrap="wrap">
        <TextInput label="Filter" value={filter} onChange={setFilter} placeholder="Team name…" isLabelHidden />
        <Text type="supporting" color="secondary">{filtered.length} matches{filtered.length > shown.length ? ` · showing first ${shown.length}` : ""}</Text>
      </HStack>
      <Card padding={0}>
        <Table
          data={shown.map((match, index) => ({ id: `${match.a}-${match.b}-${index}`, ...match }))}
          idKey="id"
          density="compact"
          dividers="rows"
          columns={[
            { key: "a", header: "Match", width: proportional(2, { minWidth: 220 }), renderCell: (row) => `${row.a} vs ${row.b}` },
            { key: "verdict", header: "Result", renderCell: (row) => row.verdict === "tie" ? "Tie" : row.verdict === "a" ? `${row.a} won` : `${row.b} won` },
            { key: "mean_bb", header: "bb/100", align: "right", renderCell: (row) => bb(row.mean_bb) },
            { key: "ci95", header: "95% ±", align: "right", renderCell: (row) => row.ci95_bb == null ? "—" : row.ci95_bb.toFixed(1) },
            { key: "hands", header: "Hands", align: "right" },
            { key: "faults", header: "Faults", align: "right", renderCell: (row) => `${row.faults_a || 0}/${row.faults_b || 0}` },
            { key: "finished_at", header: "When", renderCell: (row) => formatWhen(row.finished_at) },
          ]}
        />
      </Card>
    </Band>
  );
}

export function Team({ name }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api("/api/tournament").then(setData).catch(setError); }, []);
  if (error) return <Band><ErrorBanner error={error} /></Band>;
  if (!data) return <Band><Loading label="Loading the team…" /></Band>;
  const key = String(name || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const repo = (data.teamsData || []).find((row) => row.team === name)?.github_repo;
  const team = (data.allTeams?.teams || []).find((row) => row.team === name || row.team.toLowerCase().replace(/[^a-z0-9]/g, "") === key);
  const row = (data.ourTournament?.teams || []).find((item) => item.team === name);
  if (!team) return <Band><Empty title="Team not found" description="This team may not have published a discoverable repository." action={<Button label="All teams" onClick={() => go("#/teams")} />} /></Band>;

  return (
    <Band>
      <PageHeader
        eyebrow="Team"
        title={team.team}
        description="A competition entry recovered from the public forks of Manchester-AI-Society/poker_bot."
        actions={repo ? [<Link key="repo" href={repo} isExternalLink isStandalone>Open the repository</Link>] : null}
      />
      <Grid columns={{ minWidth: 220, max: 4 }} gap={4}>
        <Stat label="Rank" value={row?.rank ?? "—"} />
        <Stat label="bb/100" value={bb(row?.score)} />
        <Stat label="95% ±" value={row?.ci95 == null ? "—" : row.ci95.toFixed(1)} />
        <Stat label="Record" value={row ? `${row.wins}–${row.draws}–${row.losses}` : "—"} />
      </Grid>
      <Card padding={6}>
        <VStack gap={2}>
          <Heading level={2}>Model</Heading>
          <Text type="body" color="secondary">
            {repo ? "The submitted model and pickle were found in the repository below." : "No public repository was found for this team."}
          </Text>
          {repo ? <Link href={repo} isExternalLink isStandalone>{repo.replace(/^https:\/\/github\.com\//, "")}</Link> : null}
          <Text type="supporting" color="secondary">Availability: {data.availableTeams?.includes(team.team) ? "playable in the browser" : "files not recovered"}</Text>
        </VStack>
      </Card>
      {row ? (
        <Card padding={0}>
          <Table
            data={[{ id: "row", ...row }]}
            idKey="id"
            density="compact"
            columns={[
              { key: "samples", header: "Deals", align: "right" },
              { key: "opponents_played", header: "Opponents", align: "right" },
              { key: "faults", header: "Faults", align: "right" },
            ]}
          />
        </Card>
      ) : null}
    </Band>
  );
}
