import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Game } from "../../src/game.js";
import { loadTeams, registerPythonBots, loadUserBot } from "../python_runtime.js";
import { api } from "../api.js";

const PLAYABLE_KEYS = ["conservative", "workhorse", "adaptive", "mixed", "aggressive"];
const PLAYABLE_NAMES = {
  conservative: "Conservative",
  workhorse: "Workhorse",
  adaptive: "Adaptive",
  mixed: "Mixed",
  aggressive: "Aggressive",
};
const PLAYABLE_SET = new Set(PLAYABLE_KEYS);
export const MAX_SELECTED = 6;

export const BOSSES = [
  { key: "aggressive", name: "The Bully", stack: 1500, smallBlind: 10, bigBlind: 20, style: "Aggressive" },
  { key: "mixed", name: "The Wall", stack: 2500, smallBlind: 15, bigBlind: 30, style: "Mixed" },
  { key: "adaptive", name: "The Machine", stack: 4000, smallBlind: 25, bigBlind: 50, style: "Adaptive" },
];

function readChosen() {
  try {
    const raw = JSON.parse(localStorage.getItem("arenaBots") || "null");
    if (Array.isArray(raw) && raw.length) return raw.filter((key) => typeof key === "string");
  } catch { /* ignore */ }
  return PLAYABLE_KEYS;
}

const PlayContext = createContext(null);

export function PlayProvider({ children }) {
  const [gameState, setGameState] = useState(null);
  const [sessionId, setSessionId] = useState(() => localStorage.getItem("arenaSession") || null);
  const [loading, setLoading] = useState("");
  const [mode, setMode] = useState("nlh6");
  const [bossRun, setBossRun] = useState(null);
  const [selectedBots, setSelectedBots] = useState(readChosen);
  const [humanPlaying, setHumanPlaying] = useState(true);
  const [error, setError] = useState(null);
  const localGame = useRef(null);
  const lastSetup = useRef(null);
  const challengeKey = useRef("");

  useEffect(() => {
    localStorage.setItem("arenaBots", JSON.stringify(selectedBots));
  }, [selectedBots]);

  const resume = useCallback(async () => {
    const id = localStorage.getItem("arenaSession");
    if (!id) return;
    try {
      const data = await api(`/api/session/${id}`);
      localGame.current = null;
      setSessionId(id);
      setGameState(data.state);
      setMode("nlh6");
    } catch {
      localStorage.removeItem("arenaSession");
      setSessionId(null);
    }
  }, []);

  useEffect(() => { resume(); }, [resume]);

  const startLocal = useCallback((name, keys, playing = true, options = {}) => {
    const game = new Game({
      humanName: name || "You",
      botKeys: keys,
      humanPlaying: playing,
      seed: Date.now() % 1000000,
      stack: options.stack || 500,
      smallBlind: options.smallBlind || 10,
      bigBlind: options.bigBlind || 20,
      stacks: options.stacks,
      autoRebuy: options.autoRebuy,
      playerNames: options.playerNames,
    });
    const { state } = game.startHand();
    localGame.current = game;
    setGameState(state);
    setSessionId(null);
    localStorage.removeItem("arenaSession");
    setLoading("");
    setError(null);
    setMode(options.mode || "hu");
    lastSetup.current = { name: name || "You", keys, playing, options, mode: options.mode || "hu" };
  }, []);

  const startSession = useCallback(async (name, keys, playing = true) => {
    setError(null);
    setLoading("");
    const clean = [...new Set(keys.filter(Boolean))].slice(0, MAX_SELECTED);
    const userKeys = clean.filter((key) => key.startsWith("user:"));
    const teamKeys = clean.filter((key) => !key.startsWith("user:"));
    const needsPython = userKeys.length > 0 || teamKeys.some((key) => !PLAYABLE_SET.has(key));

    if (!needsPython) {
      try {
        const data = await api("/api/session", {
          method: "POST",
          body: JSON.stringify({
            humanName: playing ? (name || "You") : "Spectator",
            botKeys: clean.length ? clean : PLAYABLE_KEYS,
            humanPlaying: playing,
          }),
        });
        localGame.current = null;
        setSessionId(data.sessionId);
        localStorage.setItem("arenaSession", data.sessionId);
        setGameState(data.state);
        setMode("nlh6");
        lastSetup.current = { name: name || "You", keys: clean, playing, mode: "nlh6" };
      } catch (cause) {
        setError(cause);
      }
      return;
    }

    try {
      setLoading("Loading the Python runtime (about 10 MB)…");
      const teamOnly = teamKeys.filter((key) => !PLAYABLE_SET.has(key));
      if (teamOnly.length) await loadTeams(teamOnly, setLoading);
      if (teamOnly.length) registerPythonBots(teamOnly, {});
      for (const key of userKeys) {
        const meta = (await api(`/api/bots/${encodeURIComponent(key.slice(5))}`)).bot;
        await loadUserBot(meta, setLoading);
      }
      startLocal(name || "You", clean, playing, { mode: "nlh6" });
    } catch (cause) {
      setLoading("");
      setError(cause);
    }
  }, [startLocal]);

  const startChallenge = useCallback(async ({ opponent, botId }) => {
    const key = botId ? `bot:${botId}` : `team:${opponent}`;
    if (challengeKey.current === key) return;
    challengeKey.current = key;
    setError(null);
    setLoading("Loading the opponent…");
    try {
      if (botId) {
        const meta = (await api(`/api/bots/${encodeURIComponent(botId)}`)).bot;
        await loadUserBot(meta, setLoading);
        startLocal("You", [`user:${meta.id}`], true, {
          mode: "hu",
          playerNames: ["You", meta.name],
          opponent: { botId },
        });
        return;
      }
      if (PLAYABLE_SET.has(opponent)) {
        startLocal("You", [opponent], true, { mode: "hu", opponent: { opponent } });
        return;
      }
      await loadTeams([opponent], setLoading);
      registerPythonBots([opponent], { [opponent]: opponent });
      startLocal("You", [opponent], true, {
        mode: "hu",
        playerNames: ["You", opponent],
        opponent: { opponent },
      });
    } catch (cause) {
      setLoading("");
      setError(cause);
    }
  }, [startLocal]);

  const startBossRun = useCallback((level = 0) => {
    const boss = BOSSES[level];
    setBossRun({ level, completed: false, over: false });
    startLocal("You", [boss.key], true, {
      mode: "boss",
      stack: 1000,
      smallBlind: boss.smallBlind,
      bigBlind: boss.bigBlind,
      stacks: [1000, boss.stack],
      autoRebuy: false,
      playerNames: ["You", boss.name],
      boss: { level },
    });
  }, [startLocal]);

  const act = useCallback(async (kind, amount = 0) => {
    if (localGame.current) {
      const { state } = localGame.current.act(kind, amount);
      setGameState(state);
      return;
    }
    if (!sessionId) return;
    try {
      const data = await api(`/api/session/${sessionId}/action`, {
        method: "POST",
        body: JSON.stringify({ kind, amount }),
      });
      setGameState(data.state);
    } catch (cause) {
      setError(cause);
    }
  }, [sessionId]);

  const next = useCallback(async () => {
    if (bossRun) {
      if (bossRun.completed) {
        startBossRun(0);
        return;
      }
      if (!localGame.current) return;
      const human = localGame.current.stacks[0];
      const boss = localGame.current.stacks[1];
      if (human <= 0) {
        startBossRun(0);
        return;
      }
      if (boss <= 0) {
        if (bossRun.level >= BOSSES.length - 1) {
          setBossRun({ ...bossRun, completed: true });
          setGameState(localGame.current.publicState());
          return;
        }
        startBossRun(bossRun.level + 1);
        return;
      }
    }
    if (localGame.current) {
      const { state } = localGame.current.startHand();
      setGameState(state);
      return;
    }
    if (!sessionId) return;
    try {
      const data = await api(`/api/session/${sessionId}/next`, { method: "POST" });
      setGameState(data.state);
    } catch (cause) {
      setError(cause);
    }
  }, [bossRun, sessionId, startBossRun]);

  const rebuy = useCallback(async () => {
    if (localGame.current) {
      localGame.current.rebuy();
      setGameState(localGame.current.publicState());
      return;
    }
    if (!sessionId) return;
    const data = await api(`/api/session/${sessionId}/rebuy`, { method: "POST" });
    setGameState(data.state);
  }, [sessionId]);

  const newSession = useCallback(() => {
    localGame.current = null;
    lastSetup.current = null;
    challengeKey.current = "";
    setSessionId(null);
    setGameState(null);
    setBossRun(null);
    setLoading("");
    setError(null);
    setMode("nlh6");
    localStorage.removeItem("arenaSession");
  }, []);

  const startFresh = useCallback(() => {
    if (bossRun) {
      startBossRun(0);
      return;
    }
    const setup = lastSetup.current;
    if (setup?.options?.opponent) {
      challengeKey.current = "";
      if (setup.options.opponent.botId) startChallenge({ botId: setup.options.opponent.botId });
      else startChallenge({ opponent: setup.options.opponent.opponent });
      return;
    }
    const setupMode = setup?.mode || mode;
    if (setupMode === "hu") {
      startSession("You", selectedBots.slice(0, 1), true);
      return;
    }
    startSession(setup?.name || "You", selectedBots, humanPlaying);
  }, [bossRun, humanPlaying, mode, selectedBots, startBossRun, startChallenge, startSession]);

  const chooseBots = useCallback((values) => {
    setSelectedBots((current) => {
      const nextValues = values.slice(0, MAX_SELECTED);
      if (nextValues.length === current.length && nextValues.every((value, index) => value === current[index])) return current;
      return nextValues;
    });
  }, []);

  const value = useMemo(() => ({
    gameState, sessionId, loading, mode, bossRun, selectedBots, humanPlaying, error,
    setHumanPlaying, chooseBots, startSession, startChallenge, startBossRun, startLocal,
    act, next, rebuy, newSession, startFresh, setMode, setError,
    PLAYABLE_KEYS, PLAYABLE_NAMES, PLAYABLE_SET,
  }), [gameState, sessionId, loading, mode, bossRun, selectedBots, humanPlaying, error, chooseBots, startSession, startChallenge, startBossRun, startLocal, act, next, rebuy, newSession, startFresh]);

  return <PlayContext.Provider value={value}>{children}</PlayContext.Provider>;
}

export function usePlay() {
  const value = useContext(PlayContext);
  if (!value) throw new Error("usePlay must be used inside PlayProvider");
  return value;
}
