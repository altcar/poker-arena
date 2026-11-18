import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PORT = Number(process.env.PORT || (9400 + Math.floor(Math.random() * 400)));
const BASE = "https://poker-arena.hheylau.workers.dev";
const profile = join(tmpdir(), `poker-uploaded-${Date.now()}`);
const bots = await (await fetch(`${BASE}/api/bots`)).json();
const bot = bots.bots.find((b) => b.name === "PocketRockets");
const edge = spawn(EDGE, ["--headless=new","--disable-gpu","--no-sandbox",`--remote-debugging-port=${PORT}`,`--user-data-dir=${profile}`,`${BASE}/#/teams`], { stdio: "ignore" });
process.on("exit", () => { try { spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" }); } catch { /* ignore */ } });
let target;
for (let i=0;i<60;i++){try{const l=await(await fetch(`http://127.0.0.1:${PORT}/json`)).json();target=l.find(x=>x.type==="page"&&x.url.includes("poker-arena"));if(target)break;}catch{}await sleep(500);}
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener("open",r,{once:true}));
let id=1;function ev(e){const i=id++;return new Promise(res=>{const hnd=(m)=>{const x=JSON.parse(m.data);if(x.id!==i)return;ws.removeEventListener("message",hnd);res(x.result?.result?.value)};ws.addEventListener("message",hnd);ws.send(JSON.stringify({id:i,method:"Runtime.evaluate",params:{expression:e,returnByValue:true,awaitPromise:true}}))})}
async function waitFor(text,seconds=30){for(let i=0;i<seconds*2;i++){const t=await ev("document.body.textContent");if(typeof t==="string"&&t.includes(text))return true;await sleep(500)}return false}
async function click(text){return ev(`(() => { const b=[...document.querySelectorAll("button")].find((n)=>n.textContent.trim().includes(${JSON.stringify(text)})); if(!b)return false; b.click(); return true; })()`)}
async function check(name,ok,detail=""){console.log(`${ok?"PASS":"FAIL"} ${name}${detail?` — ${detail}`:""}`);if(!ok)process.exitCode=1}

await waitFor("All competition bots",40);
check("uploaded section", await waitFor("Uploaded bots",20));
await ev(`localStorage.setItem("pokerArenaBots", JSON.stringify(["user:${bot.id}", "conservative"])); location.hash="#/play"`);
check("setup lists uploaded", await waitFor("PocketRockets",20));
await click("Yes, start the game");
check("table with uploaded bot", await waitFor("PocketRockets",120));
check("3 seats", (await ev("document.body.textContent")).includes("3 seats"));
check("action bar", await click("Check") || await click("Call ") || await click("Fold"));

ws.close();
spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" });
