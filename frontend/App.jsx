import React from "react";
import { AuthProvider } from "./auth.jsx";
import { PlayProvider } from "./play/context.jsx";
import Shell from "./components/Shell.jsx";
import { useRoute } from "./router.js";
import { Pricing, PrizePool, Business, Rules, About } from "./pages/marketing.jsx";
import Home from "./pages/home.jsx";
import { Games, GameDetail, Stack, Article, Newsletter, Live, Season, Forum, Thread, NewThread } from "./pages/content.jsx";
import { Leaderboard, Tournaments, TournamentDetail, Teams, Matrix, Matches, Team } from "./pages/data.jsx";
import { Login, Signup, Account, Upload, BotTest } from "./pages/account.jsx";
import { Paper } from "./pages/paper.jsx";
import Play from "./pages/play.jsx";

function Routes({ route }) {
  const [first, second] = route.path;
  switch (first) {
    case undefined:
    case "home":
      return <Home />;
    case "play":
      return <Play route={route} />;
    case "pricing":
      return <Pricing />;
    case "prize-pool":
      return <PrizePool />;
    case "business":
      return <Business />;
    case "games":
      return second ? <GameDetail slug={second} /> : <Games />;
    case "leaderboard":
      return <Leaderboard query={route.query} />;
    case "tournaments":
      return second ? <TournamentDetail id={second} /> : <Tournaments />;
    case "live":
      return <Live />;
    case "forum":
      if (second === "new") return <NewThread />;
      return second ? <Thread id={second} /> : <Forum query={route.query} />;
    case "stack":
      return second ? <Article slug={second} /> : <Stack />;
    case "newsletter":
      return <Newsletter />;
    case "rules":
      return <Rules />;
    case "about":
      return <About />;
    case "season":
      return <Season id={second || "2"} />;
    case "teams":
      return <Teams />;
    case "team":
      return <Team name={decodeURIComponent(second || "")} />;
    case "matrix":
      return <Matrix />;
    case "matches":
      return <Matches />;
    case "login":
      return <Login />;
    case "signup":
      return <Signup />;
    case "account":
      return <Account />;
    case "paper":
      return <Paper />;
    case "upload":
      return <Upload />;
    case "test":
      return <BotTest id={second} />;
    default:
      return <Home />;
  }
}

export default function App() {
  const route = useRoute();
  return (
    <AuthProvider>
      <PlayProvider>
        <Shell route={route}>
          <Routes route={route} />
        </Shell>
      </PlayProvider>
    </AuthProvider>
  );
}
